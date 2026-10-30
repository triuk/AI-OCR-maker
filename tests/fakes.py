"""Stand-ins for workflow steps and the scripted language provider that replaces the AI in seam 1 tests."""
from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from ocrmaker.errors import StepError
from ocrmaker.language import Completion, LanguageProvider, Request
from ocrmaker.step import Step
from ocrmaker.steps.prepare_step import PrepareStep


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


class TransformingPrepare(PrepareStep):
    """Image preparation that leaves the rasters alone but sets a synthetic page transform (rotation, crop)."""

    def __init__(self, rotate: float = 0.0, crop: list[float] | None = None):
        self.rotate = rotate
        self.crop = crop

    def prepare_page(self, page, raster, ctx):
        return {**page, "transform": {"rotate": self.rotate, "crop": self.crop}}


# What a scripted request reports unless the test says otherwise; usage=None reports unknown usage.
SCRIPTED_USAGE = {"input": 1200, "cached_input": 1000, "output": 80, "reasoning": None}


@dataclass
class ScriptedRequest:
    """One request the scripted provider received; images are kept as bytes, their files may be gone later."""
    purpose: str
    instructions: str
    data: object
    schema: dict | None
    images: list[bytes]
    web_search: bool
    model: str | None


class ScriptedProvider(LanguageProvider):
    """The AI stand-in of seam 1 tests: deterministic answers per request purpose, for every provider name.

    Only the connection is scripted. The session around it is the real one, so requests are recorded in
    diagnostics and JSON answers are parsed exactly as with Claude or Codex.

    Script answers with `on(purpose, *answers)`. An answer is a dict or list (sent as JSON text), a str (raw
    text, e.g. invalid JSON), an exception instance (raised, e.g. LanguageError(..., code=USAGE_LIMIT)), or a
    callable taking the ScriptedRequest and returning any of these. Several answers are used in turn and the
    last one keeps answering. A purpose without an answer fails the test loudly (AssertionError).
    """

    name = "scripted"
    label = "Scripted"

    def __init__(self, answers: dict | None = None, *, usage: dict | None = SCRIPTED_USAGE,
                 model: str = "scripted-model"):
        self.answers = {purpose: [answer] for purpose, answer in (answers or {}).items()}
        self.usage = usage
        self.model = model
        self.requests: list[ScriptedRequest] = []
        self._lock = threading.Lock()

    def on(self, purpose: str, *answers) -> "ScriptedProvider":
        with self._lock:
            self.answers[purpose] = list(answers)
        return self

    def requests_for(self, purpose: str) -> list[ScriptedRequest]:
        return [request for request in self.requests if request.purpose == purpose]

    def models(self) -> list[dict]:
        return [{"id": self.model, "label": self.model}]

    def default_model(self) -> str:
        return self.model

    def complete(self, request: Request, *, model, stopped) -> Completion:
        scripted = ScriptedRequest(purpose=request.purpose, instructions=request.instructions,
                                   data=copy.deepcopy(request.data), schema=request.schema,
                                   images=[Path(image).read_bytes() for image in request.images],
                                   web_search=request.web_search, model=model)
        with self._lock:
            self.requests.append(scripted)
            queue = self.answers.get(request.purpose)
            if not queue:
                raise AssertionError(f"The scripted provider has no answer for purpose {request.purpose!r}.")
            answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(answer) and not isinstance(answer, type):
            answer = answer(scripted)
        if isinstance(answer, BaseException):
            raise answer
        text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        return Completion(text=text, usage=copy.deepcopy(self.usage), usage_source="scripted", model=model)
