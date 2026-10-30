"""Ink gap finder: printed ink inside text lines that no word box covers becomes empty word boxes.

Tesseract drops words it cannot read at all, typically blurred print near the binding or a page edge, and
sometimes boxes only part of a word. So that no printed word is silently lost, every line of a recognised
page is searched for ink that no word box covers, both between its words and beyond its ends (a missed word
at a line's start or end is the usual case). That ink is split into words at gaps as wide as the line's word
spacing and each piece becomes an empty word box: a word with text "" and confidence 0, placed in the line
in display order (see ocrmaker.words). Problem detection marks every empty word box as an unreadable word.

Everything is measured on the working raster, in pixels, relative to the line's height, so it does not
depend on resolution. Ink is a pixel clearly darker than the paper around it; the paper level is measured
locally, so a shaded gutter does not count as ink; underlines are ignored, and ink that continues above and
below the line (a rule, an image edge, the binding's shadow) is not a word.
"""
from __future__ import annotations

from pathlib import Path

import numpy
import pymupdf

from .pages import Matrix, apply_rect

# A pixel is ink when it is darker than the local paper by this share of the paper-to-darkest-ink contrast.
INK_CONTRAST = 0.2
# Fainter ink, down to this share, still joins letters: fading thins letters and widens the gaps between them.
JOIN_CONTRAST = 0.08
# Beyond a line's first and last word, uncovered ink is followed across gaps of up to this many line heights.
REACH = 1.2
# Word boxes are widened by this many line heights before uncovered ink is measured: blur spreads ink.
BOX_MARGIN = 0.12
# Ink narrower than this (line heights) is a speck, not a word; a one-letter word is wider.
MIN_WIDTH = 0.18
# Without recognised words to measure the spacing, ink pieces further apart than this are separate words.
DEFAULT_WORD_GAP = 0.2
# A horizontal run of ink this many line heights long is a rule or an underline, not part of a letter.
RULE_LENGTH = 2.0
# Ink filling more than this share of its box is solid (a bar, a bullet, a dark background), not letters.
MAX_FILL = 0.9
# Readings at least this confident (Tesseract, 0-100) measure the word spacing of a line.
TRUSTED_CONFIDENCE = 80
# Ink pieces closer than this share of the line's median word spacing belong to one word.
WORD_GAP_SHARE = 0.7


def _grey(raster: Path) -> numpy.ndarray:
    pixmap = pymupdf.Pixmap(str(raster))
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    if pixmap.n != 1:
        pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pixmap)
    return numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width)


def _runs(mask: numpy.ndarray) -> list[tuple[int, int]]:
    """Start and end (exclusive) of every run of True values."""
    padded = numpy.concatenate([[False], mask, [False]])
    changes = numpy.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(changes[::2].tolist(), changes[1::2].tolist()))


def _pixel_box(to_raster: Matrix, bbox) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = apply_rect(to_raster, bbox)
    return x0, y0, x1, y1


