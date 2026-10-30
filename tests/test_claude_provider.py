"""Seam 3: Claude as a language provider through `claude -p`, tested against a fake `claude` executable."""
from __future__ import annotations

import base64
import json
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from ocrmaker.claude import ClaudeProvider
from ocrmaker.errors import StepInterrupted
from ocrmaker.language import (MISSING_CLI, UNAUTHORIZED, UNREACHABLE_SERVER, USAGE_LIMIT, LanguageError,
                               Request)

FAKE = r'''#!{python}
import json, os, sys, time
args = sys.argv[1:]
log = os.environ["FAKE_CLAUDE_LOG"]
mode = os.environ.get("FAKE_CLAUDE_MODE", "answer")
if args[:2] == ["auth", "status"]:
    print(os.environ.get("FAKE_CLAUDE_AUTH", '{{"loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "pro"}}'))
    sys.exit(0)
stdin = sys.stdin.read()
system = open(args[args.index("--system-prompt-file") + 1], encoding="utf-8").read() if "--system-prompt-file" in args else None
with open(log, "a") as f:
    f.write(json.dumps({{"args": args, "stdin": stdin, "system": system, "cwd": os.getcwd(), "env": dict(os.environ)}}) + "\n")
if mode == "freeze":
    open(log + ".frozen", "w").write("1")
    time.sleep(60)
usage = {{"input_tokens": 12, "cache_creation_input_tokens": 30, "cache_read_input_tokens": 900, "output_tokens": 25,
          "server_tool_use": {{"web_search_requests": 2}}}}
base = {{"type": "result", "session_id": "s", "usage": usage, "duration_ms": 5, "num_turns": 1,
         "modelUsage": {{"claude-sonnet-5-1": {{"inputTokens": 12}}}}}}
ok = {{**base, "subtype": "success", "is_error": False, "result": "plain text answer"}}
if mode == "answer":
    out = {{**ok, "structured_output": {{"words": ["Rychvald"]}}}}
elif mode == "text":
    out = ok
elif mode == "nousage":
    out = {{"type": "result", "subtype": "success", "is_error": False, "result": "plain text answer"}}
elif mode == "login":
    out = {{**base, "subtype": "success", "is_error": True, "result": "Invalid API key · Please run /login"}}
elif mode == "limit":
    out = {{**base, "subtype": "success", "is_error": True, "result": "Claude AI usage limit reached|4102444800"}}
elif mode == "limit_unknown":
    out = {{**base, "subtype": "success", "is_error": True, "result": "You've hit your limit · resets 3pm"}}
elif mode == "offline":
    out = {{**base, "subtype": "success", "is_error": True,
            "result": "API Error: Can't reach the API server — check your internet or DNS (EAI_AGAIN)"}}
else:
    sys.stderr.write("boom")
    sys.exit(2)
print(json.dumps({{"type": "system", "subtype": "init", "model": "claude-sonnet-5-1"}}))
print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "..."}}]}}}}))
print(json.dumps(out))
sys.exit(1 if out.get("is_error") else 0)
'''

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    path.write_text(FAKE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("OCRMAKER_CLAUDE", str(path))
    for variable in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.setenv(variable, "must-not-reach-claude")

    class Fake:
        frozen = tmp_path / "calls.jsonl.frozen"

        def calls(self):
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

        def mode(self, value):
            monkeypatch.setenv("FAKE_CLAUDE_MODE", value)

        def auth(self, value: dict):
            monkeypatch.setenv("FAKE_CLAUDE_AUTH", json.dumps(value))
    return Fake()


SCHEMA = {"type": "object", "properties": {"words": {"type": "array", "items": {"type": "string"}}},
          "required": ["words"]}


def ask(fake, provider=None, **options):
    request = Request(instructions=options.pop("instructions", "Read the marked words."),
                      data=options.pop("data", {"page": 1, "spots": [{"id": "w7", "partial": "Rychv…"}]}),
                      schema=options.pop("schema", SCHEMA), **options)
    return (provider or ClaudeProvider()).complete(request, model=options.pop("model", "sonnet"),
                                                   stopped=lambda: False)


def test_request_runs_on_the_subscription_isolated_and_without_api_keys(fake):
    ask(fake)
    call = fake.calls()[0]
    for variable in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        assert variable not in call["env"]
    assert call["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    args = call["args"]
    assert args[0] == "-p"
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert {"--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"} <= set(args)
    assert "--bare" not in args  # it would require an API key
    # A scratch directory of its own: nothing in the user's working tree can leak into the request.
    assert Path(call["cwd"]).name.startswith("ocrmaker-claude-") and not Path(call["cwd"]).exists()


def test_shared_instructions_go_to_the_cached_system_prompt_and_data_only_to_stdin(fake):
    ask(fake, instructions="Read the marked words.", data={"spots": [{"id": "w7", "partial": "Rychv…"}]})
    call = fake.calls()[0]
    assert call["system"] == "Read the marked words."
    assert "Rychv…" not in json.dumps(call["args"], ensure_ascii=False)
    message = json.loads(call["stdin"].splitlines()[0])
    assert message["type"] == "user"
    text = [block for block in message["message"]["content"] if block["type"] == "text"][0]["text"]
    assert '"partial":"Rychv…"' in text


def test_images_are_sent_as_base64_blocks_before_the_text(fake, tmp_path):
    crop = tmp_path / "crop.png"
    crop.write_bytes(PNG)
    ask(fake, images=(crop,))
    call = fake.calls()[0]
    args = call["args"]
    assert args[args.index("--input-format") + 1] == "stream-json"
    content = json.loads(call["stdin"].splitlines()[0])["message"]["content"]
    assert [block["type"] for block in content] == ["image", "text"]
    assert content[0]["source"] == {"type": "base64", "media_type": "image/png",
                                    "data": base64.b64encode(PNG).decode()}


def test_web_search_is_the_only_tool_and_only_when_asked(fake):
    ask(fake)
    ask(fake, web_search=True)
    without, with_search = (call["args"] for call in fake.calls())
    assert without[without.index("--tools") + 1] == ""
    assert "--allowedTools" not in without
    assert with_search[with_search.index("--tools") + 1] == "WebSearch"
    assert with_search[with_search.index("--allowedTools") + 1] == "WebSearch"


def test_the_answer_schema_goes_through_the_structured_output_channel(fake):
    completion = ask(fake)
    args = fake.calls()[0]["args"]
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    assert json.loads(completion.text) == {"words": ["Rychvald"]}


def test_a_request_without_schema_returns_the_plain_text(fake):
    fake.mode("text")
    completion = ask(fake, schema=None)
    assert "--json-schema" not in fake.calls()[0]["args"]
    assert completion.text == "plain text answer"


def test_reported_tokens_are_parsed_with_cache_reads_as_part_of_the_input(fake):
    completion = ask(fake)
    assert completion.usage == {"input": 942, "cached_input": 900, "output": 25, "reasoning": None}
    assert completion.model == "claude-sonnet-5-1"
    assert completion.details["web_search_requests"] == 2


def test_unknown_usage_stays_unknown_never_zero(fake):
    fake.mode("nousage")
    assert ask(fake).usage is None


def test_a_missing_cli_gives_a_czech_message(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("OCRMAKER_CLAUDE", str(tmp_path / "nowhere" / "claude"))
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == MISSING_CLI
    assert "není nainstalovaný" in str(caught.value)


def test_a_failed_login_asks_for_the_subscription_login(fake):
    fake.mode("login")
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == UNAUTHORIZED
    assert "/login" in str(caught.value) and "přihlášený" in str(caught.value)


def test_a_logged_out_cli_is_refused_before_any_request(fake):
    fake.auth({"loggedIn": False})
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == UNAUTHORIZED
    assert fake.calls() == []


def test_an_api_key_login_is_refused_because_only_the_subscription_may_pay(fake):
    fake.auth({"loggedIn": True, "authMethod": "api_key"})
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == UNAUTHORIZED
    assert "API klíč" in str(caught.value)
    assert fake.calls() == []


def test_a_usage_limit_carries_the_reset_time(fake):
    fake.mode("limit")
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == USAGE_LIMIT
    assert caught.value.retry_at == 4102444800
    assert "Limit předplatného Claude je vyčerpaný" in str(caught.value)


def test_a_usage_limit_without_reset_time_still_counts_as_the_limit(fake):
    fake.mode("limit_unknown")
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == USAGE_LIMIT
    assert caught.value.usage is not None  # the failed request still reports what it used


def test_an_unreachable_server_is_told_apart(fake):
    fake.mode("offline")
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == UNREACHABLE_SERVER
    assert "nedostupný" in str(caught.value)


def test_a_crash_reports_the_cli_output(fake):
    fake.mode("crash")
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert "boom" in str(caught.value)


def test_stopping_kills_the_running_process(fake):
    fake.mode("freeze")
    stop = threading.Event()
    failure = []

    def run():
        try:
            ClaudeProvider().complete(Request(instructions="x", data={}), model="sonnet",
                                      stopped=stop.is_set)
        except Exception as exc:  # noqa: BLE001
            failure.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 10
    while not fake.frozen.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    started = time.monotonic()
    stop.set()
    worker.join(10)
    assert not worker.is_alive() and time.monotonic() - started < 5
    assert isinstance(failure[0], StepInterrupted)
