"""Damaged edge band: the strip along a page's left or right edge where the scan cannot be trusted.

Near a binding shadow, a faded edge or an edge the scan cuts close to the print, Tesseract misreads words with
the same confidence (70-97) it gives clean print, so the confidence threshold alone leaves them silent. Problem
detection holds words in the band to a higher confidence (TRUSTED_CONFIDENCE).

All lengths are in the page's median line height h, measured from the raster edge inwards. A side's band is

    shadow  the inner end S of the last h/2 wide strip within MAX_SHADOW whose paper level (the 90th percentile
            of grey over the rows holding text) is below SHADOW_SHARE of the paper level of the page's central
            half; the band reaches SHADOW_REACH beyond it
    cut     when the print runs to the scan's edge (the 10th percentile of the distance of each line's outermost
            word is below CUT_MARGIN), the band reaches CUT_DEPTH beyond that margin

and a word lies in the band when its outer edge (its left edge on the left side) is closer to the edge.
"""
from __future__ import annotations

from pathlib import Path

import numpy

from .inkgaps import load_grey
from .pages import Matrix, apply_rect

# Words in the band below this Tesseract confidence (0-100) are doubtful readings.
TRUSTED_CONFIDENCE = 96.0
MAX_SHADOW = 8.0
SHADOW_SHARE = 0.95
SHADOW_REACH = 1.0
CUT_MARGIN = 2.0
CUT_DEPTH = 1.5


def band_words(recognised: dict, raster: Path, to_raster: Matrix) -> set[str]:
    """Ids of the page's read words that lie in a damaged edge band."""
    return words_in_bands(recognised, load_grey(raster), to_raster)


def words_in_bands(recognised: dict, grey: numpy.ndarray, to_raster: Matrix) -> set[str]:
    if not recognised["lines"]:
        return set()
    height = float(numpy.median([box[3] - box[1] for box in (apply_rect(to_raster, line["bbox"])
                                                             for line in recognised["lines"])]))
    lines = [[(word["id"], apply_rect(to_raster, word["bbox"])) for word in line["words"] if word["text"]]
             for line in recognised["lines"]]
    lines = [line for line in lines if line]
    if not lines:
        return set()
    width = grey.shape[1]
    boxes = [box for line in lines for _, box in line]
    rows = grey[max(0, int(min(box[1] for box in boxes))):int(max(box[3] for box in boxes)) + 1]
    if height < 1 or not rows.size:
        return set()
    paper = numpy.percentile(rows[:, width // 4:3 * width // 4], 90)
    left = _band(rows, height, paper, [line[0][1][0] for line in lines if len(line) >= 2])
    right = _band(rows[:, ::-1], height, paper, [width - line[-1][1][2] for line in lines if len(line) >= 2])
    return {word for line in lines for word, box in line if box[0] < left or width - box[2] < right}


def _band(rows: numpy.ndarray, height: float, paper: float, margins: list[float]) -> float:
    """How far from the edge (column 0 of rows) the band reaches, in pixels."""
    shadow = 0.0
    for strip in range(int(2 * MAX_SHADOW)):
        start, end = int(strip * height / 2), int((strip + 1) * height / 2)
        pixels = rows[:, start:end]
        if pixels.size and numpy.percentile(pixels, 90) < SHADOW_SHARE * paper:
            shadow = end
    band = shadow + SHADOW_REACH * height if shadow else 0.0
    margin = float(numpy.percentile(margins, 10)) if margins else None
    if margin is not None and margin < CUT_MARGIN * height:
        band = max(band, margin + CUT_DEPTH * height)
    return band
