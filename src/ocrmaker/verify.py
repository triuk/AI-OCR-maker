"""Export verifier: reads the text back out of a written OCR PDF and compares it with the planned text layer.

It checks the three things a reader of the OCR PDF relies on:
    order        the words come out in the planned order,
    outside_box  every word lies in its word box (as the page transform places it),
    text         every word copies back exactly as stored, diacritics included (compared in NFC).
Two more kinds cover words that cannot be paired at all: `missing` (a planned word that does not come out,
e.g. one with a zero-size box, which the exporter skips) and `extra` (text that comes out but was not planned).

The text is extracted the way a PDF reader copies it, with PyMuPDF's words in content stream order, so the
check sees what a user would get. Extracted words are paired with the planned ones first by the order and
text they share, then by position (the extracted word lies in the planned word's box), then by text alone;
a pair is out of order when it is not part of the longest run of pairs that keeps the planned order.

A finding is {"page" (0-based index), "word" (planned word id, None for extra text), "kind", "text"
(planned text or None), "extracted" (the text that came out or None), "bbox" (source page coordinates)}.
"""
from __future__ import annotations

import bisect
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

from .pages import apply_rect, invert, multiply, transform_matrix

KINDS = ("order", "outside_box", "text", "missing", "extra")
# How far (points) an extracted word may reach beyond its word box: extraction rounds glyph geometry.
TOLERANCE = 1.0


def verify_ocr_pdf(path: Path, pages: list[dict]) -> list[dict]:
    """Findings of an OCR PDF against the plan it was written from, page by page, in reading order.

    pages is what textlayer.write_ocr_pdf was given: [{"page": prepared page, "lines": [[{id, text, bbox}]]}].
    """
    findings = []
    with pymupdf.open(path) as document:
        for item in pages:
            findings += _verify_page(document[item["page"]["index"]], item["page"], item["lines"])
    return findings


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _placement(page: dict):
    """Source page coordinates -> coordinates of the words extracted from the written page, and back.

    The page transform turns the page content (ADR 0003) and a crop moves the origin to the new CropBox.
    """
    crop = (page.get("transform") or {}).get("crop")
    shift = [1, 0, 0, 1, -crop[0], -crop[1]] if crop else [1, 0, 0, 1, 0, 0]
    forward = multiply(transform_matrix(page), shift)
    return forward, invert(forward)


def _inside(rect, box, tolerance: float = 0.0) -> bool:
    return (rect[0] >= box[0] - tolerance and rect[1] >= box[1] - tolerance
            and rect[2] <= box[2] + tolerance and rect[3] <= box[3] + tolerance)


def _centre(rect) -> tuple[float, float]:
    return (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2


def _contains(box, point) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _keeping_order(pairs: list[tuple[int, int]]) -> set[tuple[int, int]]:
    """The longest run of (planned, extracted) pairs, taken in extracted order, whose planned indices rise."""
    pairs = sorted(pairs, key=lambda pair: pair[1])
    tails, tail_at, previous = [], [], [None] * len(pairs)
    for position, (planned, _) in enumerate(pairs):
        place = bisect.bisect_left(tails, planned)
        previous[position] = tail_at[place - 1] if place else None
        if place == len(tails):
            tails.append(planned)
            tail_at.append(position)
        else:
            tails[place], tail_at[place] = planned, position
    kept, position = set(), tail_at[-1] if tail_at else None
    while position is not None:
        kept.add(pairs[position])
        position = previous[position]
    return kept


def _verify_page(document_page: pymupdf.Page, page: dict, lines: list[list[dict]]) -> list[dict]:
    forward, backward = _placement(page)
    # A word whose text holds spaces comes out as several words; each must lie in the word's box.
    planned = [{"id": word["id"], "text": _nfc(token), "bbox": word["bbox"],
                "expected": apply_rect(forward, word["bbox"])}
               for line in lines for word in line for token in word["text"].split()]
    extracted = [{"text": _nfc(word[4]), "rect": list(word[:4])}
                 for word in document_page.get_text("words", sort=False)]
    pairs: dict[int, int] = {}  # planned index -> extracted index

    matcher = SequenceMatcher(None, [word["text"] for word in planned], [word["text"] for word in extracted],
                              autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            pairs[block.a + offset] = block.b + offset
    for by_position in (True, False):
        free = [index for index in range(len(planned)) if index not in pairs]
        taken = set(pairs.values())
        for number, word in enumerate(extracted):
            if number in taken:
                continue
            candidates = [index for index in free if index not in pairs and (
                _contains(planned[index]["expected"], _centre(word["rect"])) if by_position
                else planned[index]["text"] == word["text"])]
            if by_position:  # of several boxes around the word, prefer the one with the same text
                candidates.sort(key=lambda index: planned[index]["text"] != word["text"])
            if candidates:
                pairs[candidates[0]] = number

    def finding(kind, word=None, found=None):
        if word is None:
            bbox = [round(value, 2) for value in apply_rect(backward, found["rect"])]
        else:
            bbox = word["bbox"]
        return {"page": page["index"], "word": word and word["id"], "kind": kind,
                "text": word and word["text"], "extracted": found and found["text"], "bbox": bbox}

    in_order = _keeping_order(list(pairs.items()))
    findings = []
    for index, word in enumerate(planned):
        if index not in pairs:
            findings.append(finding("missing", word))
            continue
        found = extracted[pairs[index]]
        if (index, pairs[index]) not in in_order:
            findings.append(finding("order", word, found))
        if not _inside(found["rect"], word["expected"], TOLERANCE):
            findings.append(finding("outside_box", word, found))
        if found["text"] != word["text"]:
            findings.append(finding("text", word, found))
    paired = set(pairs.values())
    findings += [finding("extra", found=found) for number, found in enumerate(extracted) if number not in paired]
    return findings
