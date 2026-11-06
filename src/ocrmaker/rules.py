"""Printed rules: long thin horizontal lines on a working raster, such as the line under a table's column headings.

A rule is a horizontal run of ink at least RULE_LENGTH inches long and at most RULE_THICKNESS inches thick, with
paper right above and below it, so the edge of a photo or a dark bar is no rule. Ink is a pixel darker than the
page's paper by RULE_CONTRAST of the paper-to-ink contrast, so a faint rule still counts. Each pixel row is
OR-ed with its neighbours and runs are bridged across gaps of up to RULE_BRIDGE inches, so a slightly skewed or
broken rule stays one. Layout reads the rules of each page (`page_rules`, source page coordinates).
"""
from __future__ import annotations

from pathlib import Path

import numpy

from .inkgaps import _runs, load_grey
from .pages import apply_rect

RULE_LENGTH = 0.5      # inches
RULE_THICKNESS = 0.04  # inches
RULE_BRIDGE = 0.03     # inches
RULE_CONTRAST = 0.35   # share of the paper-to-ink contrast
PAPER_SHARE = 0.2      # a rule's neighbourhood above and below is paper: at most this share of it is ink


def find_rules(grey: numpy.ndarray, dpi: float) -> list[tuple[int, int, int, int]]:
    """The rules of a grey raster as boxes (x0, y0, x1, y1) in pixels, end exclusive."""
    paper, ink = numpy.median(grey), numpy.percentile(grey, 1)
    dark = grey < paper - RULE_CONTRAST * (paper - ink)
    near = dark.copy()
    near[1:] |= dark[:-1]
    near[:-1] |= dark[1:]
    length, bridge, thickness = RULE_LENGTH * dpi, RULE_BRIDGE * dpi, RULE_THICKNESS * dpi
    found: list[list[int]] = []
    for y in numpy.flatnonzero(near.sum(axis=1) >= length).tolist():
        runs: list[list[int]] = []
        for start, end in _runs(near[y]):
            if runs and start - runs[-1][1] <= bridge:
                runs[-1][1] = end
            else:
                runs.append([start, end])
        for start, end in runs:
            if end - start < length:
                continue
            # Rows of one rule overlap along most of the shorter run.
            same = next((rule for rule in found if y <= rule[3]
                         and min(end, rule[2]) - max(start, rule[0]) > 0.5 * min(end - start, rule[2] - rule[0])),
                        None)
            if same is None:
                found.append([start, y, end, y + 1])
            else:
                same[:] = [min(same[0], start), same[1], max(same[2], end), y + 1]
    # The OR with neighbouring rows widens a rule by a row on either side.
    margin = int(thickness) + 1
    rules = []
    for x0, y0, x1, y1 in found:
        if y1 - y0 > thickness + 2:
            continue
        above = dark[max(y0 - margin, 0):y0, x0:x1]
        below = dark[y1:y1 + margin, x0:x1]
        if any(side.size and side.mean() > PAPER_SHARE for side in (above, below)):
            continue
        rules.append((x0, y0 + 1, x1, y1 - 1))
    return rules


def page_rules(raster: Path, page_raster: dict) -> list[list[float]]:
    """The rules of a page's working raster (`pages` format) in source page coordinates."""
    dpi = sum(page_raster["dpi"]) / 2
    return [apply_rect(page_raster["to_source"], rule) for rule in find_rules(load_grey(raster), dpi)]
