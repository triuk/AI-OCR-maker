"""Claude as a language provider: the subscription CLI called as `claude -p`, never an API key.

Every request is one non-interactive process in an empty scratch directory. It gets no tools (or only
WebSearch when a request asks for web search), an empty MCP configuration, no user or project settings,
no CLAUDE.md files and no session. The shared instructions go in the system prompt, which Claude caches on
its own; the data and images go through standard input as one stream-json user message, and the answer
schema through `--json-schema`. `--bare` is not used because it requires an API key.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .language import (DEFAULT_EFFORT, IMAGE_TYPES, MISSING_CLI, UNAUTHORIZED, UNREACHABLE_SERVER, USAGE_LIMIT, Completion,
                       LanguageError, LanguageProvider, Request, canonical_json, child_environment, czech_time,
                       find_executable, run_process)

DEFAULT_MODEL = "sonnet"
MODELS = [{"id": "sonnet", "label": "Sonnet"}, {"id": "opus", "label": "Opus"}, {"id": "fable", "label": "Fable"},
          {"id": "haiku", "label": "Haiku"}]
# A confirmed subscription login is trusted this long before `claude auth status` is asked again.
LOGIN_CHECK_SECONDS = 600
LOGIN_HINT = re.compile(r"/login|invalid api key|not logged in|authentication|oauth|unauthori[sz]ed", re.I)
LIMIT_HINT = re.compile(r"usage limit|hit your limit|limit reached|rate limit|limit will reset", re.I)
UNREACHABLE_HINT = re.compile(r"can'?t reach the api server|unable to connect|failed to connect|connection refused|"
                              r"dns error|getaddrinfo|name resolution|network is unreachable|no route to host|"
                              r"EAI_AGAIN|ENOTFOUND|ECONNREFUSED|EHOSTUNREACH|ENETUNREACH", re.I)
MISSING = ("Claude Code (příkaz „claude“) není nainstalovaný. Nainstalujte ho, nebo nastavte cestu "
           "v proměnné OCRMAKER_CLAUDE.")
NOT_SIGNED_IN = "Claude není přihlášený k předplatnému. Spusťte v terminálu „claude“ a přihlaste se příkazem /login."
API_KEY_LOGIN = ("Claude je přihlášený API klíčem. Aplikace smí používat jen předplatné: spusťte „claude“ "
                 "a přihlaste se příkazem /login k účtu claude.ai.")


def claude_usage(usage) -> dict | None:
    """Claude reports input outside the cache separately; reasoning tokens are not reported at all."""
    if not isinstance(usage, dict):
        return None
    values = [usage.get(key) for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                                          "output_tokens")]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return None
    fresh, read, written, output = values
    return {"input": fresh + read + written, "cached_input": read, "output": output, "reasoning": None}


def limit_reset(text: str, now: float) -> float | None:
    """Reset time in an error such as "usage limit reached|<epoch>"."""
    match = re.search(r"\|\s*(\d{9,11})\b", text)
    return float(match.group(1)) if match and int(match.group(1)) > now else None


class ClaudeProvider(LanguageProvider):
    name = "claude"
    label = "Claude"

    def __init__(self, executable: str | None = None):
        self._executable = executable
        self._login_checked = 0.0
        self._lock = threading.Lock()

    def models(self) -> list[dict]:
        return MODELS

    def default_model(self) -> str:
        return DEFAULT_MODEL

    def complete(self, request: Request, *, model: str | None, stopped: Callable[[], bool],
                 effort: str | None = None) -> Completion:
        executable = self._executable or find_executable("claude")
        if not executable:
            raise LanguageError(MISSING, code=MISSING_CLI)
        self._require_subscription(executable)
        with tempfile.TemporaryDirectory(prefix="ocrmaker-claude-") as scratch:
            # A file, not an argument: shared instructions may exceed the command-line limit.
            system = Path(scratch) / "system.txt"
            system.write_text(request.instructions, encoding="utf-8")
            code, stdout, stderr = run_process(
                self._command(executable, request, model, system, effort), stdin=self._message(request), cwd=Path(scratch),
                env=self._environment(), stopped=stopped, label="Claude")
        return self._answer(code, stdout, stderr)

    def _environment(self) -> dict:
        return child_environment(CLAUDE_CODE_DISABLE_CLAUDE_MDS="1")

    def _require_subscription(self, executable: str) -> None:
        with self._lock:
            if self._login_checked and time.monotonic() - self._login_checked < LOGIN_CHECK_SECONDS:
                return
        try:
            done = subprocess.run([executable, "auth", "status", "--json"], capture_output=True, text=True,
                                  encoding="utf-8", timeout=30, env=self._environment())
            status = json.loads(done.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise LanguageError(f"Nelze zjistit přihlášení Claude: {exc}", code=UNAUTHORIZED) from exc
        if not status.get("loggedIn"):
            raise LanguageError(NOT_SIGNED_IN, code=UNAUTHORIZED)
        if status.get("authMethod") == "api_key":
            raise LanguageError(API_KEY_LOGIN, code=UNAUTHORIZED)
        with self._lock:
            self._login_checked = time.monotonic()

    @staticmethod
    def _command(executable: str, request: Request, model: str | None, system: Path,
                 effort: str | None = None) -> list[str]:
        command = [executable, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                   "--model", model or DEFAULT_MODEL, "--effort", effort or DEFAULT_EFFORT, "--system-prompt-file", str(system),
                   "--tools", "WebSearch" if request.web_search else ""]
        if request.web_search:
            # Without it, -p would refuse the tool for lack of a permission prompt.
            command += ["--allowedTools", "WebSearch"]
        if request.schema is not None:
            command += ["--json-schema", canonical_json(request.schema)]
        return command + ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
                          "--disable-slash-commands", "--no-session-persistence"]

    @staticmethod
    def _message(request: Request) -> str:
        content = []
        for image in request.images:
            media_type = IMAGE_TYPES.get(image.suffix.lower())
            if media_type is None:
                raise LanguageError(f"Nepodporovaný formát obrázku pro AI: {image.name}")
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                        "data": base64.b64encode(image.read_bytes()).decode()}})
        content.append({"type": "text", "text": request.message or "Answer as instructed."})
        return json.dumps({"type": "user", "message": {"role": "user", "content": content}},
                          ensure_ascii=False) + "\n"

    def _answer(self, code: int, stdout: str, stderr: str) -> Completion:
        result = None
        for line in reversed(stdout.splitlines()):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
                break
        if result is None:
            raise self._failure(stderr.strip() or stdout.strip() or f"návratový kód {code}", None)
        usage = claude_usage(result.get("usage"))
        text = result.get("result") if isinstance(result.get("result"), str) else ""
        if result.get("is_error") or code != 0:
            raise self._failure(text or stderr.strip() or "Claude selhal.", usage)
        structured = result.get("structured_output")
        answer = json.dumps(structured, ensure_ascii=False) if structured is not None else text
        server_tools = (result.get("usage") or {}).get("server_tool_use") or {}
        details = {key: value for key, value in {
            "num_turns": result.get("num_turns"), "duration_api_ms": result.get("duration_api_ms"),
            "web_search_requests": server_tools.get("web_search_requests")}.items() if value is not None}
        models = list(result.get("modelUsage") or {})
        return Completion(text=answer, usage=usage, usage_source="claude_cli",
                          model=", ".join(models) if models else None, details=details)

    @staticmethod
    def _failure(message: str, usage: dict | None) -> LanguageError:
        if LIMIT_HINT.search(message):
            reset = limit_reset(message, time.time())
            when = f" Obnoví se {czech_time(reset)}." if reset else " Zkuste to později."
            return LanguageError("Limit předplatného Claude je vyčerpaný." + when, code=USAGE_LIMIT, retry_at=reset,
                                 usage=usage)
        if LOGIN_HINT.search(message):
            return LanguageError(NOT_SIGNED_IN, code=UNAUTHORIZED, usage=usage)
        if UNREACHABLE_HINT.search(message):
            return LanguageError(f"Server Claude je nedostupný. Zkontrolujte připojení k internetu. ({message[:200]})",
                                 code=UNREACHABLE_SERVER, usage=usage)
        return LanguageError(f"Claude selhal: {message[:300]}", usage=usage)
