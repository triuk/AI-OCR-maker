"""Run report: what a workflow run left behind, for analysing and tuning runs later (e.g. in Claude Code).

Every workflow operation (run, export, rerun) writes one report when it ends, whatever its outcome, into
`<project>/reports/<run id>.json` plus a short Markdown summary `<run id>.md`. Both are English.

The report describes the project as that run left it: every step's latest state and timing (from whichever
run produced it), the AI requests behind the current step results, and each step's own section.

    {"report": 1, "generated": iso time,
     "project": {"id", "name", "source": {"file_name", "pages"}, "settings"},
     "run": {"id", "operation" (run | export | rerun), "started", "finished", "duration" (s),
             "steps": [{"step", "outcome" (done | warning | failed | interrupted), "duration"}]},  # this run only
     "steps": [{"id", "state", "started", "finished", "duration", "message", "run_id"}],      # all ten steps
     "ai": {"requests": [{"run_id", "step", "purpose", "provider", "model", "duration", "outcome",
                          "web_search", "images", "usage", "usage_source", "error_code"}],
            "totals": {"requests", "failed", "duration", "unknown_usage" (requests with unknown usage),
                       "usage": {input, cached_input, output, reasoning, total, uncached_input}
                                # each summed over the requests that report it, null when none does
                       "models": [{"provider", "model", "requests"}]}},
     "sections": {<step id>: <the step's section data>}}

Contributing a section: a step implementation overrides `Step.report(result)` and returns a
`ReportSection(title, data, summary)` computed from its own committed result (see ocrmaker.step). The
section appears for as long as the step has a finished result. Its data lands under `sections[<step id>]`;
per-page metrics go in a "pages" list of {"index" (0-based), ...}. Its summary lines (Markdown) are shown
under "## <title>" in the human summary. A section that fails to compute is replaced by {"error": ...}, so
a report is always written. Metrics that are no step's result (manual corrections, say) belong to the step
that applies them. AI requests are summarised here from diagnostics; steps need not repeat them.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .diagnostics import Diagnostics
from .files import write_bytes_atomic, write_json_atomic
from .step import ReportSection, Step, StepResult
from .steps import STEP_IDS

REPORT_VERSION = 1
# The directory of run reports inside the project directory.
REPORTS = "reports"
USAGE_FIELDS = ("input", "cached_input", "output", "reasoning", "total", "uncached_input")


class RunReports:
    """The reports of one project, one JSON file and one summary per run, named by run id (chronological)."""

    def __init__(self, directory: Path):
        self.directory = directory

    def write(self, report: dict, summary: str) -> None:
        run_id = report["run"]["id"]
        write_json_atomic(self.directory / f"{run_id}.json", report)
        write_bytes_atomic(self.directory / f"{run_id}.md", summary.encode("utf-8"))

    def run_ids(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))

    def path(self, run_id: str | None, suffix: str) -> Path | None:
        """The report file of a run (the latest run by default), or None when there is none."""
        run_id = run_id or next(reversed(self.run_ids()), None)
        if run_id is None or "/" in run_id or run_id.startswith("."):
            return None
        path = self.directory / f"{run_id}{suffix}"
        return path if path.is_file() else None


def build(document: dict, run: dict, diagnostics: Diagnostics, steps: dict[str, Step],
          results: Callable[[str], StepResult | None]) -> tuple[dict, str]:
    """The report JSON and its human summary for a project document as a run left it.

    run is {"id", "operation", "started", "finished", "duration"}; results gives a step's committed result.
    """
    entries = [{"id": step_id, **{key: document["steps"][step_id].get(key) for key in
                                  ("state", "started", "finished", "duration", "message", "run_id")}}
               for step_id in STEP_IDS]
    run_ids = sorted({entry["run_id"] for entry in entries if entry["run_id"]} | {run["id"]})
    records = {run_id: diagnostics.records(run_id) for run_id in run_ids}
    executed = [{"step": record["step"], "outcome": record["outcome"], "duration": record["duration"]}
                for record in records[run["id"]] if record["kind"] == "step"]
    current = {entry["id"]: entry["run_id"] for entry in entries}
    requests = [_request(record) for run_id in run_ids for record in records[run_id]
                if record["kind"] == "ai_request" and record.get("step")
                and current.get(record["step"]) == record["run_id"]]
    sections = _sections(steps, results)
    report = {
        "report": REPORT_VERSION,
        "generated": run["finished"],
        "project": {"id": document["id"], "name": document["name"],
                    "source": {key: document["source"].get(key) for key in ("file_name", "pages")},
                    "settings": document["settings"]},
        "run": {**run, "steps": executed},
        "steps": entries,
        "ai": {"requests": requests, "totals": _totals(requests)},
        "sections": {step_id: section.data for step_id, section in sections.items()},
    }
    return report, summarize(report, sections)


def _request(record: dict) -> dict:
    return {"run_id": record["run_id"], "step": record.get("step"), "purpose": record.get("purpose"),
            "provider": record.get("provider"), "model": record.get("model"), "duration": record.get("duration"),
            "outcome": record.get("outcome"), "web_search": record.get("web_search"),
            "images": len(record.get("images") or []), "usage": record.get("usage"),
            "usage_source": record.get("usage_source"), "error_code": record.get("error_code")}


def _totals(requests: list[dict]) -> dict:
    usage = {}
    for name in USAGE_FIELDS:
        known = [request["usage"][name] for request in requests
                 if request["usage"] and request["usage"].get(name) is not None]
        usage[name] = sum(known) if known else None
    models: dict[tuple, int] = {}
    for request in requests:
        key = (request["provider"], request["model"])
        models[key] = models.get(key, 0) + 1
    return {"requests": len(requests), "failed": sum(request["outcome"] != "ok" for request in requests),
            "duration": round(sum(request["duration"] or 0 for request in requests), 3),
            "unknown_usage": sum(request["usage"] is None for request in requests), "usage": usage,
            "models": [{"provider": provider, "model": model, "requests": count}
                       for (provider, model), count in models.items()]}


def _sections(steps: dict[str, Step], results: Callable[[str], StepResult | None]) -> dict[str, ReportSection]:
    sections = {}
    for step_id in STEP_IDS:
        step, result = steps.get(step_id), None
        if step is None or (result := results(step_id)) is None:
            continue
        try:
            section = step.report(result)
        except Exception as exc:  # noqa: BLE001 - a broken section must not cost the whole report
            section = ReportSection(step_id, {"error": f"{type(exc).__name__}: {exc}"},
                                    [f"The section could not be computed: {type(exc).__name__}: {exc}"])
        if section is not None:
            sections[step_id] = section
    return sections


def _cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def summarize(report: dict, sections: dict[str, ReportSection]) -> str:
    """The short human summary (Markdown) of a report."""
    project, run, settings = report["project"], report["run"], report["project"]["settings"]
    lines = [f"# Run report: {project['name']}", "",
             f"- Source: {project['source']['file_name']}, {project['source']['pages']} pages, "
             f"language {settings.get('language')}",
             f"- Run {run['id']} ({run['operation']}): {run['started']} to {run['finished']}, {run['duration']} s",
             f"- Steps run: " + (", ".join(f"{step['step']} {step['outcome']}" for step in run["steps"]) or "none"),
             "", "## Steps", "", "| Step | State | Duration (s) | Run | Message |", "|---|---|---|---|---|"]
    for entry in report["steps"]:
        which = "this run" if entry["run_id"] == run["id"] else entry["run_id"]
        lines.append(f"| {entry['id']} | {entry['state']} | {_cell(entry['duration'])} | {_cell(which)} "
                     f"| {_cell(entry['message'])} |")
    lines += ["", "## AI requests", "", _ai_summary(report["ai"])]
    for section in sections.values():
        lines += ["", f"## {section.title}", ""] + section.summary
    return "\n".join(lines) + "\n"


def _ai_summary(ai: dict) -> str:
    totals = ai["totals"]
    if not totals["requests"]:
        return "No AI requests."
    usage = totals["usage"]
    tokens = ", ".join(f"{name.replace('_', ' ')} {usage[name]}" for name in ("input", "cached_input", "output")
                       if usage[name] is not None) or "none reported"
    models = ", ".join(f"{model['provider']} {model['model']} ({model['requests']})" for model in totals["models"])
    text = (f"{totals['requests']} requests ({totals['failed']} failed), {totals['duration']} s, by {models}. "
            f"Tokens: {tokens}.")
    if totals["unknown_usage"]:
        text += f" Usage unknown for {totals['unknown_usage']} requests."
    return text


def read(path: Path | None):
    return json.loads(path.read_text(encoding="utf-8")) if path else None
