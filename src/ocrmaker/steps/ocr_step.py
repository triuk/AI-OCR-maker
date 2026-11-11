"""Local OCR: Tesseract 5 reads every prepared working raster into lines, words and chars (ADR 0005).

Tesseract's automatic page segmentation can drop an isolated short line, typically a centred page number in
the footer. So uncovered line-like ink in the page's top and bottom bands (`inkgaps.uncovered_edge_lines`)
is read again as a single text line, and what Tesseract reads there with at least RECOVERED_CONFIDENCE
becomes a line of its own. It also drops whole regions it takes for pictures, typically text on a screened grey
background and sometimes a headline; so the page is read once more with every recognised line erased and the
paper evened out (`inkgaps.cleared_page`), and the lines read there with RECOVERED_CONFIDENCE are added too,
unless they are much lower than the page's lines: erasing a neighbouring line cuts off the tops or bottoms of
letters Tesseract missed, and what is read in such a sliver is a guess (it stays an unreadable word instead).
Then words Tesseract merged over a narrow space ("19.2.2000" for "19. 2. 2000") are cut at it
(`inkgaps.split_narrow_spaces`), and the ink gap finder adds empty word boxes for ink inside lines no word covers.

On a page divided into layout regions (ADR 0006, the Layout regions result) each region is read like that on its
own, turned upright by its orientation (`recognise_regions`); ink outside every region is not read. The page is
also read whole, and what only that reading found inside an upright region is taken over (`with_page_words`).

The result format is described in `ocrmaker.words`; all geometry is in source page coordinates.
"""
from __future__ import annotations

import math
import tempfile
import time
from pathlib import Path

import numpy
import pymupdf

from .. import inkgaps, tessdata, tesseract
from ..errors import StepError
from ..pages import PREPARED_STEP, apply_rect, invert, multiply, read_pages, union, upright
from ..regions import divided_pages, region_degrees
from ..step import ReportSection, Step, StepContext, StepResult
from ..words import OCR_FILE, page_file, page_words, read_page
from ..wording import on_pages


# A line read again (in an edge band or on the cleared page) is kept when its words reach this mean confidence and hold a letter or
# digit; anything less is likely an ornament or a smudge, not text.
RECOVERED_CONFIDENCE = 50.0
# A line read on the cleared page is kept only when at least this share of the page's median line height.
RECOVERED_HEIGHT = 0.6
# The paper level of a region: this percentile of its grey values (most of a region is paper).
PAPER_PERCENTILE = 90
# A word of the whole page's reading is one the regions read too when their words cover this share of its box.
COVERED = 0.5
# A run of page words joins a region's line when the line spans this share of the run's height.
SAME_ROW = 0.5


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


def recognise_page(recognizer, page: dict, raster_path) -> dict:
    """The page file of a whole prepared page: Tesseract's reading with the lines it dropped read again, narrow
    spaces split and empty word boxes added (see the module docstring)."""
    raster = page["raster"]
    recognised = recognizer.recognize(raster_path, index=page["index"], dpi=sum(raster["dpi"]) / 2,
                                      to_source=raster["to_source"])
    recognised = _recover_edge_lines(recognizer, recognised, raster_path, raster)
    recognised = _recover_dropped_regions(recognizer, recognised, raster_path, raster)
    recognised = inkgaps.split_narrow_spaces(recognised, raster_path, raster["to_raster"], raster["to_source"])
    return inkgaps.find_empty_words(recognised, raster_path, raster["to_raster"], raster["to_source"])


