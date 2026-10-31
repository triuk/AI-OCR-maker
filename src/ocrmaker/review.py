"""Review service: what manual review shows, and the manual corrections it records (ADR 0004).

Everything here reads the committed results of the automatic steps through `ctx` (anything with
`has_result(step id)` and `result(step id)`) plus the project's manual corrections (`ocrmaker.corrections`);
nothing here runs a step or writes a result. The text is the one Export would write: Local OCR's words with
the reconstruction records of every record step (`ocrmaker.transcription`, so Web lookup's records appear
here as soon as it writes them) and the manual corrections applied, in reading order.

Queue (`queue`): one item per illegible marker, i.e. per reconstruction record in force with status
"unresolved", page by page in reading order:

    {"id": "p1-w30",                 # the group's first OCR word id: what `correction` and the crop take
     "page": 0, "ids": ["p1-w30"],   # 0-based page index; the OCR word ids the marker stands for
     "status": "unresolved", "reason": "low_confidence", "source": "ai",   # from the record
     "text": "[NEČITELNÉ]",          # what the export writes there now (a correction's text once corrected)
     "hint": "čtv",                  # the partial reading (Local OCR's text of the ids), "" for an empty box
     "candidate": "čtvrtek" | null,  # the last automatic attempt's words, joined
     "confidence": 40.0 | null, "bbox": [x0, y0, x1, y1],                  # the group's box, source page coords
     "context": {"before": "... Ve", "after": "proběhne beseda ..."},       # the neighbouring written words
     "correction": {...} | null}     # the manual correction made there, if any

Page view (`page_view`): a page's text in reading order, every written word with the OCR word id a
correction of it takes (`target`) and where its text comes from (`origin`: "ocr", "ai", "publisher", "web",
"marker" or "manual").
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import corrections as layer
from . import inkgaps, transcription
from .pages import PREPARED_STEP, apply_rect, read_pages
from .reconstruction import PUBLISHER_ERROR, UNRESOLVED, render_crop
from .steps.export_step import reading_articles, reading_order
from .words import OCR_STEP, read_page

# Written words shown before and after a queue item.
CONTEXT_WORDS = 12


class Page:
    """One page as manual review sees it: the records in force, the words written, the reading sequence."""

    def __init__(self, ctx, index: int, corrections: list[dict], order_pages: dict | None = None):
        self.index = index
        self.recognised = read_page(ctx.result(OCR_STEP), index) or {"index": index, "lines": []}
        self.ocr = {word["id"]: word for line in self.recognised["lines"] for word in line["words"]}
        self.records = transcription.records(ctx, index)
        self.record_of = {word_id: record for record in self.records for word_id in record["ids"]}
        self.corrections = layer.text_corrections(corrections, index)
        self.correction_of = {word_id: item for item in self.corrections for word_id in item["ids"]}
        self.automatic = transcription.page_words(ctx, self.recognised)
        self.words = layer.apply(self.automatic, self.recognised, corrections)
        order_pages = reading_order(ctx)[0] if order_pages is None else order_pages
        self.articles = reading_articles(ctx, self.recognised, order_pages)
        self.sequence = [word_id for article in self.articles for block in article["blocks"]
                         for line in block["lines"] for word_id in line]
        self.position = {word_id: number for number, word_id in enumerate(self.sequence)}

    def group(self, word_id: str) -> list[str]:
        """The OCR word ids a correction of this OCR word covers: its record's whole group, or itself."""
        record = self.record_of.get(word_id)
        return [i for i in record["ids"] if i in self.ocr] if record else [word_id]

    def target(self, word_id: str) -> str:
        """The OCR word id behind a written word id (a split part `<id>-N` included); KeyError if none."""
        if word_id in self.ocr:
            return word_id
        for record in self.records:
            if any(word["id"] == word_id for word in record["words"]):
                return record["ids"][0]
        for item in self.corrections:
            if any(word["id"] == word_id for word in self.words.get(item["ids"][0], [])):
                return item["ids"][0]
        raise KeyError("Slovo na stránce neexistuje.")

    def text(self, ids: list[str], words: dict[str, list[dict]] | None = None) -> str:
        return " ".join(word["text"] for word in transcription.expand(words or self.words, ids) if word["text"])

    def origin(self, word_id: str) -> str:
        if word_id in self.correction_of:
            return "manual"
        record = self.record_of.get(word_id)
        if record is None:
            return "ocr"
        if record["status"] == UNRESOLVED:
            return "marker"
        if record.get("source") == "web":
            return "web"
        return "publisher" if record["status"] == PUBLISHER_ERROR else "ai"

    def box(self, ids: list[str]) -> list[float]:
        return layer.union(self.ocr[word_id]["bbox"] for word_id in ids)

    def context(self, ids: list[str]) -> dict:
        positions = sorted(self.position[word_id] for word_id in ids if word_id in self.position)
        if not positions:
            return {"before": "", "after": ""}
        before = [word["text"] for word in transcription.expand(self.words, self.sequence[:positions[0]])
                  if word["text"]]
        after = [word["text"] for word in transcription.expand(self.words, self.sequence[positions[-1] + 1:])
                 if word["text"]]
        return {"before": " ".join(before[-CONTEXT_WORDS:]), "after": " ".join(after[:CONTEXT_WORDS])}


