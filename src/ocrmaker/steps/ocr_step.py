"""Local OCR: Tesseract 5 reads every prepared working raster into lines, words and chars (ADR 0005).

Tesseract's automatic page segmentation can drop an isolated short line, typically a centred page number in
the footer. So uncovered line-like ink in the page's top and bottom bands (`inkgaps.uncovered_edge_lines`)
is read again as a single text line, and what Tesseract reads there with at least RECOVERED_CONFIDENCE
becomes a line of its own. It also drops whole regions it takes for pictures, typically text on a screened grey
background and sometimes a headline; so the page is read once more with every recognised line erased and the
paper evened out (`inkgaps.cleared_page`), and the lines read there with RECOVERED_CONFIDENCE are added too,
unless they are much lower than the page's lines: erasing a neighbouring line cuts off the tops or bottoms of
letters Tesseract missed, and what is read in such a sliver is a guess (it stays an unreadable word instead).
Then the ink gap finder adds empty word boxes for ink inside lines no word covers.

The result format is described in `ocrmaker.words`; all geometry is in source page coordinates.
"""
from __future__ import annotations

import time

from .. import inkgaps, tessdata, tesseract
from ..errors import StepError
from ..pages import PREPARED_STEP, read_pages
from ..step import ReportSection, Step, StepContext, StepResult
from ..words import OCR_FILE, page_file, page_words, read_page
from ..wording import on_pages


# A line read again (in an edge band or on the cleared page) is kept when its words reach this mean confidence and hold a letter or
# digit; anything less is likely an ornament or a smudge, not text.
RECOVERED_CONFIDENCE = 50.0
# A line read on the cleared page is kept only when at least this share of the page's median line height.
RECOVERED_HEIGHT = 0.6


def _with_lines(recognised: dict, found: list[list[dict]]) -> dict:
    """The page file with each word list in `found` that reads like text added as a line of its own."""
    lines = list(recognised["lines"])
    number = recognised["index"] + 1
    count = sum(len(line["words"]) for line in lines)
    for words in found:
        if not words or sum(word["confidence"] for word in words) / len(words) < RECOVERED_CONFIDENCE \
                or not any(char.isalnum() for word in words for char in word["text"]):
            continue
        words = [{**word, "id": f"p{number}-w{count + n}"} for n, word in enumerate(words, start=1)]
        count += len(words)
        boxes = [word["bbox"] for word in words]
        lines.append({"id": f"p{number}-l{len(lines) + 1}",
                      "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                               max(b[3] for b in boxes)],
                      "baseline": None, "block": -1, "paragraph": -1, "words": words})
    return {**recognised, "lines": lines}


def _recover_edge_lines(recognizer, recognised: dict, raster_path, raster: dict) -> dict:
    """The page file with the lines Tesseract dropped in the edge bands read again (see the module docstring)."""
    return _with_lines(recognised, [
        recognizer.recognize_line(raster_path, box, index=recognised["index"], dpi=sum(raster["dpi"]) / 2,
                                  to_source=raster["to_source"])
        for box in inkgaps.uncovered_edge_lines(recognised, raster_path, raster["to_raster"])])


def _recover_dropped_regions(recognizer, recognised: dict, raster_path, raster: dict) -> dict:
    """The page file with the lines read on the cleared page added (see the module docstring)."""
    again = recognizer.recognize_pixels(inkgaps.cleared_page(recognised, raster_path, raster["to_raster"]),
                                        index=recognised["index"], dpi=sum(raster["dpi"]) / 2,
                                        to_source=raster["to_source"])
    heights = sorted(line["bbox"][3] - line["bbox"][1] for line in recognised["lines"])
    least = RECOVERED_HEIGHT * heights[len(heights) // 2] if heights else 0
    return _with_lines(recognised, [line["words"] for line in again["lines"]
                                    if line["bbox"][3] - line["bbox"][1] >= least])


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
                recognised = _recover_edge_lines(recognizer, recognised, raster_path, raster)
                recognised = _recover_dropped_regions(recognizer, recognised, raster_path, raster)
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
            ctx.warn(f"Lokální OCR nerozpoznalo žádné slovo {on_pages(empty)}.")

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
