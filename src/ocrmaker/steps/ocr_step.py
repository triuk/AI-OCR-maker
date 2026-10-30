"""Local OCR: Tesseract 5 reads every prepared working raster into lines, words and chars (ADR 0005).

The result format is described in `ocrmaker.words`; all geometry is in source page coordinates.
"""
from __future__ import annotations

import time

from .. import inkgaps, tessdata, tesseract
from ..errors import StepError
from ..pages import PREPARED_STEP, read_pages
from ..step import ReportSection, Step, StepContext, StepResult
from ..words import OCR_FILE, page_file, page_words, read_page


def _page_list(numbers: list[int]) -> str:
    shown = ", ".join(str(number) for number in numbers[:10])
    return shown + (" …" if len(numbers) > 10 else "")


class OcrStep(Step):
    id = "ocr"

    def run(self, ctx: StepContext) -> None:
        pages = read_pages(ctx.result(PREPARED_STEP))
        language = ctx.settings["language"]
        try:
            model = tessdata.ensure_model(language)
        except tessdata.ModelError as exc:  # already a Czech message
            raise StepError(str(exc)) from exc
        summaries, empty = [], []
        with tesseract.Recognizer(model, language) as recognizer:
            for page in pages:
                ctx.checkpoint()
                number = page["index"] + 1
                ctx.progress(page["index"], len(pages), f"Stránka {number} z {len(pages)}")
                started = time.monotonic()
                raster = page["raster"]
                raster_path = ctx.result(raster["step"]).path(raster["file"])
                recognised = recognizer.recognize(raster_path, index=page["index"], dpi=sum(raster["dpi"]) / 2,
                                                  to_source=raster["to_source"])
                recognised = inkgaps.find_empty_words(recognised, raster_path, raster["to_raster"],
                                                      raster["to_source"])
                ctx.output.write_json(page_file(page["index"]), recognised)
                # Word count and mean confidence describe Tesseract's readings; empty word boxes are counted apart.
                confidences = [word["confidence"] for word in page_words(recognised) if word["text"]]
                summary = {"index": page["index"], "file": page_file(page["index"]), "words": len(confidences),
                           "empty_words": sum(1 for word in page_words(recognised) if not word["text"]),
                           "mean_confidence": round(sum(confidences) / len(confidences), 2) if confidences else None}
                summaries.append(summary)
                if not confidences:
                    empty.append(number)
                ctx.record("ocr_page", **summary, lines=len(recognised["lines"]),
                           seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        ctx.output.write_json(OCR_FILE, {
            "engine": {"name": "tesseract", "version": tesseract.version(), "language": language,
                       "model": f"tessdata_best {tessdata.COMMIT[:12]}"},
            "pages": summaries})
        if empty:
            where = "na straně" if len(empty) == 1 else "na stranách"
            ctx.warn(f"Lokální OCR nerozpoznalo žádné slovo {where} {_page_list(empty)}.")

    def overlay(self, result: StepResult, page: int) -> dict | None:
        """Word boxes with text and confidence, in source page coordinates."""
        recognised = read_page(result, page)
        if recognised is None:
            return None
        return {"words": [{key: word[key] for key in ("id", "text", "bbox", "confidence")}
                          for word in page_words(recognised)],
                "lines": [line["bbox"] for line in recognised["lines"]]}

    def report(self, result: StepResult) -> ReportSection:
        """Word count and mean word confidence per page and for the document (weighted by words)."""
        recognised = result.read_json(OCR_FILE)
        pages = [{"index": page["index"], "words": page["words"], "empty_words": page.get("empty_words", 0),
                  "mean_confidence": page["mean_confidence"]} for page in recognised["pages"]]
        words = sum(page["words"] for page in pages)
        mean = round(sum(page["words"] * page["mean_confidence"] for page in pages if page["words"]) / words, 2) \
            if words else None
        engine = recognised["engine"]
        empty = sum(page["empty_words"] for page in pages)
        summary = [f"{words} words on {len(pages)} pages, mean confidence {mean} "
                   f"({engine['name']} {engine['version']}, {engine['language']}); "
                   f"{empty} empty word boxes for ink without a reading.", ""]
        shown = pages
        if len(pages) > REPORTED_PAGES:  # pages without words first, then by rising confidence
            shown = sorted(pages, key=lambda page: (page["mean_confidence"] is not None,
                                                    page["mean_confidence"] or 0, page["index"]))
            shown = shown[:REPORTED_PAGES // 2]
            summary += [f"The {len(shown)} pages with the lowest mean confidence:", ""]
        summary += ["| Page | Words | Mean confidence |", "|---|---|---|"]
        for page in shown:
            confidence = "" if page["mean_confidence"] is None else page["mean_confidence"]
            summary.append(f"| {page['index'] + 1} | {page['words']} | {confidence} |")
        return ReportSection("Local OCR", {"engine": engine, "words": words, "empty_words": empty,
                                           "mean_confidence": mean, "pages": pages}, summary)


# Up to this many pages the summary lists each page; above it only the weakest half of this number.
REPORTED_PAGES = 20

STEP = OcrStep()
