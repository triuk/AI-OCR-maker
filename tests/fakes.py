"""Stand-ins for workflow steps (and, once AI steps exist, the scripted language provider)."""
from __future__ import annotations

import threading

from ocrmaker.errors import StepError
from ocrmaker.step import Step


class RecordingStep(Step):
    """Writes which earlier results it saw, plus an optional overlay, warning or failure."""

    def __init__(self, step_id: str, *, reads: tuple[str, ...] = (), overlay: dict | None = None,
                 warning: str | None = None, failure: str | None = None, crash: bool = False):
        self.id = step_id
        self.reads = reads
        self.overlay_data = overlay
        self.warning = warning
        self.failure = failure
        self.crash = crash
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        seen = {step_id: ctx.result(step_id).read_json("result.json") for step_id in self.reads}
        ctx.progress(1, 2, "Polovina")
        ctx.output.write_json("result.json", {"step": self.id, "run": self.calls, "seen": seen,
                                              "language": ctx.settings["language"]})
        if self.overlay_data is not None:
            ctx.output.write_overlay(0, self.overlay_data)
        if self.crash:
            raise ZeroDivisionError("division by zero")
        if self.failure:
            raise StepError(self.failure)
        if self.warning:
            ctx.warn(self.warning)


class BlockingStep(Step):
    """Runs until released, checking for a stop request like a real long step."""

    def __init__(self, step_id: str):
        self.id = step_id
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self, ctx):
        self.entered.set()
        while not self.release.wait(0.01):
            ctx.checkpoint()
        ctx.output.write_json("result.json", {"step": self.id})
