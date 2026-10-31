"""Per-run diagnostic records (English, JSON Lines) for analysing runs afterwards.

Each workflow run appends to `<project>/diagnostics/<run id>.jsonl`. Every record has `kind`, `time`
and `run_id`. Kinds written so far:
    step_started  {step, started}
    step          {step, started, finished, duration (s), outcome, message, warnings[, error]}
    ai_request    {step (absent for Check AI), purpose, provider, model, instructions, prompt, schema,
                   web_search, images [{path, bytes}], duration (s), outcome (ok | failed | interrupted),
                   response, usage {input, cached_input, output, reasoning, total, uncached_input} or null when
                   unknown (never 0), usage_source, model_reported, details, error, error_code, retry_at}
                  written by ocrmaker.language.LanguageSession for every AI request.
    report_failed {error} when the run report (ocrmaker.report) could not be written.
Steps add their own kinds through StepContext.record, e.g. ocr_page, export and export_verification
{strategy, findings (the first 200), counts {kind: n}, seconds}, one per text strategy tried.
The run report summarises these records together with the step results.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.Lock()


def new_run_id() -> str:
    # Sorts chronologically, so the record files read back in run order.
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:6]


class Diagnostics:
    def __init__(self, directory: Path):
        self.directory = directory

    def record(self, run_id: str, kind: str, **data) -> dict:
        entry = {"kind": kind, "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "run_id": run_id, **data}
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with _lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / f"{run_id}.jsonl").open("a", encoding="utf-8") as output:
                output.write(line)
                output.flush()
                os.fsync(output.fileno())
        return entry

    def records(self, run_id: str | None = None) -> list[dict]:
        if not self.directory.is_dir():
            return []
        files = [self.directory / f"{run_id}.jsonl"] if run_id else sorted(self.directory.glob("*.jsonl"))
        entries = []
        for path in files:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a line cut short by a crash
        return entries