def page_count(ctx) -> int:
    return len(read_pages(ctx.result(PREPARED_STEP))) if ctx.has_result(PREPARED_STEP) else 0


def queue(ctx, corrections: list[dict]) -> dict:
    """The review queue: every illegible marker in force (see the module docstring), and its counts."""
    items = []
    if ctx.has_result(OCR_STEP):
        order_pages = reading_order(ctx)[0]
        for index in range(page_count(ctx)):
            page = Page(ctx, index, corrections, order_pages)
            found = [_item(page, record) for record in page.records if record["status"] == UNRESOLVED]
            items += sorted(found, key=lambda item: min((page.position.get(i, len(page.sequence))
                                                         for i in item["ids"]), default=0))
    corrected = sum(item["correction"] is not None for item in items)
    return {"items": items, "counts": {"markers": len(items), "corrected": corrected,
                                       "remaining": len(items) - corrected,
                                       "corrections": len(layer.text_corrections(corrections))}}


def _item(page: Page, record: dict) -> dict:
    ids = [word_id for word_id in record["ids"] if word_id in page.ocr]
    correction = next((page.correction_of[word_id] for word_id in ids if word_id in page.correction_of), None)
    return {"id": ids[0], "page": page.index, "ids": ids, "status": record["status"], "reason": record.get("reason"),
            "source": record.get("source"), "text": page.text(ids),
            "hint": " ".join(page.ocr[word_id]["text"] for word_id in ids if page.ocr[word_id]["text"]),
            "candidate": " ".join(record["candidate"]) if record.get("candidate") else None,
            "confidence": record.get("confidence"), "bbox": page.box(ids), "context": page.context(ids),
            "correction": correction}


def page_view(ctx, index: int, corrections: list[dict]) -> dict:
    """A page's written text in reading order: articles, blocks, lines of words (see the module docstring)."""
    if not 0 <= index < page_count(ctx):
        raise KeyError("Stránka neexistuje.")
    page = Page(ctx, index, corrections)

    def words(line: list[str]) -> list[dict]:
        shown = []
        for word_id in line:
            target = page.group(word_id)[0]
            record = page.record_of.get(word_id)
            confidence = record.get("confidence") if record else page.ocr[word_id]["confidence"]
            shown += [{"id": word["id"], "target": target, "text": word["text"], "bbox": word["bbox"],
                       "origin": page.origin(word_id), "confidence": confidence}
                      for word in page.words.get(word_id, [])]
        return shown

    return {"index": index, "articles": [
        {"id": article["id"], "kind": article["kind"], "blocks": [
            {"id": block["id"], "kind": block["kind"], "lines": [shown for line in block["lines"]
                                                                 if (shown := words(line))]}
            for block in article["blocks"]]} for article in page.articles]}


def correction(ctx, index: int, word_id: str, text: str, corrections: list[dict]) -> dict:
    """What a text correction of a written word would store: its group of OCR ids, its text and what the
    automatic steps wrote there (see ocrmaker.corrections). KeyError for a word not on the page."""
    if not 0 <= index < page_count(ctx):
        raise KeyError("Stránka neexistuje.")
    if not isinstance(text, str):
        raise ValueError("Opravený text musí být text.")
    page = Page(ctx, index, corrections)
    ids = page.group(page.target(word_id))
    return {"kind": layer.TEXT, "page": index, "ids": ids, "text": " ".join(text.split()),
            "previous": page.text(ids, page.automatic)}


def crop(ctx, index: int, word_id: str) -> bytes:
    """A PNG of the working raster around a word's group: its line with a line's height above and below,
    the group outlined in red (as AI reconstruction's crops)."""
    if not 0 <= index < page_count(ctx):
        raise KeyError("Stránka neexistuje.")
    geometry = read_pages(ctx.result(PREPARED_STEP))[index]
    page = Page(ctx, index, [])
    ids = page.group(page.target(word_id))
    line = next(line for line in page.recognised["lines"] if any(word["id"] == ids[0] for word in line["words"]))
    raster = geometry["raster"]
    x0, y0, x1, y1 = apply_rect(raster["to_raster"], line["bbox"])
    height = max(1.0, y1 - y0)
    region = apply_rect(raster["to_source"], [x0, y0 - height, x1, y1 + height])
    ink = inkgaps.load_ink(ctx.result(raster["step"]).path(raster["file"]))
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "crop.png"
        render_crop(ink, raster["to_raster"], region, [page.box(ids)], height, target)
        return target.read_bytes()

