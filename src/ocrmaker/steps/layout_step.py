"""Layout: builds blocks (paragraphs in columns, headings, captions, running headers and footers, tables) from
the recognised lines and words, so that every word belongs to exactly one block (ADR 0005).

The result format is described in `ocrmaker.layout`.
"""
from __future__ import annotations

import time
from collections import Counter

from ..layout import LAYOUT_FILE, block_words, build_page, page_file
from ..pages import PREPARED_STEP, read_pages
from ..step import Step, StepContext
from ..words import OCR_STEP, page_words, read_page

SNIPPET_WORDS = 12


class LayoutStep(Step):
    id = "layout"

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
