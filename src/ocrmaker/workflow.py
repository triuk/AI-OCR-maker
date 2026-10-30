"""Seam 1, the project workflow interface: projects over a store and the fixed step sequence they go through.

Every operation that executes steps (run, export, rerun) holds the project's run lock for its whole duration,
so one project never has two runs at once, also across application instances. The synchronous methods
return when the work is done; `start` does the same work in a background thread for the WebUI.
"""
from __future__ import annotations

import shutil
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path

from . import settings as project_settings
from .diagnostics import Diagnostics, new_run_id
from .errors import Conflict, ConfirmationRequired, StepError, StepInterrupted
from .pages import PAGES_STEP, read_pages
from .steps.export_step import OUTPUTS
from .pdf import inspect_source
from .step import Step, StepContext, StepOutput, StepResult
from .steps import DEFINITIONS, STEP_IDS, STEPS, StepDefinition, implementations
from .store import ProjectStore, now

PENDING, RUNNING, DONE, WARNING, FAILED = "pending", "running", "done", "warning", "failed"
FINISHED = (DONE, WARNING)
# Progress is written to the project document at most this often (seconds).
PROGRESS_INTERVAL = 0.25
# Manual corrections belong to the review; rerunning a step before it discards them (ADR 0004).
FIRST_MANUAL = next(index for index, definition in enumerate(STEPS) if definition.manual)


def _pending_step() -> dict:
    return {"state": PENDING, "message": None, "started": None, "finished": None, "duration": None,
            "run_id": None, "progress": None}


def _plural(count: int, one: str, few: str, many: str) -> str:
    return one if count == 1 else few if 2 <= count <= 4 else many


