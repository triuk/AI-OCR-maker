"""Stand-ins for workflow steps and the scripted language provider that replaces the AI in seam 1 tests."""
from __future__ import annotations

import copy
import json
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from ocrmaker.errors import StepError
from ocrmaker.language import Completion, LanguageProvider, Request
from ocrmaker.step import Step
from ocrmaker.steps.export_step import ExportStep
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


class BrokenExport(ExportStep):
    """Export whose text layer assembly is deliberately broken, so that export verification has to catch it.

    breakage: "outside_box" sets the first word 150 pt to the right of its box, "order" writes the first two
    words of the first line swapped, "diacritics" writes every word without its diacritics, and
    "to_unicode" drops the font's ToUnicode map from the written PDF. strategies limits the breakage to
    those text layer strategies (all of them by default); the planned text layer itself stays intact.
    """

    def __init__(self, breakage: str, strategies: tuple[str, ...] | None = None):
        self.breakage = breakage
        self.broken_strategies = strategies

    def assemble(self, source, target, pages, strategy):
        if self.broken_strategies is not None and strategy not in self.broken_strategies:
            return super().assemble(source, target, pages, strategy)
        pages = copy.deepcopy(pages)
        lines = pages[0]["lines"]
        if self.breakage == "outside_box":
            word = lines[0][0]
            word["bbox"] = [word["bbox"][0] + 150, word["bbox"][1], word["bbox"][2] + 150, word["bbox"][3]]
        elif self.breakage == "order":
            lines[0][0], lines[0][1] = lines[0][1], lines[0][0]
        elif self.breakage == "diacritics":
            for item in pages:
                for line in item["lines"]:
                    for word in line:
                        word["text"] = "".join(char for char in unicodedata.normalize("NFD", word["text"])
                                               if not unicodedata.combining(char))
        super().assemble(source, target, pages, strategy)
        if self.breakage == "to_unicode":
            with pymupdf.open(target) as document:
                for xref in range(1, document.xref_length()):
                    if document.xref_get_key(xref, "ToUnicode")[0] != "null":
                        document.xref_set_key(xref, "ToUnicode", "null")
                document.saveIncr()


def answer_spots(readings: dict | None = None, *, extra: list[dict] = ()):
    """A scripted reconstruction answer: each marked spot with its own reading, confidently, unless told otherwise.

    readings maps a spot id, or a tuple of spot ids (a merge), to an answer item's fields: "words" (a list of
    texts, more than one for a split) or "text", plus "confidence", "error", "reasoning" and "intended".
    Spots without a reading of their own (an empty word box, say) are left unanswered; extra items are added
    as they are.
    """
    readings = {(key,) if isinstance(key, str) else tuple(key): value for key, value in (readings or {}).items()}

    def answer(request):
        spots = [spot["id"] for spot in request.data["spots"]]
        covered = {spot_id for ids in readings for spot_id in ids}
        items = []
        for ids, fields in readings.items():
            if set(ids) <= set(spots):
                words = fields.get("words") or [fields["text"]]
                items.append({"ids": list(ids), "words": words, "confidence": fields.get("confidence", 95),
                              "error": fields.get("error", "recognition"),
                              "reasoning": fields.get("reasoning", "Scripted reading."),
                              "intended": fields.get("intended")})
        for spot in request.data["spots"]:
            if spot["id"] not in covered and spot["reading"]:
                items.append({"ids": [spot["id"]], "words": [spot["reading"]], "confidence": 100, "error": "none",
                              "reasoning": "The local reading matches the print.", "intended": None})
        return {"readings": items + list(extra)}

    return answer


def default_provider() -> ScriptedProvider:
    """The scripted AI of the language_provider fixture: Reading order as Layout's numbering in one article,
    AI reconstruction confirming every problem spot's own reading (empty word boxes stay unanswered), and Web
    lookup settling nothing."""
    return ScriptedProvider().on("reading_order", lambda request: {"articles": [
        {"kind": "article", "blocks": [block["number"] for block in request.data["blocks"]], "continues": None}]}
    ).on("reconstruction", answer_spots()).on("lookup", {"lookups": []})


def run_and_export(workflow, project_id: str) -> dict:
    """Spustit, then Exportovat: the automatic steps up to manual review, then an export with no corrections."""
    workflow.run(project_id)
    return workflow.export(project_id)
