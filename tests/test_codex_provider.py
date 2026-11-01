"""Seam 3: Codex as a language provider through `codex exec`, tested against a fake `codex` executable."""
from __future__ import annotations

import json
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from ocrmaker.codex import CodexProvider
from ocrmaker.errors import StepInterrupted
from ocrmaker.language import MISSING_CLI, UNAUTHORIZED, UNREACHABLE_SERVER, USAGE_LIMIT, LanguageError, Request

FAKE = r'''#!{python}
import json, os, sys, time
args = sys.argv[1:]
log = os.environ["FAKE_CODEX_LOG"]
mode = os.environ.get("FAKE_CODEX_MODE", "answer")
if args[:2] == ["login", "status"]:
    sys.stderr.write(os.environ.get("FAKE_CODEX_LOGIN", "Logged in using ChatGPT") + "\n")
    sys.exit(int(os.environ.get("FAKE_CODEX_LOGIN_EXIT", "0")))
stdin = sys.stdin.read()
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
schema = value("--output-schema")
images = [a.split("=", 1)[1] for a in args if a.startswith("--image=")]
with open(log, "a") as f:
    f.write(json.dumps({{"args": args, "stdin": stdin, "cwd": os.getcwd(), "env": dict(os.environ),
                        "schema": json.load(open(schema)) if schema else None,
                        "images": [open(i, "rb").read().hex() for i in images]}}) + "\n")
if mode == "freeze":
    open(log + ".frozen", "w").write("1")
    time.sleep(60)
def emit(event):
    print(json.dumps(event), flush=True)
usage = {{"input_tokens": 14000, "cached_input_tokens": 9000, "cache_write_input_tokens": 0, "output_tokens": 40,
          "reasoning_output_tokens": 15}}
emit({{"type": "thread.started", "thread_id": "t1"}})
emit({{"type": "turn.started"}})
failures = {{"limit": "You've hit your usage limit. Upgrade to Pro or try again at 3:05 PM.",
             "login": "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header",
             "offline": "stream error: error sending request for url (https://chatgpt.com/backend-api/codex/responses): dns error: failed to lookup address information"}}
if mode in failures:
    emit({{"type": "error", "message": failures[mode]}})
    emit({{"type": "turn.failed", "error": {{"message": failures[mode]}}}})
    sys.exit(1)
if mode == "crash":
    sys.stderr.write("boom")
    sys.exit(2)
answer = os.environ.get("FAKE_CODEX_ANSWER", '{{"words":["Rychvald"]}}')
emit({{"type": "item.completed", "item": {{"id": "item_0", "type": "agent_message", "text": answer}}}})
if value("-o"):
    open(value("-o"), "w").write(answer)
emit({{"type": "turn.completed", **({{}} if mode == "nousage" else {{"usage": usage}})}})
'''

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000")
SCHEMA = {"type": "object", "properties": {"words": {"type": "array", "items": {"type": "string"}}},
          "required": ["words"]}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    path = tmp_path / "bin" / "codex"
    path.parent.mkdir()
    path.write_text(FAKE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("OCRMAKER_CODEX", str(path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    for variable in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.setenv(variable, "must-not-reach-codex")

    class Fake:
        frozen = tmp_path / "calls.jsonl.frozen"
        home = tmp_path / "codex-home"

        def calls(self):
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

        def mode(self, value):
            monkeypatch.setenv("FAKE_CODEX_MODE", value)

        def login(self, text, code=0):
            monkeypatch.setenv("FAKE_CODEX_LOGIN", text)
            monkeypatch.setenv("FAKE_CODEX_LOGIN_EXIT", str(code))
    return Fake()


def ask(fake, **options):
    model = options.pop("model", "gpt-6.1-sol")
    effort = options.pop("effort", None)
    request = Request(instructions=options.pop("instructions", "Read the marked words."),
                      data=options.pop("data", {"page": 1, "spots": [{"id": "w7", "partial": "Rychv…"}]}),
                      schema=options.pop("schema", SCHEMA), **options)
    return CodexProvider().complete(request, model=model, effort=effort, stopped=lambda: False)


def config(args) -> dict:
    """The -c overrides of a call, with their TOML values decoded."""
    import tomllib
    values = {}
    for index, arg in enumerate(args):
        if arg == "-c":
            key, value = args[index + 1].split("=", 1)
            values[key] = tomllib.loads(f"v = {value}")["v"]
    return values


def test_request_runs_on_the_subscription_isolated_and_without_api_keys(fake):
    ask(fake)
    call = fake.calls()[0]
    for variable in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        assert variable not in call["env"]
    assert call["env"]["CODEX_HOME"] == str(fake.home)  # the login lives there
    args = call["args"]
    assert args[0] == "exec"
    assert args[args.index("-m") + 1] == "gpt-6.1-sol"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert {"--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"} <= set(args)
    overrides = config(args)
    assert overrides["features.shell_tool"] is False
    assert overrides["project_doc_max_bytes"] == 0
    assert overrides["model_reasoning_effort"] == "medium"
    assert Path(call["cwd"]).name.startswith("ocrmaker-codex-") and not Path(call["cwd"]).exists()


def test_the_chosen_reasoning_effort_is_passed_to_the_cli(fake):
    ask(fake, effort="high")
    assert config(fake.calls()[0]["args"])["model_reasoning_effort"] == "high"


def test_shared_instructions_lead_the_request_and_data_goes_only_to_stdin(fake):
    ask(fake, instructions="Read the marked words.", data={"spots": [{"id": "w7", "partial": "Rychv…"}]})
    call = fake.calls()[0]
    # Developer instructions precede the user message, so their prefix is cached across requests.
    assert config(call["args"])["developer_instructions"] == "Read the marked words."
    assert "Rychv…" not in json.dumps(call["args"], ensure_ascii=False)
    assert '"partial":"Rychv…"' in call["stdin"]


def test_images_are_attached_as_files(fake, tmp_path):
    crop = tmp_path / "crop.png"
    crop.write_bytes(PNG)
    ask(fake, images=(crop,))
    assert fake.calls()[0]["images"] == [PNG.hex()]


def test_web_search_is_live_only_when_asked(fake):
    ask(fake)
    ask(fake, web_search=True)
    without, with_search = (config(call["args"]) for call in fake.calls())
    assert without["web_search"] == "disabled"
    assert with_search["web_search"] == "live"


def test_the_answer_schema_goes_to_the_output_schema_file(fake):
    completion = ask(fake)
    assert fake.calls()[0]["schema"] == SCHEMA
    assert json.loads(completion.text) == {"words": ["Rychvald"]}


def test_a_request_without_schema_returns_the_plain_text(fake, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_ANSWER", "plain text answer")
    completion = ask(fake, schema=None)
    assert "--output-schema" not in fake.calls()[0]["args"]
    assert completion.text == "plain text answer"


def test_reported_tokens_are_parsed_with_cached_input_as_part_of_the_input(fake):
    completion = ask(fake)
    assert completion.usage == {"input": 14000, "cached_input": 9000, "output": 40, "reasoning": 15}


def test_unknown_usage_stays_unknown_never_zero(fake):
    fake.mode("nousage")
    assert ask(fake).usage is None


def test_without_a_chosen_model_codex_uses_the_first_listed_model_of_its_cache(fake):
    fake.home.mkdir()
    (fake.home / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "hidden", "display_name": "Hidden", "visibility": "hide", "priority": 0},
        {"slug": "gpt-b", "display_name": "GPT-B", "visibility": "list", "priority": 2},
        {"slug": "gpt-a", "display_name": "GPT-A", "visibility": "list", "priority": 1}]}))
    provider = CodexProvider()
    assert provider.models() == [{"id": "gpt-a", "label": "GPT-A"}, {"id": "gpt-b", "label": "GPT-B"}]
    assert provider.default_model() == "gpt-a"


