"""Export: writes the OCR PDF from the source PDF with the recognised words as an invisible text layer.

Its result holds the output files under `outputs/` (named after the source PDF) and `textlayer.json`, the
plan of what was written: per page, the lines in written order with each word's id, text and box. Export
verification and the step preview read that plan.
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import textlayer
from ..pages import PREPARED_STEP, read_pages
from ..step import Step, StepContext, StepResult
from ..words import OCR_STEP, read_page

OUTPUTS = "outputs"
PLAN_FILE = "textlayer.json"


def output_names(source_name: str) -> dict[str, str]:
    """Output file names derived from the source PDF's file name."""
    stem = Path(source_name).stem or "dokument"
    return {"pdf": f"{stem}-ocr.pdf"}


class ExportStep(Step):
    id = "export"

    def text_layer_plan(self, ctx: StepContext, pages: list[dict]) -> list[dict]:
        """Per page, the lines of words in the order they are written; for now Tesseract's (geometric) order."""
        ocr = ctx.result(OCR_STEP)
        plan = []
        for page in pages:
            recognised = read_page(ocr, page["index"]) or {"lines": []}
            plan.append({"index": page["index"], "lines": [
                [{key: word[key] for key in ("id", "text", "bbox")} for word in line["words"]]
                for line in recognised["lines"]]})
        return plan

    def run(self, ctx: StepContext) -> None:
        started = time.monotonic()
        pages = read_pages(ctx.result(PREPARED_STEP))
        plan = self.text_layer_plan(ctx, pages)
        ctx.checkpoint()
        name = output_names(ctx.source_name)["pdf"]
        ctx.progress(0, 1, "Zápis OCR PDF")
        textlayer.write_ocr_pdf(ctx.source_pdf, ctx.output.path(f"{OUTPUTS}/{name}"),
                                [{"page": page, "lines": item["lines"]} for page, item in zip(pages, plan)])
        ctx.output.write_json(PLAN_FILE, {"pages": plan})
        ctx.progress(1, 1, "Zápis OCR PDF")
        ctx.record("export", outputs=[name], pages=len(plan),
                   words=sum(len(line) for item in plan for line in item["lines"]),
                   seconds=round(time.monotonic() - started, 3))

    def overlay(self, result: StepResult, page: int) -> dict | None:
        """The written text layer of one page: word boxes with their text, in written order."""
        pages = result.read_json(PLAN_FILE)["pages"]
        item = next((item for item in pages if item["index"] == page), None)
        if item is None:
            return None
        return {"words": [word for line in item["lines"] for word in line]}


STEP = ExportStep()
