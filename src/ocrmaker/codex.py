"""Codex as a language provider: the subscription CLI called as `codex exec`, never an API key.

Every request is one ephemeral, read-only `codex exec` run in an empty scratch directory, without the user's
config.toml, execution rules, project docs, shell tool or apps; web search is live only when a request asks
for it. The shared instructions go in as developer instructions, which precede the user message, so the
provider's automatic prefix cache covers them; the data comes through standard input, images as files,
the answer schema as an output schema file. Usage comes from the JSONL `turn.completed` event.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .language import (IMAGE_TYPES, MISSING_CLI, UNAUTHORIZED, UNREACHABLE_SERVER, USAGE_LIMIT, Completion,
                       LanguageError, LanguageProvider, Request, child_environment, find_executable, run_process)

# Offered when Codex has not cached its model list yet (~/.codex/models_cache.json).
FALLBACK_MODELS = [{"id": "gpt-6.1-sol", "label": "GPT-6.1-Sol"}, {"id": "gpt-6-astra", "label": "GPT-6-Astra"},
                   {"id": "gpt-6.1-terra", "label": "GPT-6.1 Terra"}, {"id": "gpt-6-luna", "label": "GPT-6-Luna"}]
EFFORT = "medium"
LOGIN_CHECK_SECONDS = 600
# Above this, developer instructions would not fit one command-line argument; they lead the prompt instead.
ARGUMENT_LIMIT = 100_000
LOGIN_HINT = re.compile(r"401|unauthori[sz]ed|not logged in|login|authentication|refresh token|token (has )?expired",
                        re.I)
LIMIT_HINT = re.compile(r"usage limit|hit your limit|rate limit|quota|too many requests|429", re.I)
UNREACHABLE_HINT = re.compile(r"error sending request|dns error|failed to lookup address|connection refused|"
                              r"network is unreachable|no route to host|failed to connect|name resolution", re.I)
MISSING = "Codex (příkaz „codex“) není nainstalovaný. Nainstalujte ho, nebo nastavte cestu v proměnné OCRMAKER_CODEX."
NOT_SIGNED_IN = "Codex není přihlášený k předplatnému ChatGPT. Spusťte v terminálu „codex login“ a přihlaste se."
API_KEY_LOGIN = ("Codex je přihlášený API klíčem. Aplikace smí používat jen předplatné: spusťte „codex logout“ "
                 "a potom „codex login“ s účtem ChatGPT.")
# Tools and context Codex would otherwise add to a request; none of them serves a transcription request.
ISOLATION = {"features.shell_tool": "false", "features.apps": "false", "features.multi_agent": "false",
             "project_doc_max_bytes": "0", "skills.max_context_tokens": "1"}


def toml_string(text: str) -> str:
    """A TOML basic string; JSON's escapes are valid TOML except that DEL must be escaped too."""
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def codex_usage(usage) -> dict | None:
    """Codex counts cached input inside the input and reasoning inside the output."""
    if not isinstance(usage, dict):
        return None
    return {"input": usage.get("input_tokens"), "cached_input": usage.get("cached_input_tokens"),
            "output": usage.get("output_tokens"), "reasoning": usage.get("reasoning_output_tokens")}


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


