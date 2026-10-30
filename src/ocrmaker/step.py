"""What a workflow step implementation sees: its context, its own output and earlier steps' results."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .errors import StepError, StepInterrupted
from .files import write_bytes_atomic, write_json_atomic


def _overlay_name(page: int) -> str:
    return f"overlay/page-{page:04d}.json"


class StepResult:
    """A committed step result: a directory of named files, read-only for everybody but its own step."""

    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, name: str) -> Path:
        relative = PurePosixPath(name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError(f"Invalid result file name: {name!r}")
        return self.directory.joinpath(*relative.parts)

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def read_json(self, name: str):
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def read_bytes(self, name: str) -> bytes:
        return self.path(name).read_bytes()

    def overlay(self, page: int) -> dict | None:
        """The stored step preview overlay of one page (0-based page index), if the step wrote one."""
        name = _overlay_name(page)
        return self.read_json(name) if self.exists(name) else None


class StepOutput(StepResult):
    """The result a running step writes. It becomes visible to others only when the step succeeds."""

    def path(self, name: str) -> Path:
        path = super().path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, name: str, data) -> None:
        write_json_atomic(self.path(name), data)

    def write_bytes(self, name: str, data: bytes) -> None:
        write_bytes_atomic(self.path(name), data)

    def write_overlay(self, page: int, data: dict) -> None:
        """Store the step preview overlay of one page; geometry is in source page coordinates."""
        self.write_json(_overlay_name(page), data)


class StepContext:
    """Everything a step may use. Steps exchange data only through results, never through each other."""

    def __init__(self, *, step_id: str, project_id: str, run_id: str, settings: dict, source_pdf: Path,
                 output: StepOutput, language, results: Callable[[str], StepResult | None],
                 progress: Callable[[int, int, str], None], stopped: Callable[[], bool], record: Callable[..., None],
                 source_name: str = "source.pdf"):
        self.step_id = step_id
        self.project_id = project_id
        self.run_id = run_id
        self.settings = settings
        self.source_pdf = source_pdf
        # The user's file name of the source PDF; output names derive from it.
        self.source_name = source_name
        self.output = output
        # The language provider for AI requests; steps never know which AI answers (ADR 0002).
        self.language = language
        self.warnings: list[str] = []
        self._results = results
        self._progress = progress
        self._stopped = stopped
        self._record = record

    def result(self, step_id: str) -> StepResult:
        """The committed result of an earlier step; a missing one fails this step with a Czech message."""
        result = self._results(step_id)
        if result is None:
            raise StepError(f"Chybí výsledek předchozího kroku „{step_id}“. Spusťte nejprve tento krok.")
        return result

    def has_result(self, step_id: str) -> bool:
        return self._results(step_id) is not None

    def progress(self, done: int, total: int, label: str = "") -> None:
        """Report progress shown on the step tile; label is Czech, e.g. "Stránka 3 z 12"."""
        self._progress(done, total, label)

    def warn(self, message: str) -> None:
        """Finish as done-with-warning; the Czech message is shown to the user."""
        self.warnings.append(message)

    def checkpoint(self) -> None:
        """Call between units of work: raises when the user stopped the run."""
        if self._stopped():
            raise StepInterrupted("Zpracování bylo zastaveno.")

    def record(self, kind: str, **data) -> None:
        """Add an English diagnostic record to this run, e.g. kind="ai_request" with model and usage."""
        self._record(kind, step=self.step_id, **data)


class Step:
    """Base class of a step implementation; subclasses set `id` and implement `run`."""

    id: str = ""

    def run(self, ctx: StepContext) -> None:
        raise NotImplementedError

    def overlay(self, result: StepResult, page: int) -> dict | None:
        """Step preview overlay data of one page. By default it is what the step stored with write_overlay."""
        return result.overlay(page)

    def report(self, result: StepResult) -> ReportSection | None:
        """This step's section of the run report, computed from its committed result; None adds none.

        See ocrmaker.report for how sections end up in the report JSON and the human summary.
        """
        return None


@dataclass
class ReportSection:
    """A step's contribution to the run report (English).

    data goes into the report JSON as `sections[<step id>]`; keep it JSON-serialisable and put per-page
    metrics in a "pages" list of {"index" (0-based), ...}. summary is a few Markdown lines for the human
    summary, shown under a "## <title>" heading.
    """
    title: str
    data: dict
    summary: list[str] = field(default_factory=list)