def test_a_missing_cli_gives_a_czech_message(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("OCRMAKER_CODEX", str(tmp_path / "nowhere" / "codex"))
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == MISSING_CLI
    assert "není nainstalovaný" in str(caught.value)


def test_a_logged_out_cli_is_refused_before_any_request(fake):
    fake.login("Not logged in", code=1)
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == UNAUTHORIZED
    assert "codex login" in str(caught.value)
    assert fake.calls() == []


def test_an_api_key_login_is_refused_because_only_the_subscription_may_pay(fake):
    fake.login("Logged in using an API key - sk-proj-***")
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == UNAUTHORIZED
    assert "API klíč" in str(caught.value)
    assert fake.calls() == []


def test_a_rejected_authorisation_asks_for_the_login(fake):
    fake.mode("login")
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == UNAUTHORIZED


def test_a_usage_limit_is_reported_in_czech(fake):
    fake.mode("limit")
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == USAGE_LIMIT
    assert "Limit předplatného Codex je vyčerpaný" in str(caught.value)


def test_an_unreachable_server_is_told_apart(fake):
    fake.mode("offline")
    with pytest.raises(LanguageError) as caught:
        ask(fake)
    assert caught.value.code == UNREACHABLE_SERVER


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
            CodexProvider().complete(Request(instructions="x", data={}), model="gpt-a", stopped=stop.is_set)
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
