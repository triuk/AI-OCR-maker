"""Layout: builds blocks (paragraphs in columns, headings, captions, running headers and footers, tables) from
the recognised lines and words, so that every word belongs to exactly one block (ADR 0005).

The result format is described in `ocrmaker.layout`. Ads are recognised later, from the page image, by
Reading order; once it has run, the step preview shows the blocks it put into an "ad" article as ads (kind
"ad", with Layout's own kind as "layout_kind"). The Layout result itself keeps Layout's kinds.
"""
from __future__ import annotations

import time
from collections import Counter

from ..layout import LAYOUT_FILE, block_words, build_page, page_file
from ..pages import PREPARED_STEP, read_pages
from ..reading_order import ORDER_FILE, ORDER_STEP
from ..step import Step, StepContext, StepResult
from ..words import OCR_STEP, page_words, read_page

SNIPPET_WORDS = 12


class LayoutStep(Step):
    id = "layout"
    preview_reads = (ORDER_STEP,)

    def preview(self, result: StepResult, page: int, later: dict[str, StepResult | None]) -> dict | None:
        """The stored overlay, with the blocks Reading order put into an ad article shown as ads."""
        overlay = self.overlay(result, page)
        order = later.get(ORDER_STEP)
        if overlay is None or order is None or not order.exists(ORDER_FILE):
            return overlay
        entry = next((item for item in order.read_json(ORDER_FILE)["pages"] if item["index"] == page), None)
        ads = {block for article in (entry or {}).get("articles", []) if article["kind"] == "ad"
               for block in article["blocks"]}
        return {**overlay, "blocks": [{**block, "kind": "ad", "layout_kind": block["kind"]}
                                      if block["id"] in ads else block for block in overlay["blocks"]]}

    def run(self, ctx: StepContext) -> None:
        pages = read_pages(ctx.result(PREPARED_STEP))
        ocr = ctx.result(OCR_STEP)
        summaries = []
        for page in pages:
            ctx.checkpoint()
            ctx.progress(page["index"], len(pages), f"Stránka {page['index'] + 1} z {len(pages)}")
            started = time.monotonic()
            recognised = read_page(ocr, page["index"]) or {"lines": []}
            layout = build_page(page, recognised)
            ctx.output.write_json(page_file(page["index"]), layout)
            kinds = dict(Counter(block["kind"] for block in layout["blocks"]))
            summaries.append({"index": page["index"], "file": page_file(page["index"]),
                              "blocks": len(layout["blocks"]), "kinds": kinds})
            texts = {word["id"]: word["text"] for word in page_words(recognised)}
            ctx.output.write_overlay(page["index"], {
                "columns": layout["columns"],
                "blocks": [{"id": block["id"], "number": block["number"], "kind": block["kind"],
                            "bbox": block["bbox"], "lines": len(block["lines"]),
                            "words": sum(len(line) for line in block["lines"]),
                            "text": " ".join(texts[word] for word in list(block_words(block))[:SNIPPET_WORDS])}
                           for block in layout["blocks"]]})
            ctx.record("layout_page", index=page["index"], blocks=len(layout["blocks"]), kinds=kinds,
                       columns=len(layout["columns"]), seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        ctx.output.write_json(LAYOUT_FILE, {"pages": summaries})


STEP = LayoutStep()