class _Page:
    """The ink mask of one working raster and the page's word boxes in raster pixels."""

    def __init__(self, grey: numpy.ndarray):
        self.grey = grey.astype(numpy.float32)
        self.height, self.width = grey.shape
        self.darkest = float(numpy.percentile(self.grey, 0.5))

    def ink(self, top: int, bottom: int, around: int, contrast: float = INK_CONTRAST) -> numpy.ndarray:
        """The ink mask of rows top..bottom, with the paper level measured over `around` extra rows each side."""
        rows = self.grey[max(0, top - around):min(self.height, bottom + around)]
        # Per column the paper is the light end of the rows around the line, smoothed along the line.
        paper = numpy.percentile(rows, 90, axis=0)
        window = max(1, around * 4)
        kernel = numpy.ones(window, dtype=numpy.float32) / window
        paper = numpy.convolve(numpy.pad(paper, window // 2, mode="edge"), kernel, mode="same")
        paper = paper[window // 2:window // 2 + self.width]
        limit = paper - contrast * numpy.maximum(paper - self.darkest, 0)
        return self.grey[top:bottom] < limit[None, :] - 1


def find_empty_words(recognised: dict, raster: Path, to_raster: Matrix, to_source: Matrix) -> dict:
    """The page file (ocrmaker.words) with empty word boxes added for uncovered ink inside its lines.

    The added words get ids `p<page>-e<n>`; the lines keep their order and grow to cover their new words.
    """
    page = _Page(_grey(raster))
    number = recognised["index"] + 1
    every_word = [_pixel_box(to_raster, word["bbox"]) for line in recognised["lines"] for word in line["words"]]
    # The page's word spacing relative to line height, for lines without enough trusted readings to measure.
    ratios = [space / (box[3] - box[1]) for line in recognised["lines"]
              for box in [_pixel_box(to_raster, line["bbox"])] if box[3] > box[1]
              for space in _spacings(line, to_raster)]
    page_ratio = sorted(ratios)[len(ratios) // 2] if len(ratios) >= 5 else None
    count = 0
    lines = []
    for line in recognised["lines"]:
        found = _line_gaps(page, line, every_word, to_raster, page_ratio)
        if not found:
            lines.append(line)
            continue
        words = [(_pixel_box(to_raster, word["bbox"])[0], word) for word in line["words"]]
        for box in found:
            count += 1
            words.append((box[0], {"id": f"p{number}-e{count}", "text": "",
                                   "bbox": [round(value, 2) for value in apply_rect(to_source, box)],
                                   "confidence": 0.0, "chars": []}))
        words.sort(key=lambda item: item[0])
        boxes = [word["bbox"] for _, word in words] + [line["bbox"]]
        bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                max(b[3] for b in boxes)]
        lines.append({**line, "bbox": bbox, "words": [word for _, word in words]})
    return {**recognised, "lines": lines}


def _line_gaps(page: _Page, line: dict, every_word, to_raster: Matrix, page_ratio: float | None) -> list[tuple]:
    """Boxes (raster pixels) of the uncovered ink pieces of one line, split into words."""
    x0, y0, x1, y1 = _pixel_box(to_raster, line["bbox"])
    own = [_pixel_box(to_raster, word["bbox"]) for word in line["words"]]
    height = y1 - y0
    if height < 4:
        return []
    top, bottom = int(y0), int(numpy.ceil(y1))
    # Underlines and rules join the words they run under; letters never draw a row of ink that long.
    rule = RULE_LENGTH * height
    ink = _erase_rules(page.ink(top, bottom, around=max(2, round(height / 2))), rule)
    # The core rows (the middle half of the line) carry ink of every letter; ascents and accents may not.
    core = ink[round(height / 4):max(round(height / 4) + 1, round(height * 3 / 4))]
    inked = core.sum(axis=0) >= 2
    faint = _erase_rules(page.ink(top, bottom, around=max(2, round(height / 2)), contrast=JOIN_CONTRAST), rule)
    joined = faint[round(height / 4):max(round(height / 4) + 1, round(height * 3 / 4))].sum(axis=0) >= 2
    margin = BOX_MARGIN * height
    covered = numpy.zeros(page.width, dtype=bool)
    blocked = numpy.zeros(page.width, dtype=bool)  # words of other lines (another column) end the search
    middle = (y0 + y1) / 2
    for box in every_word:
        if box[1] <= middle <= box[3] or y0 <= (box[1] + box[3]) / 2 <= y1:
            start, end = max(0, int(box[0] - margin)), min(page.width, int(numpy.ceil(box[2] + margin)))
            covered[start:end] = True
            if box not in own:
                blocked[start:end] = True
    # Beyond the line's ends, ink pieces are followed outwards while each lies within reach of the last one,
    # and the search stops at another line's word (the next column).
    lo, hi = int(x0), int(numpy.ceil(x1))
    blocked_left = numpy.flatnonzero(blocked[:lo])
    left = int(blocked_left[-1]) + 1 if blocked_left.size else 0
    blocked_right = numpy.flatnonzero(blocked[hi:])
    right = hi + int(blocked_right[0]) if blocked_right.size else page.width
    free = inked & ~covered
    free[:left] = False
    free[right:] = False
    reach = REACH * height
    pieces = []
    edge = lo
    for start, end in reversed(_runs(free[:lo])):
        if edge - end > reach:
            break
        pieces.insert(0, (start, end))
        edge = start
    inside = [(lo + start, lo + end) for start, end in _runs(free[lo:hi])]
    if pieces and inside and inside[0][0] == lo:  # a piece crossing the line's start
        pieces[-1] = (pieces[-1][0], inside.pop(0)[1])
    pieces += inside
    edge, after = hi, []
    for start, end in _runs(free[hi:]):
        start, end = hi + start, hi + end
        if start - edge > reach:
            break
        after.append((start, end))
        edge = end
    if pieces and after and after[0][0] == hi and pieces[-1][1] == hi:
        pieces[-1] = (pieces[-1][0], after.pop(0)[1])
    pieces += after
    if not pieces:
        return []
    gap = _word_gap(_spacings(line, to_raster), height, page_ratio)
    words, current = [], list(pieces[0])
    for start, end in pieces[1:]:
        if _longest_gap(joined[current[1]:start]) < gap and not covered[current[1]:start].any():
            current[1] = end
        else:
            words.append(current)
            current = [start, end]
    words.append(current)
    found = []
    for start, end in words:
        if end - start < MIN_WIDTH * height:
            continue
        rows = numpy.flatnonzero(ink[:, start:end].any(axis=1))
        if rows.size == 0 or _continues_vertically(page, top, bottom, start, end, height):
            continue
        if ink[rows[0]:rows[-1] + 1, start:end].mean() > MAX_FILL:
            continue  # solid ink: a bullet, a bar, or the background around inverted (light on dark) text
        found.append((float(start), float(top + rows[0]), float(end), float(top + rows[-1] + 1)))
    return found


def _erase_rules(mask: numpy.ndarray, length: float) -> numpy.ndarray:
    """The mask without horizontal runs of ink at least `length` pixels long."""
    for row in numpy.flatnonzero(mask.sum(axis=1) >= length):
        for start, end in _runs(mask[row]):
            if end - start >= length:
                mask[row, start:end] = False
    return mask


def _longest_gap(inked: numpy.ndarray) -> int:
    runs = _runs(~inked)
    return max((end - start for start, end in runs), default=0)


def _spacings(line: dict, to_raster: Matrix) -> list[float]:
    """The spaces (pixels) between neighbouring trusted readings of a line; garbage readings say nothing."""
    boxes = [_pixel_box(to_raster, word["bbox"]) if word["confidence"] >= TRUSTED_CONFIDENCE else None
             for word in line["words"]]
    return [b[0] - a[2] for a, b in zip(boxes, boxes[1:]) if a and b and b[0] > a[2]]


def _word_gap(spaces: list[float], height: float, page_ratio: float | None) -> float:
    """Ink pieces at least this far apart (pixels) are separate words: most of the usual word spacing.

    The line's own spacing is used when it has trusted readings to measure, else the page's relative to the
    line height. Faded ink thins at the ink threshold, which widens the gaps between letters, so the share
    is large."""
    if len(spaces) >= 2:
        spacing = sorted(spaces)[len(spaces) // 2]
    else:
        spacing = (page_ratio or DEFAULT_WORD_GAP / WORD_GAP_SHARE) * height
    return max(spacing * WORD_GAP_SHARE, 0.08 * height)


def _continues_vertically(page: _Page, top: int, bottom: int, start: int, end: int, height: float) -> bool:
    """Whether the ink runs on above and below the line, as a rule, an image edge or a shadow does."""
    span = max(2, round(height / 3))
    above = page.ink(max(0, top - span), top, around=span) if top > 0 else None
    below = page.ink(bottom, min(page.height, bottom + span), around=span) if bottom < page.height else None
    if above is None or below is None or not above.size or not below.size:
        return False
    return bool(above[:, start:end].all(axis=0).mean() > 0.5 and below[:, start:end].all(axis=0).mean() > 0.5)