class Workflow:
    def __init__(self, store: ProjectStore, *, steps: Mapping[str, Step] | None = None, language=None):
        self.store = store
        self.steps = dict(implementations() if steps is None else steps)
        unknown = set(self.steps) - set(STEP_IDS)
        if unknown:
            raise ValueError(f"Unknown step ids: {sorted(unknown)}")
        # Passed to every step as ctx.language; a scripted provider in tests.
        self.language = language
        self._active: dict[str, tuple[threading.Event, threading.Thread | None]] = {}
        self._active_lock = threading.Lock()
        self._recover()

    # Projects ----------------------------------------------------------------------------------------

    def create(self, source_pdf: Path, *, name: str | None = None, file_name: str | None = None,
               settings: dict | None = None) -> dict:
        """Create a project from a copy of the source PDF; the file itself is only read.

        file_name is the user's name of the file when source_pdf is an upload staged under another name.
        """
        source_pdf = Path(source_pdf)
        file_name = Path(file_name or source_pdf.name).name
        facts = inspect_source(source_pdf)
        name = (name or "").strip() or Path(file_name).stem
        document = {
            "name": name,
            "source": {"file_name": file_name, **facts},
            "settings": project_settings.merged({}, settings or {}),
            "steps": {definition.id: _pending_step() for definition in STEPS},
        }
        return self._view(self.store.create(source_pdf, document))

    def list(self) -> list[dict]:
        return [self._view(document) for document in self.store.list()]

    def get(self, project_id: str) -> dict:
        return self._view(self.store.get(project_id))

    def update_settings(self, project_id: str, changes: dict) -> dict:
        def change(document):
            document["settings"] = project_settings.merged(document["settings"], changes)
        return self._view(self.store.mutate(project_id, change))

    def delete(self, project_id: str) -> None:
        """Delete a project with all its results; a project in the middle of a run is refused."""
        lock = self._lock(project_id)
        try:
            self.store.delete(project_id)
        finally:
            lock.release()

    # Workflow operations -----------------------------------------------------------------------------

    def run(self, project_id: str) -> dict:
        """Run the automatic steps that are not finished yet, up to the first manual step (manual review)."""
        return self._operate(project_id, self._plan_run(project_id))

    def export(self, project_id: str) -> dict:
        """Run manual review's closing work and every step after it (Export), always anew."""
        return self._operate(project_id, self._plan_export(project_id))

    def rerun(self, project_id: str, step_id: str, *, discard_corrections: int | None = None) -> dict:
        """Invalidate a step and every later step, then run again from it.

        If manual corrections would be discarded, their count must be passed as discard_corrections,
        otherwise ConfirmationRequired is raised and nothing changes.
        """
        return self._operate(project_id, self._plan_rerun(project_id, step_id, discard_corrections))

    def start(self, project_id: str, operation: str = "run", **options) -> dict:
        """Start run, export or rerun (with step_id, discard_corrections) in the background.

        Refusals (a run in progress, a needed confirmation, nothing to run) are raised here, synchronously.
        """
        plans = {"run": self._plan_run, "export": self._plan_export, "rerun": self._plan_rerun}
        if operation not in plans:
            raise ValueError("Neznámá operace pracovního postupu.")
        return self._operate(project_id, plans[operation](project_id, **options), background=True)

    def stop(self, project_id: str) -> bool:
        """Ask a running operation to stop at the step's next checkpoint; False when nothing runs."""
        with self._active_lock:
            active = self._active.get(project_id)
        if active:
            active[0].set()
        return bool(active)

    def wait(self, project_id: str, timeout: float | None = None) -> None:
        """Wait for a background operation of the project to finish."""
        with self._active_lock:
            active = self._active.get(project_id)
        if active and active[1]:
            active[1].join(timeout)

    def close(self, timeout: float | None = None) -> None:
        """Stop every running operation and wait for it; finished steps keep their results."""
        with self._active_lock:
            active = list(self._active.values())
        for stop, _ in active:
            stop.set()
        for _, thread in active:
            if thread:
                thread.join(timeout)

    def running(self, project_id: str) -> bool:
        with self._active_lock:
            return project_id in self._active

    def rerun_impact(self, project_id: str, step_id: str) -> dict:
        """What a rerun of the step would invalidate: the step ids and the number of manual corrections lost."""
        index = self._step_index(step_id)
        self.store.get(project_id)
        corrections = len(self.store.corrections(project_id)) if index < FIRST_MANUAL else 0
        return {"corrections": corrections, "steps": list(STEP_IDS[index:])}

    # Results and diagnostics -------------------------------------------------------------------------

    def result(self, project_id: str, step_id: str) -> StepResult | None:
        """The committed result of a finished step, or None."""
        self._step_index(step_id)
        document = self.store.get(project_id)
        directory = self.store.result_dir(project_id, step_id)
        if document["steps"][step_id]["state"] not in FINISHED or not directory.is_dir():
            return None
        return StepResult(directory)

    def overlay(self, project_id: str, step_id: str, page: int) -> dict | None:
        """Step preview overlay data of one page (0-based index), or None when the step has none for it."""
        result = self.result(project_id, step_id)
        step = self.steps.get(step_id)
        if result is None or step is None:
            return None
        return step.overlay(result, page)

    def pages(self, project_id: str) -> list[dict]:
        """The page geometry of every page (see ocrmaker.pages), or [] until Import has finished."""
        result = self.result(project_id, PAGES_STEP)
        return read_pages(result) if result else []

    def page_image(self, project_id: str, page: int) -> Path:
        """The source-view working raster of one page (0-based index), a PNG; KeyError when there is none."""
        pages = self.pages(project_id)
        if not 0 <= page < len(pages):
            raise KeyError("Obraz stránky zatím není k dispozici.")
        raster = pages[page]["raster"]
        result = self.result(project_id, raster["step"])
        if result is None or not result.exists(raster["file"]):
            raise KeyError("Obraz stránky zatím není k dispozici.")
        return result.path(raster["file"])

    def outputs(self, project_id: str) -> list[dict]:
        """The files the last export wrote (OCR PDF, ...): name and size in bytes; [] before any export."""
        result = self.result(project_id, "export")
        directory = result.path(OUTPUTS) if result else None
        if directory is None or not directory.is_dir():
            return []
        return [{"name": path.name, "size": path.stat().st_size}
                for path in sorted(directory.iterdir()) if path.is_file()]

    def output(self, project_id: str, name: str) -> Path:
        """The path of one exported file; KeyError when there is no such output."""
        if name not in {output["name"] for output in self.outputs(project_id)}:
            raise KeyError("Výstupní soubor neexistuje. Nejprve projekt exportujte.")
        return self.result(project_id, "export").path(f"{OUTPUTS}/{name}")

    def diagnostics(self, project_id: str, run_id: str | None = None) -> list[dict]:
        self.store.get(project_id)
        return Diagnostics(self.store.diagnostics_dir(project_id)).records(run_id)

    # Planning: checks that run synchronously, before any step starts ---------------------------------

    def _plan_run(self, project_id: str) -> Callable[[str, threading.Event], None]:
        self.store.get(project_id)
        if not self.steps:
            raise ValueError("Pracovní postup zatím neobsahuje žádný proveditelný krok.")
        return lambda run_id, stop: self._run_automatic(project_id, run_id, stop)

    def _plan_export(self, project_id: str) -> Callable[[str, threading.Event], None]:
        document = self.store.get(project_id)
        unfinished = [d.label for d in STEPS[:FIRST_MANUAL]
                      if d.id in self.steps and document["steps"][d.id]["state"] not in FINISHED]
        if unfinished:
            raise ValueError(f"Nejprve dokončete automatické zpracování (chybí: {', '.join(unfinished)}).")
        if not any(d.id in self.steps for d in STEPS[FIRST_MANUAL:]):
            raise ValueError("Export zatím není k dispozici.")
        return lambda run_id, stop: self._run_sequence(project_id, run_id, stop, STEPS[FIRST_MANUAL:])

    def _plan_rerun(self, project_id: str, step_id: str,
                    discard_corrections: int | None = None) -> Callable[[str, threading.Event], None]:
        index = self._step_index(step_id)
        corrections = self.rerun_impact(project_id, step_id)["corrections"]
        if corrections and discard_corrections != corrections:
            raise ConfirmationRequired(
                f"Opakování kroku zahodí {corrections} "
                f"{_plural(corrections, 'ruční opravu', 'ruční opravy', 'ručních oprav')}.", corrections)
        if not self.steps:
            raise ValueError("Pracovní postup zatím neobsahuje žádný proveditelný krok.")

        def work(run_id, stop):
            if index < FIRST_MANUAL:
                self.store.write_corrections(project_id, [])
            self._invalidate(project_id, STEP_IDS[index:])
            if index < FIRST_MANUAL:
                self._run_automatic(project_id, run_id, stop)
            else:
                self._run_sequence(project_id, run_id, stop, STEPS[FIRST_MANUAL:])
        return work

    # Execution ---------------------------------------------------------------------------------------

    def _operate(self, project_id: str, work: Callable[[str, threading.Event], None], *,
                 background: bool = False) -> dict:
        lock = self._lock(project_id)
        stop = threading.Event()
        run_id = new_run_id()

        def execute():
            try:
                work(run_id, stop)
            except Exception:  # noqa: BLE001 - a background thread has nobody else to report to
                if not background:
                    raise
                traceback.print_exc(file=sys.stderr)
            finally:
                with self._active_lock:
                    self._active.pop(project_id, None)
                lock.release()

        thread = threading.Thread(target=execute, name=f"workflow-{project_id[:8]}", daemon=True) \
            if background else None
        with self._active_lock:
            self._active[project_id] = (stop, thread)
        if thread:
            thread.start()
        else:
            execute()
        return self.get(project_id)

    def _run_automatic(self, project_id: str, run_id: str, stop: threading.Event) -> None:
        sequence = []
        for definition in STEPS:
            if definition.manual and definition.id in self.steps:
                break
            sequence.append(definition)
        self._run_sequence(project_id, run_id, stop, sequence, skip_finished=True)

    def _run_sequence(self, project_id: str, run_id: str, stop: threading.Event, sequence, *,
                      skip_finished: bool = False) -> None:
        for definition in sequence:
            step = self.steps.get(definition.id)
            if step is None:
                continue
            if skip_finished and self.store.get(project_id)["steps"][definition.id]["state"] in FINISHED:
                continue
            if stop.is_set() or self._execute(project_id, run_id, stop, definition, step) not in FINISHED:
                return

    def _execute(self, project_id: str, run_id: str, stop: threading.Event, definition: StepDefinition,
                 step: Step) -> str:
        diagnostics = Diagnostics(self.store.diagnostics_dir(project_id))
        started, clock = now(), time.monotonic()

        def begin(document):
            document["steps"][definition.id] = _pending_step() | {"state": RUNNING, "started": started,
                                                                  "run_id": run_id}
        document = self.store.mutate(project_id, begin)
        diagnostics.record(run_id, "step_started", step=definition.id, started=started)
        staging = self.store.new_result_dir(project_id, definition.id)
        last_progress = [0.0]

        def progress(done: int, total: int, label: str = "") -> None:
            moment = time.monotonic()
            if done < total and moment - last_progress[0] < PROGRESS_INTERVAL:
                return
            last_progress[0] = moment

            def change(document):
                document["steps"][definition.id]["progress"] = {"done": done, "total": total, "label": label}
            self.store.mutate(project_id, change)

        context = StepContext(
            step_id=definition.id, project_id=project_id, run_id=run_id,
            settings=project_settings.merged(document["settings"], {}),
            source_pdf=self.store.source_pdf(project_id), output=StepOutput(staging), language=self.language,
            results=lambda step_id: self.result(project_id, step_id), progress=progress, stopped=stop.is_set,
            record=lambda kind, **data: diagnostics.record(run_id, kind, **data),
            source_name=document["source"]["file_name"])
        error = None
        try:
            step.run(context)
            context.checkpoint()
        except StepInterrupted:
            outcome, state, message = "interrupted", PENDING, None
        except StepError as exc:
            outcome, state, message = FAILED, FAILED, str(exc)
        except Exception as exc:  # noqa: BLE001 - any defect fails only this step, with a trace in diagnostics
            outcome, state = FAILED, FAILED
            message = f"Krok skončil neočekávanou chybou: {exc}"
            error = traceback.format_exc()
        else:
            self.store.commit_result(project_id, definition.id, staging)
            outcome = state = WARNING if context.warnings else DONE
            message = "\n".join(context.warnings) or None
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        finished, duration = now(), round(time.monotonic() - clock, 3)

        def end(document):
            entry = document["steps"][definition.id]
            entry.update(state=state, message=message, progress=None)
            if state == PENDING:
                entry.update(started=None, run_id=None)
            else:
                entry.update(finished=finished, duration=duration)
        self.store.mutate(project_id, end)
        diagnostics.record(run_id, "step", step=definition.id, started=started, finished=finished,
                           duration=duration, outcome=outcome, message=message, warnings=context.warnings,
                           **({"error": error} if error else {}))
        return state

    def _invalidate(self, project_id: str, step_ids) -> None:
        for step_id in step_ids:
            self.store.discard_result(project_id, step_id)

        def change(document):
            for step_id in step_ids:
                document["steps"][step_id] = _pending_step()
        self.store.mutate(project_id, change)

    # Helpers -----------------------------------------------------------------------------------------

    def _lock(self, project_id: str):
        """Take the project's run lock: one workflow run (or deletion) per project at a time."""
        self.store.get(project_id)  # a missing project is reported as such, not as a lock failure
        lock = self.store.run_lock(project_id)
        try:
            return lock.acquire()
        except BlockingIOError as exc:
            raise Conflict("Projekt se právě zpracovává. Nejprve zpracování zastavte.") from exc

    @staticmethod
    def _step_index(step_id: str) -> int:
        if step_id not in DEFINITIONS:
            raise KeyError("Krok neexistuje.")
        return STEP_IDS.index(step_id)

    def _recover(self) -> None:
        """A step left running by a crash returns to pending; projects a live run holds are left alone."""
        for document in self.store.list():
            if not any(entry["state"] == RUNNING for entry in document["steps"].values()):
                continue
            lock = self.store.run_lock(document["id"])
            try:
                lock.acquire()
            except BlockingIOError:
                continue
            try:
                def change(doc):
                    for step_id, entry in doc["steps"].items():
                        if entry["state"] == RUNNING:
                            doc["steps"][step_id] = _pending_step()
                self.store.mutate(document["id"], change)
            finally:
                lock.release()

    def _view(self, document: dict) -> dict:
        """The project as callers see it: step states in workflow order with their Czech labels."""
        steps = [{"id": d.id, "label": d.label, "manual": d.manual, "implemented": d.id in self.steps,
                  **document["steps"].get(d.id, _pending_step())} for d in STEPS]
        return ({key: value for key, value in document.items() if key != "steps"}
                | {"steps": steps, "running": self.running(document["id"])})
