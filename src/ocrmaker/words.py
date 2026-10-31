"""Recognised words: the format Local OCR stores and every later step (layout, problems, export) reads.

The Local OCR result (`steps/ocr/`) holds `ocr.json`:

    {"engine": {"name": "tesseract", "version": "5.5.3", "language": "ces", "model": "tessdata_best e2aad9b98303"},
     "pages": [{"index": 0, "file": "pages/page-0001.json", "words": 85, "mean_confidence": 93.4}]}

and one page file per page, `pages/page-0001.json`:

    {"index": 0,
     "lines": [{                               # Tesseract's lines in its recognition order
        "id": "p1-l1",
        "bbox": [x0, y0, x1, y1],              # source page coordinates (see ocrmaker.pages)
        "baseline": [[x, y], [x, y]],          # start and end point, source page coordinates
        "block": 0, "paragraph": 0,            # Tesseract's block and paragraph numbers on the page (hints)
        "words": [{
            "id": "p1-w1",                     # unique in the project, stable across later steps
            "text": "Obecní",                  # NFC; never whitespace only; "" only for an empty word box
            "bbox": [x0, y0, x1, y1],          # the word box, source page coordinates
            "confidence": 96.1,                # Tesseract's word confidence, 0-100
            "chars": [{"text": "O", "bbox": [...], "confidence": 99.6}, ...]}]}]}

Word and line boxes describe the page as displayed (text upright), mapped back into source page
coordinates, so on a page with /Rotate a word box is upright only after `pages.display_matrix`. Word order
within a line is left to right as displayed; the order of lines is Tesseract's and carries no meaning
beyond that (reading order is decided later). Whitespace-only boxes are dropped (ADR 0005).

Empty word boxes: ink inside a line that no recognised word covers becomes a word with text "", confidence
0.0, no chars and an id `p<page>-e<n>` (ocrmaker.inkgaps), in its line at its place in display order. Every
consumer must accept them; the exporter skips them until a later step gives them text. `ocr.json` counts
them apart: a page's "words" and "mean_confidence" cover only Tesseract's readings, "empty_words" the rest.
"""
from __future__ import annotations

from collections.abc import Iterator

OCR_STEP = "ocr"
OCR_FILE = "ocr.json"


def page_file(index: int) -> str:
    return f"pages/page-{index + 1:04d}.json"


def read_page(result, index: int) -> dict | None:
    """The recognised lines and words of one page (0-based index), or None for a page the result lacks."""
    name = page_file(index)
    return result.read_json(name) if result.exists(name) else None


def page_words(page: dict) -> Iterator[dict]:
    """Every word of a page file, line by line."""
    for line in page["lines"]:
        yield from line["words"]