def recognise_regions(recognizer, page: dict, raster_path, layout_regions: list[dict], workdir: Path) -> dict:
    """The page file of a page divided into layout regions: each region read as a page of its own
    (`recognise_page`) on the working raster with everything outside the region painted over with paper, turned
    so that the region's text is upright, and mapped back to source page coordinates. The whole page stays the
    canvas because Tesseract reads a small crop of it worse (its page analysis depends on the image size). Ink
    that an earlier region already holds is painted over too, so every word is read in exactly one region, and ink
    outside every region is never read. Every line names its region; lines follow the regions' order."""
    raster = page["raster"]
    grey = inkgaps.load_grey(raster_path)
    height, width = grey.shape
    lines, taken = [], []
    for number, region in enumerate(layout_regions, start=1):
        x0, y0, x1, y1 = apply_rect(raster["to_raster"], region["bbox"])
        x0, y0 = max(0, math.floor(x0)), max(0, math.floor(y0))
        x1, y1 = min(width, math.ceil(x1)), min(height, math.ceil(y1))
        if x1 <= x0 or y1 <= y0:
            continue
        own = grey[y0:y1, x0:x1]
        paper = int(numpy.percentile(own, PAPER_PERCENTILE))
        canvas = numpy.full_like(grey, paper)
        canvas[y0:y1, x0:x1] = own
        for a0, b0, a1, b1 in taken:
            canvas[b0:b1, a0:a1] = paper
        taken.append((x0, y0, x1, y1))
        degrees = region_degrees(region)
        turn, _, _ = upright([0, 0, width, height], degrees)
        pixels = numpy.ascontiguousarray(numpy.rot90(canvas, k=-degrees // 90))
        # turned raster pixel -> working raster pixel -> source page coordinates
        to_source = multiply(invert(turn), raster["to_source"])
        path = workdir / f"region-{number}.png"
        rows, columns = pixels.shape
        pymupdf.Pixmap(pymupdf.csGRAY, columns, rows, pixels.tobytes(), 0).save(path)
        turned = {"index": page["index"], "raster": {**raster, "width": columns, "height": rows,
                                                    "to_source": to_source, "to_raster": invert(to_source)}}
        lines += [{**line, "region": region["id"]} for line in recognise_page(recognizer, turned, path)["lines"]]
    lines = with_page_words(lines, recognise_page(recognizer, page, raster_path), layout_regions)
    return {"index": page["index"], "lines": _numbered(page["index"], lines)}


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def with_page_words(lines: list[dict], whole: dict, layout_regions: list[dict]) -> list[dict]:
    """The regions' lines with the words and empty word boxes only the whole page's reading (`whole`, a page
    file) found: a region alone on its canvas must not read less than the page did. A page word is taken over
    when its centre lies in an upright region (the first in reading order; turned text the page reading cannot
    read) and the regions' words cover less than COVERED of its box. A run of such words from one page line
    joins the region's line on whose row it lies, or becomes a line of its own after the region's lines. The
    regions' empty word boxes count as covering too, so a page word under one is not taken."""
    read = [word["bbox"] for line in lines for word in line["words"]]
    by_region = {region["id"]: [line for line in lines if line["region"] == region["id"]]
                 for region in layout_regions}
    added = {region["id"]: [] for region in layout_regions}

    def home(box) -> str | None:
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        region = next((region for region in layout_regions if region["bbox"][0] <= x <= region["bbox"][2]
                       and region["bbox"][1] <= y <= region["bbox"][3]), None)
        return region["id"] if region is not None and region_degrees(region) == 0 else None

    for page_line in whole["lines"]:
        runs: list[tuple[str | None, list[dict]]] = []
        for word in page_line["words"]:
            box = word["bbox"]
            area = (box[2] - box[0]) * (box[3] - box[1])
            region = home(box)
            if region is None or area <= 0 or sum(_overlap(box, other) for other in read) >= COVERED * area:
                runs.append((None, []))  # a word in between ends the run
            elif runs and runs[-1][0] == region:
                runs[-1][1].append(word)
            else:
                runs.append((region, [word]))
        for region, words in runs:
            if region is None:
                continue
            top, bottom = min(w["bbox"][1] for w in words), max(w["bbox"][3] for w in words)
            rows = [(min(bottom, line["bbox"][3]) - max(top, line["bbox"][1]), position)
                    for position, line in enumerate(by_region[region])]
            shared, position = max(rows, default=(0, None))
            if position is not None and shared >= SAME_ROW * (bottom - top):
                line = by_region[region][position]
                joined = sorted(line["words"] + words, key=lambda w: w["bbox"][0])
                by_region[region][position] = {**line, "words": joined, "bbox": union(word["bbox"] for word in joined)}
            else:
                added[region].append({**page_line, "region": region, "words": words, "bbox": union(word["bbox"] for word in words),
                                      "baseline": None})
    return [line for region in layout_regions for line in by_region[region["id"]] + added[region["id"]]]


def _numbered(index: int, lines: list[dict]) -> list[dict]:
    """The lines with line, word and empty word ids numbered anew on the page (regions are read apart)."""
    number, words, empty = index + 1, 0, 0
    numbered = []
    for position, line in enumerate(lines, start=1):
        renamed = []
        for word in line["words"]:
            if word["text"]:
                words += 1
                renamed.append({**word, "id": f"p{number}-w{words}"})
            else:
                empty += 1
                renamed.append({**word, "id": f"p{number}-e{empty}"})
        numbered.append({**line, "id": f"p{number}-l{position}", "words": renamed})
    return numbered


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
        divided = divided_pages(ctx)
        with tesseract.Recognizer(model, language) as recognizer, tempfile.TemporaryDirectory() as workdir:
            for page in pages:
                ctx.checkpoint()
                number = page["index"] + 1
                ctx.progress(page["index"], len(pages), f"Stránka {number} z {len(pages)}")
                started = time.monotonic()
                raster = page["raster"]
                raster_path = ctx.result(raster["step"]).path(raster["file"])
                if divided is None:
                    recognised = recognise_page(recognizer, page, raster_path)
                else:
                    recognised = recognise_regions(recognizer, page, raster_path, divided.get(page["index"], []),
                                                   Path(workdir))
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
