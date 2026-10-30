"""Export: writes the OCR PDF from the source PDF with the recognised words as an invisible text layer,
page by page in the reading order (ADR 0005).

Its result holds the output files under `outputs/` (named after the source PDF) and `textlayer.json`, the
plan of what was written: per page, the lines in written order with each word's id, text and box. Export
verification and the step preview read that plan.
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import textlayer
from ..layout import LAYOUT_STEP, read_layout_page
from ..pages import PREPARED_STEP, read_pages
from ..reading_order import ORDER_FILE, ORDER_STEP
from ..step import Step, StepContext, StepResult
from ..words import OCR_STEP, page_words, read_page

OUTPUTS = "outputs"
PLAN_FILE = "textlayer.json"


def output_names(source_name: str) -> dict[str, str]:
    """Output file names derived from the source PDF's file name."""
    stem = Path(source_name).stem or "dokument"
    return {"pdf": f"{stem}-ocr.pdf"}


class ExportStep(Step):
    id = "export"

    def text_layer_plan(self, ctx: StepContext, pages: list[dict]) -> list[dict]:
        """Per page, the lines of words in the order they are written: the reading order (articles, their
        blocks, the blocks' lines), or Tesseract's recognition order when there is no reading order."""
        ocr = ctx.result(OCR_STEP)
        ordered = ctx.has_result(LAYOUT_STEP) and ctx.has_result(ORDER_STEP)
        order = ctx.result(ORDER_STEP).read_json(ORDER_FILE) if ordered else None
        order_pages = {item["index"]: item for item in (order or {}).get("pages", [])}
        plan = []
        for page in pages:
            recognised = read_page(ocr, page["index"]) or {"lines": []}
            words = {word["id"]: {key: word[key] for key in ("id", "text", "bbox")}
                     for word in page_words(recognised)}
            lines = [[words[word["id"]] for word in line["words"]] for line in recognised["lines"]]
            if page["index"] in order_pages:
                layout = read_layout_page(ctx.result(LAYOUT_STEP), page["index"]) or {"blocks": []}
                blocks = {block["id"]: block for block in layout["blocks"]}
                lines = [[words[word] for word in line if word in words]
                         for article in order_pages[page["index"]]["articles"]
                         for block in article["blocks"] for line in blocks[block]["lines"]]
                # Words no block holds (there should be none) still reach the text layer, at the end.
                placed = {word["id"] for line in lines for word in line}
                rest = [word for word in words.values() if word["id"] not in placed]
                lines = [line for line in lines if line] + ([rest] if rest else [])
            plan.append({"index": page["index"], "lines": lines})
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