class CodexProvider(LanguageProvider):
    name = "codex"
    label = "Codex"

    def __init__(self, executable: str | None = None):
        self._executable = executable
        self._login_checked = 0.0
        self._lock = threading.Lock()

    def models(self) -> list[dict]:
        """The models Codex lists for the account, best first, as cached by the CLI itself."""
        try:
            cached = json.loads((codex_home() / "models_cache.json").read_text(encoding="utf-8"))["models"]
            listed = sorted((m for m in cached if m.get("visibility") == "list" and m.get("slug")),
                            key=lambda m: m.get("priority", 1 << 30))
            models = [{"id": m["slug"], "label": m.get("display_name") or m["slug"]} for m in listed]
        except (OSError, ValueError, KeyError, TypeError):
            models = []
        return models or FALLBACK_MODELS

    def default_model(self) -> str:
        return self.models()[0]["id"]

    def complete(self, request: Request, *, model: str | None, stopped: Callable[[], bool]) -> Completion:
        executable = self._executable or find_executable("codex")
        if not executable:
            raise LanguageError(MISSING, code=MISSING_CLI)
        self._require_subscription(executable)
        with tempfile.TemporaryDirectory(prefix="ocrmaker-codex-") as scratch:
            scratch = Path(scratch)
            command, prompt = self._command(executable, request, model or self.default_model(), scratch)
            code, stdout, stderr = run_process(command, stdin=prompt, cwd=scratch, env=child_environment(),
                                               stopped=stopped, label="Codex")
            last = scratch / "answer.txt"
            final = last.read_text(encoding="utf-8") if last.is_file() else None
        return self._answer(code, stdout, stderr, final)

    def _require_subscription(self, executable: str) -> None:
        with self._lock:
            if self._login_checked and time.monotonic() - self._login_checked < LOGIN_CHECK_SECONDS:
                return
        try:
            done = subprocess.run([executable, "login", "status"], capture_output=True, text=True, encoding="utf-8",
                                  timeout=30, env=child_environment())
        except (OSError, subprocess.SubprocessError) as exc:
            raise LanguageError(f"Nelze zjistit přihlášení Codex: {exc}", code=UNAUTHORIZED) from exc
        status = (done.stdout + done.stderr).lower()
        if "api key" in status:
            raise LanguageError(API_KEY_LOGIN, code=UNAUTHORIZED)
        if done.returncode != 0 or "chatgpt" not in status:
            raise LanguageError(NOT_SIGNED_IN, code=UNAUTHORIZED)
        with self._lock:
            self._login_checked = time.monotonic()

    @staticmethod
    def _command(executable: str, request: Request, model: str, scratch: Path) -> tuple[list[str], str]:
        command = [executable, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                   "--ignore-rules", "--color", "never", "--sandbox", "read-only", "--cd", str(scratch),
                   "-m", model, "-o", str(scratch / "answer.txt")]
        overrides = {**ISOLATION, "model_reasoning_effort": toml_string(EFFORT),
                     "web_search": toml_string("live" if request.web_search else "disabled")}
        prompt = request.message or "Answer as instructed."
        instructions = toml_string(request.instructions)
        if len(instructions.encode("utf-8")) <= ARGUMENT_LIMIT:
            overrides["developer_instructions"] = instructions
        else:
            prompt = request.instructions + "\n\n" + prompt
        for key, value in overrides.items():
            command += ["-c", f"{key}={value}"]
        if request.schema is not None:
            schema = scratch / "schema.json"
            schema.write_text(json.dumps(request.schema, ensure_ascii=False), encoding="utf-8")
            command += ["--output-schema", str(schema)]
        for image in request.images:
            if image.suffix.lower() not in IMAGE_TYPES:
                raise LanguageError(f"Nepodporovaný formát obrázku pro AI: {image.name}")
            # With "=", the option takes exactly one file and cannot swallow the next argument.
            command.append(f"--image={image.resolve()}")
        return command, prompt

    def _answer(self, code: int, stdout: str, stderr: str, final: str | None) -> Completion:
        usage, failure, message = None, None, None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "turn.completed":
                usage = codex_usage(event.get("usage"))
            elif kind == "turn.failed":
                failure = (event.get("error") or {}).get("message") or failure
            elif kind == "error":
                failure = failure or event.get("message")
            elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
                message = event["item"].get("text")
        if failure or code != 0:
            raise self._failure(failure or stderr.strip() or stdout.strip() or f"návratový kód {code}", usage)
        text = final if final is not None else message
        if text is None:
            raise LanguageError("Codex nevrátil žádnou odpověď.", usage=usage)
        return Completion(text=text, usage=usage, usage_source="codex_exec", model=None)

    @staticmethod
    def _failure(message: str, usage: dict | None) -> LanguageError:
        if LIMIT_HINT.search(message):
            return LanguageError(f"Limit předplatného Codex je vyčerpaný. Zkuste to později. ({message[:200]})",
                                 code=USAGE_LIMIT, usage=usage)
        if UNREACHABLE_HINT.search(message):
            return LanguageError(f"Server Codex je nedostupný. Zkontrolujte připojení k internetu. ({message[:200]})",
                                 code=UNREACHABLE_SERVER, usage=usage)
        if LOGIN_HINT.search(message):
            return LanguageError(NOT_SIGNED_IN, code=UNAUTHORIZED, usage=usage)
        return LanguageError(f"Codex selhal: {message[:300]}", usage=usage)
