"""Layout regions: the AI divides a page into its articles in reading order, one rectangle per column (ADR 0006).

Method v8 of the regions prototype (`benchmark/prototypes/regions/regions3.py`), per page:
  1. one AI request: the page preview (the working raster, longer side LONG_SIDE px) with a faint labelled grid in
     0..1000 units; the answer lists the page's articles of kind article, header, footer or ad in reading order,
     each with regions (`box` in grid units, `orientation` 0/90/180/270/stacked). An answer in image pixels
     (a coordinate above PIXEL_LIMIT) is converted to the grid (`grid_units`);
  2. snap: every region becomes the bounding box of the ink it owns (`Ink`, `snap`; see the ink section below),
     and two overlapping boxes whose ink lies apart along one axis share an edge (`separate`);
  3. settle: regions without a recognised word (a rule, an ornament, a lone picture) are dropped;
  4. check against the words of a whole-page Tesseract pass used only for checking: overlaps, words in no or
     several regions, a region edge through a text line, text-like ink in no region (`spots`), two columns in one
     region; words read off decoration and noise (scan edge, show-through) do not count, except a row of them in
     no region that reads as a line of text (`lost_lines`: handwriting-like print, a line joined to a drawing);
  5. at most one corrective request with those problems (previous answer = the snapped boxes); the corrected
     answer is kept unless it has more problems.

A later layout round (`redo_page`) asks again with the person's layout of the page (`ocrmaker.layout_review`):
every region labelled as the person sees it ("3b") and locked or not, plus the layout note. The answer lists the
whole page again and names in "keep" each current region it keeps; a locked region is taken over exactly (page box,
orientation, its part's kind, title and continuation), never snapped and never dropped, and it keeps its order and
part (`resolve`). An answer that breaks a lock gets the corrective request too; one that still breaks it is refused.
Approval (`drawn_page`) takes the person's layout as drawn: every region fixed, only checked.

Geometry: boxes are kept in grid units of the page (0..1000 along each side of the source page extent) and in
source page coordinates (`pt`). Pages with a /Rotate other than 0 are not handled specially yet.

The Layout regions result (`steps/regions/`, see `ocrmaker.steps.regions_step`) holds per page
    {"index": 0, "method": "regions", "source": "ai" | "failed" | "manual", "reason": str | None,
     "round": 1,                              # the layout round that last laid the page out
     "note": str | None,                      # the layout note sent with the page in that round
     "regions": [{"id": "p1-r1", "article": 1, "column": 1, "continues": false,    # continues the previous region's article
                  "part": 1,                                                         # the part as the AI or person gave it
                  "kind": "article", "title": "...", "orientation": "0",
                  "box": [gx0, gy0, gx1, gy1],                                      # grid units
                  "bbox": [x0, y0, x1, y1]}],                                       # source page coordinates
     "proposal": [regions],                   # only on a page approved as the person drew it ("manual"): the
                                              # AI's regions of that round, as above
     "spots": [[x0, y0, x1, y1]],             # text-like ink in no region, source page coordinates
     "check": {...},                          # the final check's counts and problems (English)
     "requests": int, "chosen": "first" | "fixed" | None}
Several parts form one article when a part continues the previous one (the person's "J"); `article` numbers the
articles, `part` the parts, `column` counts the regions of an article.
"""
from __future__ import annotations

import bisect
import copy
import statistics
from pathlib import Path

import numpy
import pymupdf

from .inkgaps import _square, load_grey
from .pages import apply_rect, multiply

REGIONS_STEP = "regions"
REGIONS_FILE = "regions.json"
PURPOSE = "layout_regions"
KINDS = ("article", "header", "footer", "ad")
ORIENTATIONS = ("0", "90", "180", "270", "stacked")

SCALE = 1000
PIXEL_LIMIT = 1010     # an answer with a box coordinate above this is in image pixels, not grid units
LONG_SIDE = 1600

# tolerances, in text heights (median height of the page's recognised words) unless noted
SHARE = 0.5            # a blob belongs to the region holding at least this share of its pixels
ROW_GAP = 0.9          # a blob joins a region's text line across a gap up to this wide
LINE_GAP = 0.7         # blobs closer than this along a row are one text line, owned as a whole
COLUMN_GAP = 0.6       # ... or the region's column across a gap up to this high (a line just below/above)
PAD = 0.15             # snapped boxes keep this margin around their ink
TOUCH = 0.6            # overlapping boxes whose ink overlaps at most this much along one axis share an edge
MIN_BLOB = 0.1         # ink blobs with fewer pixels than this share of a text height squared are fragments
CLUSTER_GAP = 1.2      # uncovered text blobs this close form one spot
GLYPH_THIN = 0.2       # uncovered shapes thinner than this are line fragments, not letters
SPOT_STRENGTH = 0.6    # an uncovered spot's darkest ink reaches this share of the paper-to-darkest-ink contrast
GLYPH_FILL = 0.12      # ... and shapes whose ink fills less of their box are ornaments
LINE_THICK = 1.0       # an uncovered single shape thinner than this and longer than RULE_LONG is a printed line
EDGE_NOISE = 0.04      # inches: a word box this close to the raster edge is scan-edge noise
NOISE_INK = 0.03       # a word box with less ink than this share of its area is noise (show-through, shadow)
GUTTER = 0.8           # a column gutter is at least this wide
COLUMN_ROWS = 4        # each side of a gutter holds at least this many text rows
COLUMN_SPAN = 0.5      # and the gutter runs through at least this share of the region's text height
MIN_LINE = 10          # a running-text row holds a run of words at least this long; table cells are shorter
RUNNING = 0.6          # a side is running text when this share of its rows is such a line
SEGMENT_GAP = 1.2      # words closer than this form one segment (a table cell, a text line; justified and
                       # letter-spaced lines stretch word gaps to about one text height)
DECOR_CONFIDENCE = 85  # OCR confidence (0..100) under which a word over decoration ink is noise; on the test
                       # pages print in frames and headlines reads 91-97, guesses over drawings and ornaments 0-68
LOST_GAP = 1.5         # words of a lost line lie at most this many text heights apart (a word recognition skipped)
LOST_WORDS = 3         # a lost line holds at least this many long words, and they are at least half its words; on
                       # the test pages a lost cursive line has 6 of 8, the fragments read off a dash-dot rule 3 of 28
WORD_LETTERS = 3       # a long word has at least this many letters


INSTRUCTIONS = """\
You divide one scanned printed page (often a Czech newsletter or magazine page) into regions for text \
recognition, which reads each region on its own, line by line from top to bottom. The image carries a faint \
grid: x and y run from 0 (left, top) to 1000 (right, bottom), with grid lines and labels every 100. Every box \
is [x0, y0, x1, y1] in these units.

Terms
- Part: one logical unit of the page, of one "kind": "article" (a report, notice, invitation, programme or \
announcement, framed or not, with its heading, text, signature and picture captions), "header" (running \
header), "footer" (running footer) or "ad" (a paid advertisement: a firm offering goods or services).
- Region: a rectangle that a reader reads straight down, line after line: one column of one part. Every text \
line inside a region belongs to that column alone.

Steps
1. Find the parts and order them as a reader reads the page: running headers first, then the articles, then \
the advertisements, running footers last. "title" is the part's heading or first words, as printed.
   - Read the parts like newspaper columns: all parts in the leftmost column from top to bottom, then all parts \
in the next column to the right, and so on; never zigzag between columns by where the parts start. Boxes or \
parts side by side are columns too, also when one is a little wider than the other.
   - A part of its own (another article, not the heading of the one above) that spans two or more columns of \
text, as a headline, box or table, splits the page into bands: first ALL columns above it are read to their \
end (the whole left column, then the one to its right, also a column that starts above it and runs on down \
beside it), then that wide part with its own columns, then the columns below it, again from the leftmost. \
A heading inside one article (its title over some of its columns, or a subheading in its middle) never \
splits the page: it is read with its own article. The advertisements follow the same order among themselves.
   - A section heading over a group of advertisements or short notices (such as "INZERCE", "INZERÁTY", \
"INFORMACE", "OZNÁMENÍ") is not a part of its own: it is the first region of the first part it introduces, \
and the parts of that group follow one another right after it.
2. Cut each part into regions, listed in reading order:
   - Text set in two or more columns is one region per column, left column first; a heading that spans the \
columns is a region of its own above them. This holds in articles, advertisements and boxes alike, also when \
a column holds a list or a timetable.
   - A table, read row by row across its columns (a column-heading row, short cells sharing baselines), is \
one region as wide as the table. A list of dates or times with each entry's text on the same line beside it \
(a programme, a calendar of events) is such a table too: never cut the dates off into a region of their own. \
But two such lists side by side, each with its own heading (one month or one place per column) and lines \
that do not share baselines, are two columns: one region each, never one region across both.
   - Where a column runs beside another part for only part of its height, split the column: a full-width \
region above (or below) and a narrow region beside the other part, so that each rectangle holds its own part \
only. A part that is not a rectangle (L-shaped, notched) takes several rectangles.
   - A small advertisement or framed box set in one column is one region covering its whole frame, pictures, \
logos and decorative lettering included.
   - Lettering that runs vertically or upside down (rotated lines, upright letters stacked one above another) \
belongs to its part: include it in a region of that part, a region of its own when it runs beside the part's \
text.
3. Draw each box around all of its region's ink with a small margin of paper: the full width and height of \
every line and letter, headline letters and signatures included. Edges run through empty paper or along \
printed rules.
4. Give each region its "orientation": the clockwise turn that makes its text read normally. "0" for normal \
lines; "90" for lines running bottom to top; "270" for lines running top to bottom; "180" for upside-down text; \
"stacked" for upright letters stacked one above another.
5. Check before answering: every piece of printed text lies in exactly one region; no two regions overlap; \
pictures and empty paper lie outside the regions, except inside a framed box that is one region.

Correcting
When the data holds "previous_answer" and "problems", a program has compared that answer with the words \
that text recognition found and with the ink on the page (coordinates in the same grid). Return the whole \
corrected answer: keep the parts, their order and every region without a problem; fix every listed problem, \
looking at the image to decide which part each listed word or mark belongs to. Leave a listed word or mark \
outside all regions only when it is noise: a smudge, the scan's edge, show-through from the other side.

Answer only with JSON matching the schema."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["parts"],
    "properties": {"parts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["kind", "title", "regions"],
        "properties": {
            "kind": {"type": "string", "enum": ["article", "header", "footer", "ad"]},
            "title": {"type": "string"},
            "regions": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["box", "orientation"],
                "properties": {"box": {"type": "array", "items": {"type": "integer"},
                                       "minItems": 4, "maxItems": 4},
                               "orientation": {"type": "string",
                                               "enum": ["0", "90", "180", "270", "stacked"]}}}}}}}}}

REDO_PURPOSE = "layout_redo"
REDO = """

Redoing
When the data holds "current_layout", a person has checked an earlier division of this page. Each of its regions \
has an "id" as the person sees it (the part's number, and a letter in a part of several regions: "3b" is the second \
region of part 3) and "locked". Answer with the whole page again, every part and region in reading order, and give \
every region a "keep": the id of the current region it keeps as it is (its box and orientation are then taken from \
the current layout), or "" for a new or redrawn region.
- Keep every locked region, each exactly once: locked regions stay in their order and in the same parts as now \
(locked regions of one part in one part, of different parts in different parts); new regions may join their parts.
- The unlocked regions are the ones the person found wrong: redraw, split, join or reorder them, or keep one that is \
right after all.
- With no unlocked region, only add regions for printed text that no region holds yet.
- "note", when not empty, is the person's note about this page: follow it.
New and redrawn regions follow all the rules above."""
REDO_INSTRUCTIONS = INSTRUCTIONS.replace("\n\nAnswer only with JSON", REDO + "\n\nAnswer only with JSON")
assert REDO_INSTRUCTIONS != INSTRUCTIONS
REDO_SCHEMA = copy.deepcopy(SCHEMA)
_REDO_REGION = REDO_SCHEMA["properties"]["parts"]["items"]["properties"]["regions"]["items"]
_REDO_REGION["properties"] = {"keep": {"type": "string"}, **_REDO_REGION["properties"]}
_REDO_REGION["required"] = ["keep", "box", "orientation"]



def preview(raster_path: Path, target: Path) -> Path:
    """The page image the AI sees: the working raster (longer side LONG_SIDE px) with a faint labelled grid in
    0..1000 coordinates."""
    image = pymupdf.Pixmap(str(raster_path))
    zoom = LONG_SIDE / max(image.width, image.height)
    w, h = round(image.width * zoom), round(image.height * zoom)
    doc = pymupdf.open()
    sheet = doc.new_page(width=w, height=h)
    sheet.insert_image(sheet.rect, filename=str(raster_path))
    for k in range(0, SCALE + 1, 100):
        x, y = k * w / SCALE, k * h / SCALE
        sheet.draw_line((x, 0), (x, h), color=(0.2, 0.6, 1), width=0.6, stroke_opacity=0.45)
        sheet.draw_line((0, y), (w, y), color=(0.2, 0.6, 1), width=0.6, stroke_opacity=0.45)
        if 0 < k < SCALE:
            for at in ((x + 2, 11), (x + 2, h - 3)):
                sheet.insert_text(at, str(k), fontsize=10, color=(0, 0.35, 0.9))
            for at in ((2, y - 2), (w - 22, y - 2)):
                sheet.insert_text(at, str(k), fontsize=10, color=(0, 0.35, 0.9))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.get_pixmap().save(target)
    return target




# ---------------------------------------------------------------- ink
# Ink of a page's working raster for region snapping and coverage .
#
# The raster is reduced to about WORK_DPI. A pixel is ink when it is darker than the paper around it by
# INK_CONTRAST of the paper-to-darkest-ink contrast. The paper level is the light end of a window of PAPER_WINDOW
# text heights around the pixel, lowered near the raster edges to the light end of the pixel's whole column (row)
# where whole columns (rows) are darker than the page's paper in a run that reaches the edge: a binding shadow or
# a dark scanner background is paper there, not ink, while text printed into the shadow stays ink (a printed bar
# inside the page is no such run and stays ink). Ink pixels closer than JOIN_X text heights along a row
# (JOIN_Y across rows) join into one blob: a word, a headline letter group, a picture, a frame. Blobs are classed:
#   border   touches the raster edge and is larger than BORDER inches (scanner background, shadow edge)
#   rule     a thin line (thinner than RULE_THIN text heights, longer than RULE_LONG text heights)
#   speck    smaller than SPECK text heights both ways (dust, screen dots)
#   picture  taller than PICTURE text heights and wider than that too (photo, drawing, large logo)
#   text     everything else: glyph-sized and word-sized shapes
# All tolerances are in inches or text heights, never pixels.
WORK_DPI = 200
INK_CONTRAST = 0.35
PAPER_WINDOW = 1.5
PROFILE_SHARE = 90      # percentile of a whole column/row taken as its paper level
EDGE_DARK = 0.08       # a column/row this much darker than the paper (share of contrast) is shadow when it reaches the edge
JOIN_X = 0.35
JOIN_Y = 0.0
BORDER = 0.4            # inches
RULE_THIN = 0.25
RULE_LONG = 6
SPECK = 0.2
PICTURE = 4.5
BAR_TALL = 5           # an upright shape taller than this ...
BAR_ASPECT = 4         # ... and this many times its width is a bar, not a letter
DOT_SIZE = 1.5         # dotted/dashed line: marks no larger than this,
DOT_SIMILAR = 1.25     # of one size (width, height and ink within this factor),
DOT_PITCH = 0.1        # at a steady step (within this share of the step),
DOT_RUN = 8            # at least this many in a row (letter-spaced words vary in letter width and step)


def _components(mask: numpy.ndarray) -> numpy.ndarray:
    """8-connected component labels (0 = background, 1..n) of a boolean mask, from its row runs."""
    h, w = mask.shape
    padded = numpy.zeros((h, w + 2), dtype=numpy.int8)
    padded[:, 1:-1] = mask
    diff = numpy.diff(padded, axis=1)
    ys, x0s = numpy.nonzero(diff == 1)
    _, x1s = numpy.nonzero(diff == -1)
    n = len(ys)
    parent = numpy.arange(n)

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root
    row_start = numpy.searchsorted(ys, numpy.arange(h + 1))
    ys_l, x0_l, x1_l = ys.tolist(), x0s.tolist(), x1s.tolist()
    for y in range(1, h):
        a, a_end = row_start[y - 1], row_start[y]
        b, b_end = row_start[y], row_start[y + 1]
        while a < a_end and b < b_end:
            # runs [x0, x1) touch (8-connectivity) when x0_a <= x1_b and x0_b <= x1_a
            if x0_l[a] <= x1_l[b] and x0_l[b] <= x1_l[a]:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
            if x1_l[a] < x1_l[b]:
                a += 1
            else:
                b += 1
    roots = numpy.array([find(i) for i in range(n)], dtype=numpy.int64)
    _, label = numpy.unique(roots, return_inverse=True)
    labels = numpy.zeros((h, w), dtype=numpy.int32)
    for y, x0, x1, lab in zip(ys_l, x0_l, x1_l, (label + 1).tolist()):
        labels[y, x0:x1] = lab
    return labels


def _edge_zone(profile: numpy.ndarray, darkest: float) -> numpy.ndarray:
    """A column (row) paper profile kept only where it is darker than the page's paper in a run that reaches
    the raster edge (binding shadow, scanner background); elsewhere no limit (a printed bar is not paper)."""
    paper = float(numpy.median(profile))
    dark = profile < paper - EDGE_DARK * (paper - darkest)
    limit = numpy.full(profile.shape, numpy.inf, dtype=numpy.float32)
    n = len(profile)
    i = 0
    while i < n and dark[i]:
        i += 1
    j = n
    while j > i and dark[j - 1]:
        j -= 1
    limit[:i] = profile[:i]
    limit[j:] = profile[j:]
    return limit


def _grow(mask: numpy.ndarray, dx: int, dy: int) -> numpy.ndarray:
    out = mask.copy()
    for k in range(1, dx + 1):
        out[:, k:] |= mask[:, :-k]
        out[:, :-k] |= mask[:, k:]
    grown = out.copy()
    for k in range(1, dy + 1):
        grown[k:] |= out[:-k]
        grown[:-k] |= out[k:]
    return grown


class Ink:
    """Ink blobs of one page, boxes in page points."""

    def __init__(self, raster_path, raster: dict, text_height_pt: float):
        grey = load_grey(raster_path).astype(numpy.float32)
        dpi = sum(raster["dpi"]) / 2
        f = max(1, round(dpi / WORK_DPI))
        h, w = grey.shape[0] // f, grey.shape[1] // f
        grey = grey[:h * f, :w * f].reshape(h, f, w, f).mean(axis=(1, 3))
        self.dpi = dpi / f
        self.f = f
        # page points -> work pixels and back
        self.to_px = multiply(raster["to_raster"], [1 / f, 0, 0, 1 / f, 0, 0])
        self.to_pt = multiply([f, 0, 0, f, 0, 0], raster["to_source"])
        th = text_height_pt * abs(self.to_px[0])         # text height in work pixels
        self.th = th
        win = max(3, round(PAPER_WINDOW * th)) | 1
        local = _square(_square(grey, win, numpy.max), win, numpy.mean)
        darkest = float(numpy.percentile(grey, 0.5))
        col = _edge_zone(numpy.percentile(grey, PROFILE_SHARE, axis=0), darkest)
        row = _edge_zone(numpy.percentile(grey, PROFILE_SHARE, axis=1), darkest)
        paper = numpy.minimum(local, numpy.minimum(col[None, :], row[:, None]))
        self.grey, self.paper, self.darkest = grey, paper, darkest
        mask = grey < paper - INK_CONTRAST * numpy.maximum(paper - darkest, 0) - 1
        self.mask = mask
        labels = _components(_grow(mask, max(1, round(JOIN_X * th / 2)), max(0, round(JOIN_Y * th / 2))))
        labels[~mask] = 0
        count = labels.max()
        pixels = numpy.bincount(labels.ravel(), minlength=count + 1)
        ys, xs = numpy.nonzero(labels)
        lab = labels[ys, xs]
        x0 = numpy.full(count + 1, w); y0 = numpy.full(count + 1, h)
        x1 = numpy.full(count + 1, -1); y1 = numpy.full(count + 1, -1)
        numpy.minimum.at(x0, lab, xs); numpy.minimum.at(y0, lab, ys)
        numpy.maximum.at(x1, lab, xs + 1); numpy.maximum.at(y1, lab, ys + 1)
        edge = 2
        border, inch = BORDER * self.dpi, self.dpi
        self.blobs = []
        kind_of = numpy.zeros(count + 1, dtype=numpy.int8)  # 0 none, 1 counted ink
        for k in range(1, count + 1):
            if pixels[k] == 0:
                continue
            bw, bh = x1[k] - x0[k], y1[k] - y0[k]
            touches = x0[k] <= edge or y0[k] <= edge or x1[k] >= w - edge or y1[k] >= h - edge
            if touches and max(bw, bh) > border:
                kind = "border"
            elif min(bw, bh) < RULE_THIN * th and max(bw, bh) > RULE_LONG * th:
                kind = "rule"
            elif bw < SPECK * th and bh < SPECK * th:
                kind = "speck"
            elif bw > PICTURE * th and bh > PICTURE * th:
                kind = "picture"
            else:
                kind = "text"
            if kind in ("text", "picture"):
                kind_of[k] = 1
            px = (int(x0[k]), int(y0[k]), int(x1[k]), int(y1[k]))
            self.blobs.append({"id": k, "kind": kind, "px": px, "box": tuple(apply_rect(self.to_pt, px)),
                               "pixels": int(pixels[k])})
        counted = kind_of[labels] == 1
        self.counted = counted
        self.integral = numpy.pad(counted.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        self.labels = labels
        # roles of ink: 1 letters; 2 marks that are never letters (rules, line fragments, upright bars, dotted
        # lines); 3 shapes that may hold letters (pictures: also a frame with the text touching it or a headline
        # whose letters touch; sparse shapes: also a text line joined by leader dots)
        role = numpy.zeros(count + 1, dtype=numpy.int8)
        dotted = self._dotted()
        for b in self.blobs:
            if b["kind"] in ("speck", "border"):
                continue
            shape = self._shape(b) if b["kind"] == "text" and b["id"] not in dotted else "mark"
            if b["kind"] == "picture":
                shape = "maybe"
            b["letter"] = shape == "letter"
            role[b["id"]] = {"letter": 1, "mark": 2, "maybe": 3}[shape]
        roles = role[labels]
        self.sums = {k: numpy.pad((roles == k).cumsum(0).cumsum(1), ((1, 0), (1, 0))) for k in (1, 2, 3)}

    def _shape(self, blob: dict) -> str:
        """"mark" for a line fragment (thinner than GLYPH_THIN) or an upright bar (taller than BAR_TALL text
        heights and BAR_ASPECT times its width: stacked or rotated letters stay separate blobs, a shadow edge or a
        vertical ornament is one); "maybe" for a sparse shape (fill under GLYPH_FILL); else "letter"."""
        x0, y0, x1, y1 = blob["px"]
        bw, bh = x1 - x0, y1 - y0
        fill = blob["pixels"] / max(bw * bh, 1)
        if min(bw, bh) < GLYPH_THIN * self.th or (bh > BAR_TALL * self.th and bh > BAR_ASPECT * bw):
            return "mark"
        return "maybe" if fill < GLYPH_FILL else "letter"

    def _dotted(self) -> set[int]:
        """Ids of blobs that form a dotted or dashed line: at least DOT_RUN small shapes (under DOT_SIZE text
        heights) of one size (within DOT_SIMILAR) in a row at a steady pitch (within DOT_PITCH of the median).
        Letters of a word join into one blob, so only marks set apart at an even step chain up."""
        small = [b for b in self.blobs if b["kind"] == "text"
                 and max(b["px"][2] - b["px"][0], b["px"][3] - b["px"][1]) <= DOT_SIZE * self.th]
        small.sort(key=lambda b: b["px"][0])
        starts = [b["px"][0] for b in small]
        found: set[int] = set()
        used: set[int] = set()
        for start in small:
            if start["id"] in used:
                continue
            chain = [start]
            while True:
                last = chain[-1]
                lw, lh = last["px"][2] - last["px"][0], last["px"][3] - last["px"][1]
                cy = (last["px"][1] + last["px"][3]) / 2
                pitch = (chain[-1]["px"][0] - chain[-2]["px"][0]) if len(chain) > 1 else None
                best = None
                reach = last["px"][2] + 3 * max(lw, lh, 1)
                for b in small[bisect.bisect_right(starts, last["px"][0]):bisect.bisect_right(starts, reach)]:
                    if b["id"] in used:
                        continue
                    bw, bh = b["px"][2] - b["px"][0], b["px"][3] - b["px"][1]
                    if abs((b["px"][1] + b["px"][3]) / 2 - cy) > 0.3 * max(lh, bh, 1):
                        continue
                    if (max(bw, lw) > DOT_SIMILAR * max(min(bw, lw), 1) or max(bh, lh) > DOT_SIMILAR * max(min(bh, lh), 1)
                            or max(b["pixels"], last["pixels"]) > DOT_SIMILAR * min(b["pixels"], last["pixels"])):
                        continue
                    step = b["px"][0] - last["px"][0]
                    if pitch is not None and abs(step - pitch) > DOT_PITCH * pitch:
                        continue
                    if best is None or b["px"][0] < best["px"][0]:
                        best = b
                if best is None:
                    break
                chain.append(best)
            if len(chain) >= DOT_RUN:
                ids = {b["id"] for b in chain}
                found |= ids
                used |= ids
        return found

    def roles_in(self, box_pt) -> tuple[int, int, int]:
        """Pixels of letters, marks and maybe-letters inside a box given in page points."""
        x0, y0, x1, y1 = apply_rect(self.to_px, box_pt)
        h, w = self.counted.shape
        x0, x1 = (min(max(int(round(v)), 0), w) for v in (x0, x1))
        y0, y1 = (min(max(int(round(v)), 0), h) for v in (y0, y1))
        if x1 <= x0 or y1 <= y0:
            return 0, 0, 0
        return tuple(int(s[y1, x1] - s[y0, x1] - s[y1, x0] + s[y0, x0]) for s in (self.sums[k] for k in (1, 2, 3)))

    def ink_in(self, box_pt) -> int:
        """Counted ink pixels (text and pictures) inside a box given in page points."""
        x0, y0, x1, y1 = apply_rect(self.to_px, box_pt)
        h, w = self.counted.shape
        x0, x1 = (min(max(int(round(v)), 0), w) for v in (x0, x1))
        y0, y1 = (min(max(int(round(v)), 0), h) for v in (y0, y1))
        if x1 <= x0 or y1 <= y0:
            return 0
        s = self.integral
        return int(s[y1, x1] - s[y0, x1] - s[y1, x0] + s[y0, x0])

    def strength(self, box_pt) -> float:
        """How dark the box's darkest ink is: 0 = local paper, 1 = the page's darkest ink. Print is near 1,
        smudges and show-through stay low."""
        x0, y0, x1, y1 = (int(round(v)) for v in apply_rect(self.to_px, box_pt))
        h, w = self.grey.shape
        sub = self.grey[max(y0, 0):min(y1, h), max(x0, 0):min(x1, w)]
        if not sub.size:
            return 0.0
        paper = float(numpy.median(self.paper[max(y0, 0):min(y1, h), max(x0, 0):min(x1, w)]))
        return (paper - float(numpy.percentile(sub, 2))) / max(paper - self.darkest, 1)

    def area_px(self, box_pt) -> float:
        x0, y0, x1, y1 = apply_rect(self.to_px, box_pt)
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)


# ---------------------------------------------------------------- geometry (page points)

def contains(box, point) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def intersection(a, b):
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return box if box[0] < box[2] and box[1] < box[3] else None


def area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def distance(a, b) -> float:
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return (dx * dx + dy * dy) ** 0.5


def union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))



class Page:
    """The page rectangle, its OCR words, its ink and conversions between page points and the 0..1000 grid.

    page: the prepared page (`ocrmaker.pages`); recognised: a whole-page Tesseract result (`ocrmaker.words`)."""

    def __init__(self, page: dict, recognised: dict, raster_path: Path):
        self.n = page["index"] + 1
        self.rect = pymupdf.Rect(0, 0, page["width"], page["height"])
        self.words = []
        for line_no, line in enumerate(recognised["lines"]):
            for word in line["words"]:
                x0, y0, x1, y1 = word["bbox"]
                self.words.append({"text": word["text"], "box": (x0, y0, x1, y1), "line": line_no,
                                   "confidence": word.get("confidence", 100.0),
                                   "c": ((x0 + x1) / 2, (y0 + y1) / 2), "h": max(y1 - y0, 1.0),
                                   "real": any(ch.isalnum() for ch in word["text"])})
        heights = [w["h"] for w in self.words if w["real"]]
        self.th = statistics.median(heights) if heights else 8.0
        self.ink = Ink(raster_path, page["raster"], self.th)
        edge = EDGE_NOISE * 72
        for w in self.words:
            x0, y0, x1, y1 = w["box"]
            at_edge = (x0 - self.rect.x0 < edge or y0 - self.rect.y0 < edge or self.rect.x1 - x1 < edge
                       or self.rect.y1 - y1 < edge)
            inked = self.ink.ink_in(w["box"]) >= NOISE_INK * max(self.ink.area_px(w["box"]), 1)
            w["noise"] = not w["real"] or at_edge or not inked
            # "words" read off a rule, a dotted line, a picture, a frame corner or an ornament: ink mostly marks,
            # or mostly marks and maybe-letters read with little confidence (text in a frame or a large headline
            # is read with confidence)
            letters, marks, maybe = self.ink.roles_in(w["box"])
            w["off_marks"] = marks > letters + maybe
            w["decor"] = not w["noise"] and (w["off_marks"] or (
                marks + maybe > letters and w["confidence"] < DECOR_CONFIDENCE))
            w["text_word"] = not w["noise"] and not w["decor"]

    def to_page(self, box):
        r = self.rect
        return (r.x0 + box[0] * r.width / SCALE, r.y0 + box[1] * r.height / SCALE,
                r.x0 + box[2] * r.width / SCALE, r.y0 + box[3] * r.height / SCALE)

    def to_grid(self, box, outward=False):
        r = self.rect
        values = [(box[0] - r.x0) * SCALE / r.width, (box[1] - r.y0) * SCALE / r.height,
                  (box[2] - r.x0) * SCALE / r.width, (box[3] - r.y0) * SCALE / r.height]
        if outward:
            return [int(numpy.floor(values[0])), int(numpy.floor(values[1])),
                    int(numpy.ceil(values[2])), int(numpy.ceil(values[3]))]
        return [round(v) for v in values]


def regions_of(answer: dict) -> list[dict]:
    """Flat list of regions: label "p.r", part index, box in grid units, orientation, and for a region kept as the
    person left it its page box (`fixed`)."""
    return [{"label": f"{p + 1}.{r + 1}", "part": p, "box": region["box"],
             "orientation": region.get("orientation", "0"),
             **({"fixed": tuple(region["fixed"])} if region.get("fixed") else {})}
            for p, part in enumerate(answer["parts"]) for r, region in enumerate(part["regions"])]


# ---------------------------------------------------------------- snapping

def snap(page: Page, answer: dict) -> dict:
    """Regions snapped to the ink they own (see the module docstring). Returns the regions with "pt" boxes
    (page points), the blob owners and the uncovered text spots and pictures."""
    regions = regions_of(answer)
    ink, th = page.ink, page.th
    # fragments (a broken-off piece of a letter, a dot) neither pull nor push an edge
    blobs = [b for b in ink.blobs if b["kind"] in ("text", "picture") and b["pixels"] >= MIN_BLOB * ink.th ** 2]
    index = {b["id"]: k for k, b in enumerate(blobs)}
    labels = ink.labels
    h, w = labels.shape
    share = numpy.zeros((len(blobs), len(regions)))
    pixels = numpy.array([b["pixels"] for b in blobs], dtype=float)
    ids = numpy.array([b["id"] for b in blobs])
    for r, region in enumerate(regions):
        x0, y0, x1, y1 = (int(round(v)) for v in _px(ink, page.to_page(region["box"])))
        sub = labels[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
        if sub.size:
            counts = numpy.bincount(sub.ravel(), minlength=labels.max() + 1)
            share[:, r] = counts[ids] / numpy.maximum(pixels, 1)
    sizes = [area(page.to_page(region["box"])) for region in regions]
    # a text line is owned as a whole: its words go to the region holding most of the line's ink, so an edge
    # drawn through a column's text moves to the column's gutter instead of cutting its lines
    lines = text_lines(blobs, th)
    for line in lines:
        weight = pixels[line].sum()
        share[line] = (share[line] * pixels[line, None]).sum(axis=0) / max(weight, 1)
    owner = [None] * len(blobs)
    for k in range(len(blobs)):
        best = max(range(len(regions)), key=lambda r: (round(share[k, r], 3), -sizes[r]), default=None)
        if best is not None and share[k, best] >= SHARE:
            owner[k] = best

    def owned_box(r, extra=None):
        boxes = [blobs[k]["box"] for k in range(len(blobs)) if owner[k] == r]
        if extra is not None:
            boxes.append(extra)
        return union(boxes) if boxes else None

    def collides(r, box) -> bool:
        """Would a region box reach ink owned by another region?"""
        for k, o in enumerate(owner):
            if o is not None and o != r:
                common = intersection(box, blobs[k]["box"])
                if common and area(common) > 0.1 * area(blobs[k]["box"]):
                    return True
        return False
    # partly-inside blobs whose growth would reach another region's ink are given up
    for k in sorted(range(len(blobs)), key=lambda k: -max(share[k]) if len(regions) else 0):
        r = owner[k]
        if r is None or share[k, r] > 0.98:
            continue
        others = [j for j in range(len(blobs)) if owner[j] == r and j != k]
        base = union([blobs[j]["box"] for j in others]) if others else None
        grown = union([base, blobs[k]["box"]]) if base else blobs[k]["box"]
        if collides(r, grown):
            owner[k] = None
    # unowned text blobs join a neighbouring region: same text line, or the line right below/above
    changed = True
    while changed:
        changed = False
        boxes = [owned_box(r) for r in range(len(regions))]
        for k, blob in enumerate(blobs):
            if owner[k] is not None or blob["kind"] != "text":
                continue
            b = blob["box"]
            best = None
            for j, o in enumerate(owner):
                if o is None:
                    continue
                c = blobs[j]["box"]
                low = min(b[3] - b[1], c[3] - c[1])
                overlap_y = min(b[3], c[3]) - max(b[1], c[1])
                overlap_x = min(b[2], c[2]) - max(b[0], c[0])
                if overlap_y >= 0.5 * low:
                    gap = max(c[0] - b[2], b[0] - c[2])
                    if gap <= ROW_GAP * th and (best is None or gap < best[0]):
                        best = (gap, o)
                elif overlap_x > 0:
                    gap = max(c[1] - b[3], b[1] - c[3])
                    if 0 <= gap <= COLUMN_GAP * th and (best is None or gap < best[0]):
                        best = (gap, o)
            if best is not None:
                grown = union([boxes[best[1]], b]) if boxes[best[1]] else b
                if not collides(best[1], grown):
                    owner[k] = best[1]
                    boxes[best[1]] = grown
                    changed = True
    pad = PAD * th
    for r, region in enumerate(regions):
        box = owned_box(r)
        if region.get("fixed"):   # kept as the person left it: owns its ink, but is never snapped
            region["pt"] = region["fixed"]
            if box is not None:
                region["ink"] = box
            continue
        if box is None:   # no ink at all: keep the AI's box
            region["pt"] = page.to_page(region["box"])
            region["empty"] = True
            continue
        padded = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
        region["pt"] = box if collides(r, padded) else padded
        region["ink"] = box
    separate(regions, th)
    # uncovered text: clusters of unowned text blobs; uncovered pictures counted apart
    loose = [k for k in range(len(blobs)) if owner[k] is None and blobs[k]["kind"] == "text"
             and not _at_edge(page, blobs[k]["box"]) and blobs[k]["letter"]]
    spots = []
    for k in loose:
        box = blobs[k]["box"]
        merged = [s for s in spots if distance(s["box"], box) <= CLUSTER_GAP * th]
        for s in merged:
            spots.remove(s)
        spots.append({"box": union([box] + [s["box"] for s in merged]),
                      "blobs": 1 + sum(s["blobs"] for s in merged),
                      "pixels": blobs[k]["pixels"] + sum(s["pixels"] for s in merged)})
    # a spot counts when it is more than a stray mark: two shapes, or one letter-sized shape
    # print is about as dark as the page's text; a smudge or show-through is fainter
    spots = [s for s in spots if ink.strength(s["box"]) >= SPOT_STRENGTH]
    spots = [s for s in spots if s["blobs"] >= 2 or (s["box"][3] - s["box"][1] >= 0.8 * th
                                                    and s["box"][2] - s["box"][0] >= 0.5 * th)]
    # one long shape no thicker than a text line, on which recognition read nothing, is a printed line
    # (a double or wavy rule)
    spots = [s for s in spots if not (s["blobs"] == 1 and max(s["box"][2] - s["box"][0], s["box"][3] - s["box"][1])
                                      > RULE_LONG * th and min(s["box"][2] - s["box"][0], s["box"][3] - s["box"][1])
                                      < LINE_THICK * th)]
    for s in spots:
        s["words"] = [i for i, word in enumerate(page.words) if word["text_word"] and contains(s["box"], word["c"])]
    spots = [s for s in spots if not s["words"]]   # spots with recognised words are reported as words
    pictures = [blobs[k]["box"] for k in range(len(blobs)) if owner[k] is None and blobs[k]["kind"] == "picture"]
    # loose strokes of a drawing left outside the regions belong to the drawing
    spots = [s for s in spots if not any(contains(p, ((s["box"][0] + s["box"][2]) / 2, (s["box"][1] + s["box"][3]) / 2))
                                         for p in pictures)]
    return {"regions": regions, "spots": spots, "pictures": pictures}


def separate(regions: list[dict], th: float) -> None:
    """Overlapping boxes of two regions whose ink lies apart along one axis (up to TOUCH text heights of
    overlap: a descender above an ascender) get one common edge midway between their ink. Overlaps where the
    ink of the two interleaves stay for the correction request. A fixed region never moves: the other region's
    edge alone gives way, up to the fixed edge or its own ink."""
    for a in regions:
        for b in regions:
            if a is b or "ink" not in a or "ink" not in b or not intersection(a["pt"], b["pt"]):
                continue
            if a.get("fixed") and b.get("fixed"):
                continue
            options = []
            for lo, hi in ((0, 2), (1, 3)):          # x, then y
                first, second = (a, b) if a["ink"][lo] <= b["ink"][lo] else (b, a)
                gap = second["ink"][lo] - first["ink"][hi]
                if gap >= -TOUCH * th:
                    options.append((gap, lo, hi, first, second))
            if not options:
                continue
            _, lo, hi, first, second = max(options, key=lambda o: o[0])
            if first["pt"][hi] <= second["pt"][lo]:
                continue
            if first.get("fixed"):
                edge = min(first["pt"][hi], second["ink"][lo])
            elif second.get("fixed"):
                edge = max(second["pt"][lo], first["ink"][hi])
            else:
                edge = (first["ink"][hi] + second["ink"][lo]) / 2
            low, high = list(first["pt"]), list(second["pt"])
            if not first.get("fixed"):
                low[hi] = min(low[hi], edge)
            if not second.get("fixed"):
                high[lo] = max(high[lo], edge)
            first["pt"], second["pt"] = tuple(low), tuple(high)


def _px(ink: Ink, box_pt):
    return apply_rect(ink.to_px, box_pt)


def chained(boxes: dict[int, tuple], joined) -> list[list[int]]:
    """Items (key -> box) chained into groups, top to bottom: two items whose boxes overlap vertically are linked
    when `joined(upper box, lower box)` holds (upper by top edge)."""
    order = sorted(boxes, key=lambda k: boxes[k][1])
    root = {k: k for k in order}

    def find(k):
        while root[k] != k:
            root[k] = root[root[k]]
            k = root[k]
        return k
    for n, a in enumerate(order):
        for b in order[n + 1:]:
            if boxes[b][1] >= boxes[a][3]:
                break
            if joined(boxes[a], boxes[b]):
                root[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for k in order:
        groups.setdefault(find(k), []).append(k)
    return list(groups.values())


def text_lines(blobs: list[dict], th: float) -> list[list[int]]:
    """Text blobs chained into lines: side by side on one row (vertical overlap of half the lower one, heights
    within a factor of 2), gaps up to LINE_GAP text heights (word spacing, narrower than a column gutter)."""
    def joined(a, b) -> bool:
        ha, hb = a[3] - a[1], b[3] - b[1]
        return (max(ha, hb) <= 2 * min(ha, hb) and min(a[3], b[3]) - max(a[1], b[1]) >= 0.5 * min(ha, hb)
                and max(b[0] - a[2], a[0] - b[2]) <= LINE_GAP * th)
    groups = chained({k: b["box"] for k, b in enumerate(blobs) if b["kind"] == "text"}, joined)
    return [g for g in groups if len(g) > 1]


def _at_edge(page: Page, box) -> bool:
    edge = EDGE_NOISE * 72
    r = page.rect
    return box[0] - r.x0 < edge or box[1] - r.y0 < edge or r.x1 - box[2] < edge or r.y1 - box[3] < edge


def settle(page: Page, answer: dict) -> tuple[dict, dict, list[str]]:
    """Snap the answer and drop its regions without text: no recognised word in the snapped box (a rule, an
    ornament, a dotted line, a picture on its own; a picture inside a framed box stays with the box's region). A region stays when dropping it would leave text-like ink
    uncovered (lettering the recognition did not read). A fixed region (kept as the person left it) always stays.
    A part left without regions goes. Returns the answer, its snapped regions and the labels of the dropped
    regions."""
    snapped = snap(page, answer)
    dropped, spots = [], len(snapped["spots"])
    # a confidently read word counts even where it has no counted ink (white letters on a black bar)
    text = [w for w in page.words
            if w["text_word"] or (w["real"] and not w["decor"] and w["confidence"] >= DECOR_CONFIDENCE)]
    for region in list(snapped["regions"]):
        if region.get("fixed") or any(contains(region["pt"], w["c"]) for w in text):
            continue
        p, r = (int(v) - 1 for v in region["label"].split("."))
        shift = sum(1 for label in dropped if label.startswith(f"{p + 1}.") and int(label.split(".")[1]) <= r + 1)
        parts = [dict(part, regions=list(part["regions"])) for part in answer["parts"]]
        del parts[p]["regions"][r - shift]
        trial = {"parts": parts}
        trial_snap = snap(page, {"parts": [part for part in parts if part["regions"]]})
        if len(trial_snap["spots"]) > spots:
            continue
        answer, dropped, spots = trial, dropped + [region["label"]], len(trial_snap["spots"])
    answer = {"parts": [part for part in answer["parts"] if part["regions"]]}
    return answer, (snap(page, answer) if dropped else snapped), dropped


def snapped_answer(page: Page, answer: dict, snapped: dict) -> dict:
    """The answer with every box replaced by its snapped box (grid units, rounded outward); a redo answer's regions
    keep their "keep"."""
    regions = iter(snapped["regions"])
    return {"parts": [{"kind": part["kind"], "title": part.get("title", ""), "regions": [
        {**({"keep": region["keep"]} if "keep" in region else {}),
         "box": page.to_grid(next(regions)["pt"], outward=True), "orientation": region.get("orientation", "0")}
        for region in part["regions"]]} for part in answer["parts"]]}


# ---------------------------------------------------------------- check

def same_row(a: dict, b: dict) -> bool:
    """Two words of one text line (similar size, overlapping rows), close enough to be read together."""
    low, high = sorted((a["h"], b["h"]))
    if high > 1.6 * low:
        return False
    overlap = min(a["box"][3], b["box"][3]) - max(a["box"][1], b["box"][1])
    if overlap < 0.5 * low:
        return False
    gap = max(b["box"][0] - a["box"][2], a["box"][0] - b["box"][2])
    return gap < 1.5 * low


def word_rows(page: Page, indices: list[int]) -> list[list[int]]:
    """Words (indices) chained into rows as the eye reads them, whatever recognition made of its lines: vertical
    overlap of half the lower word, gaps up to LOST_GAP text heights. Each row runs left to right."""
    def joined(a, b) -> bool:
        return (min(a[3], b[3]) - max(a[1], b[1]) >= 0.5 * min(a[3] - a[1], b[3] - b[1])
                and max(b[0] - a[2], a[0] - b[2]) <= LOST_GAP * page.th)
    rows = chained({i: page.words[i]["box"] for i in indices}, joined)
    return [sorted(row, key=lambda i: page.words[i]["box"][0]) for row in rows]


def lost_lines(page: Page, member: list[list[int]]) -> list[list[int]]:
    """Lines of text in no region that the noise filter set aside: rows (`word_rows`) of words in no region (not
    noise, not read off marks) with at least one decoration word, which read as text: at least LOST_WORDS words
    of WORD_LETTERS letters or more, and those at least half of the row. Recognition reads handwriting-like print
    or a line joined to a drawing with little confidence over picture ink, so its words look like decoration; the
    words recognition reads off a picture or an ornament are short fragments."""
    loose = [i for i, word in enumerate(page.words) if not member[i] and not word["noise"] and not word["off_marks"]]
    lines = []
    for row in word_rows(page, loose):
        words = [page.words[i] for i in row]
        long_words = sum(sum(ch.isalpha() for ch in word["text"]) >= WORD_LETTERS for word in words)
        if any(word["decor"] for word in words) and long_words >= LOST_WORDS and 2 * long_words >= len(words):
            lines.append(row)
    return lines


def membership(page: Page, regions: list[dict]) -> list[list[int]]:
    """For every word the indices of the regions (snapped "pt" boxes) holding its centre."""
    return [[i for i, region in enumerate(regions) if contains(region["pt"], word["c"])] for word in page.words]


def segments(page: Page, indices: list[int], key) -> list[list[int]]:
    """Group words (indices) into row segments of neighbouring words with the same key."""
    groups: list[list[int]] = []
    for i in sorted(indices, key=lambda i: (page.words[i]["line"], page.words[i]["box"][0])):
        last = groups[-1][-1] if groups else None
        if (last is not None and key(last) == key(i) and page.words[last]["line"] == page.words[i]["line"]
                and same_row(page.words[last], page.words[i])):
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def rows_of(words: list[dict]) -> list[list[dict]]:
    """Words grouped into text rows (vertical overlap of at least half the lower word)."""
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda w: w["c"][1]):
        for row in rows:
            top, bottom = min(w["box"][1] for w in row), max(w["box"][3] for w in row)
            if min(bottom, word["box"][3]) - max(top, word["box"][1]) >= 0.5 * min(word["h"], bottom - top):
                row.append(word)
                break
        else:
            rows.append([word])
    return rows


def longest_segment(row: list[dict], th: float) -> float:
    """Width of the longest run of words in a row with gaps under SEGMENT_GAP text heights."""
    row = sorted(row, key=lambda w: w["box"][0])
    best, start, end = 0.0, row[0]["box"][0], row[0]["box"][2]
    for word in row[1:]:
        if word["box"][0] - end > SEGMENT_GAP * th:
            best = max(best, end - start)
            start = word["box"][0]
        end = max(end, word["box"][2])
    return max(best, end - start)


def two_columns(page: Page, region: dict, words: list[dict]):
    """A vertical gutter inside the region with running text on both sides, or None.

    Candidate gutters are free vertical strips (at least GUTTER text heights wide) through an unbroken stretch
    of the region's text of at least COLUMN_SPAN of its text height, with COLUMN_ROWS text rows on each side.
    A gutter splits two columns, not a table, when both sides hold running text: most rows have a run of words
    (gaps under SEGMENT_GAP) at least MIN_LINE text heights long. Table rows break into short cells (date, name,
    place: on the test pages at most about 10 text heights, running text lines 11-25)."""
    th = page.th
    if len(words) < 2 * COLUMN_ROWS:
        return None
    x0 = min(w["box"][0] for w in words)
    x1 = max(w["box"][2] for w in words)
    y0 = min(w["box"][1] for w in words)
    y1 = max(w["box"][3] for w in words)
    step = 0.5   # points
    nx, ny = int((x1 - x0) / step) + 1, int((y1 - y0) / step) + 1
    busy = numpy.zeros((ny, nx), dtype=bool)
    for w in words:
        a, b = int((w["box"][0] - x0) / step), int((w["box"][2] - x0) / step) + 1
        c, d = int((w["box"][1] - y0) / step), int((w["box"][3] - y0) / step) + 1
        busy[c:d, a:b] = True
    g = max(1, int(GUTTER * th / step))
    # strip k (columns k..k+g-1) is free in row y when no column of it is busy there
    cum = numpy.concatenate([numpy.zeros((ny, 1), dtype=int), numpy.cumsum(busy, axis=1)], axis=1)
    free = (cum[:, g:] - cum[:, :-g]) == 0           # ny x (nx - g + 1)
    candidates = []
    for k in range(1, free.shape[1] - 1):
        padded = numpy.concatenate([[False], free[:, k], [False]])
        changes = numpy.flatnonzero(padded[1:] != padded[:-1])
        for s, e in zip(changes[::2], changes[1::2]):
            if (e - s) * step >= COLUMN_SPAN * (y1 - y0):
                candidates.append((e - s, k, s, e))
    candidates.sort(reverse=True)
    seen = []
    for _, k, s, e in candidates:
        if any(a <= k <= b and min(e, e2) > max(s, s2) for a, b, s2, e2 in seen):
            continue
        left, right = k, k + g - 1            # widen the strip to its full free width at this stretch
        rows_free = ~busy[s:e].any(axis=0)
        while left > 0 and rows_free[left - 1]:
            left -= 1
        while right < nx - 1 and rows_free[right + 1]:
            right += 1
        seen.append((left, right, s, e))
        gx0, gx1 = x0 + left * step, x0 + (right + 1) * step
        top, bottom = y0 + s * step, y0 + e * step
        inside = [w for w in words if top <= w["c"][1] <= bottom]
        sides = [rows_of([w for w in inside if w["c"][0] < gx0]), rows_of([w for w in inside if w["c"][0] > gx1])]
        if min(len(rows) for rows in sides) < COLUMN_ROWS:
            continue
        verdicts = []
        for rows in sides:
            long = sum(longest_segment(row, th) >= MIN_LINE * th for row in rows)
            verdicts.append((len(rows), round(long / len(rows), 2)))
        running = all(long >= RUNNING for _, long in verdicts)
        if running:
            return {"x": (gx0 + gx1) / 2, "top": top, "bottom": bottom, "rows": [v[0] for v in verdicts]}
        if len(seen) >= 8:
            break
    return None


def check(page: Page, answer: dict, snapped: dict) -> dict:
    """Deterministic problems of snapped regions: overlaps, words in no / several regions, text lines in no region
    whose words look like decoration (`lost_lines`), uncovered text-like ink, regions holding two columns."""
    regions = snapped["regions"]
    member = membership(page, regions)
    grid = lambda box: page.to_grid(box)
    text = lambda group: " ".join(page.words[i]["text"] for i in group)
    where = lambda group: grid(union([page.words[i]["box"] for i in group]))
    nearest = lambda box: min(regions, key=lambda r: distance(r["pt"], box))["label"] if regions else None
    problems = []
    overlaps = 0
    for a in range(len(regions)):
        for b in range(a + 1, len(regions)):
            common = intersection(regions[a]["pt"], regions[b]["pt"])
            if not common or min(common[2] - common[0], common[3] - common[1]) < 0.3 * page.th:
                continue
            inside = [i for i, word in enumerate(page.words) if word["text_word"] and a in member[i] and b in member[i]]
            line = f"Regions {regions[a]['label']} and {regions[b]['label']} overlap in {grid(common)}"
            line += (f"; {len(inside)} words lie in both (listed below)" if inside
                     else " (no recognised words there, but regions must not overlap)")
            problems.append(line)
            overlaps += 1
    lost = lost_lines(page, member)
    in_lost = {i for line in lost for i in line}
    for line in lost:
        box = union([page.words[i]["box"] for i in line])
        problems.append(f"Text line '{text(line)}' at {where(line)} lies in no region (nearest region "
                        f"{nearest(box)}); include it in the region of the part it belongs to")
    missing = [i for i, word in enumerate(page.words) if word["text_word"] and not member[i] and i not in in_lost]
    for group in segments(page, missing, lambda i: 0):
        box = union([page.words[i]["box"] for i in group])
        problems.append(f"Words '{text(group)}' at {where(group)} lie in no region (nearest region {nearest(box)})")
    several = [i for i, word in enumerate(page.words) if word["text_word"] and len(member[i]) > 1]
    for group in segments(page, several, lambda i: tuple(member[i])):
        labels = " and ".join(regions[r]["label"] for r in member[group[0]])
        problems.append(f"Words '{text(group)}' at {where(group)} lie in regions {labels}")
    # a region edge through a text line: neighbouring words of one recognised line (word spacing apart) that
    # lie in different regions
    split = []
    by_line: dict[int, list[int]] = {}
    for i, word in enumerate(page.words):
        if word["text_word"] and len(member[i]) == 1:
            by_line.setdefault(word["line"], []).append(i)
    for indices in by_line.values():
        indices.sort(key=lambda i: page.words[i]["box"][0])
        for a, b in zip(indices, indices[1:]):
            wa, wb = page.words[a], page.words[b]
            if (member[a] != member[b] and same_row(wa, wb)
                    and wb["box"][0] - wa["box"][2] <= LINE_GAP * page.th):
                split.append((a, b))
                problems.append(f"Words '{wa['text']} {wb['text']}' at {where([a, b])} are neighbours in one text "
                                f"line but lie in regions {regions[member[a][0]]['label']} and "
                                f"{regions[member[b][0]]['label']}: a region edge cuts through this line")
    for spot in snapped["spots"]:
        problems.append(f"Printed marks without recognised words at {grid(spot['box'])} ({spot['blobs']} "
                        f"shapes, e.g. large, vertical or decorative lettering) lie in no region (nearest region "
                        f"{nearest(spot['box'])}); include them in the region of the part they belong to")
    columns = []
    for r, region in enumerate(regions):
        words = [page.words[i] for i in range(len(page.words)) if page.words[i]["text_word"] and r in member[i]]
        found = two_columns(page, region, words)
        if found:
            columns.append(region["label"])
            gx = grid((found["x"], found["top"], found["x"], found["bottom"]))
            problems.append(f"Region {region['label']} holds two columns side by side between y={gx[1]} and "
                            f"y={gx[3]}, split by an empty gutter at x={gx[0]} ({found['rows'][0]} lines left, "
                            f"{found['rows'][1]} right); text recognition would interleave their lines. Make one "
                            f"region per column, and a region of its own for anything above or below that spans "
                            f"both")
    return {"problems": problems,
            "score": (len(missing) + len(several) + 2 * len(split) + 5 * (len(snapped["spots"]) + len(lost))
                      + 10 * len(columns) + 3 * overlaps),
            "overlaps": overlaps, "missing": len(missing), "lost": len(lost), "several": len(several),
            "split": len(split), "spots": len(snapped["spots"]),
            "columns": columns, "pictures_outside": len(snapped["pictures"]),
            "noise_words": sum(w["noise"] for w in page.words)}


def grid_units(answer: dict, image: Path) -> bool:
    """Rescale an answer given in image pixels (haiku sometimes ignores the grid) to grid units, in place.
    Returns whether it did."""
    boxes = [region["box"] for part in answer["parts"] for region in part["regions"]]
    if not boxes or max(max(box) for box in boxes) <= PIXEL_LIMIT:
        return False
    pixmap = pymupdf.Pixmap(str(image))
    for box in boxes:
        box[:] = [min(SCALE, round(value * SCALE / size))
                  for value, size in zip(box, (pixmap.width, pixmap.height) * 2)]
    return True


# ---------------------------------------------------------------- one page

def answer_problems(answer, known=frozenset()) -> list[str]:
    """Why an answer does not match the schema (English, empty when valid). A region that keeps one of the `known`
    current regions needs no box of its own."""
    if not isinstance(answer, dict) or not isinstance(answer.get("parts"), list):
        return ["the answer has no list of parts"]
    problems = []
    for p, part in enumerate(answer["parts"], start=1):
        if not isinstance(part, dict) or part.get("kind") not in KINDS:
            problems.append(f"part {p} has no known kind")
            continue
        regions = part.get("regions")
        if not isinstance(regions, list) or not regions:
            problems.append(f"part {p} has no regions")
            continue
        for r, region in enumerate(regions, start=1):
            if isinstance(region, dict) and region.get("keep") and region["keep"] in known:
                continue
            box = region.get("box") if isinstance(region, dict) else None
            if not (isinstance(box, list) and len(box) == 4
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)
                    and box[0] < box[2] and box[1] < box[3]):
                problems.append(f"region {p}.{r} has no valid box")
            elif str(region.get("orientation", "0")) not in ORIENTATIONS:
                problems.append(f"region {p}.{r} has an unknown orientation")
    return problems


def _normalised(answer: dict) -> dict:
    """The answer with boxes clipped to the grid and orientations as strings (a redo answer's "keep" as a string)."""
    def box(region):
        value = region.get("box")
        if isinstance(value, list) and len(value) == 4 and all(isinstance(v, (int, float)) for v in value):
            return [min(SCALE, max(0, v)) for v in value]
        return [0, 0, 0, 0]   # a kept region's own box is not used
    return {"parts": [{"kind": part["kind"], "title": str(part.get("title") or ""),
                       "regions": [{**({"keep": str(region.get("keep") or "")} if "keep" in region else {}),
                                    "box": box(region), "orientation": str(region.get("orientation", "0"))}
                                   for region in part["regions"]]}
                      for part in answer["parts"]]}


def whole_page(page: Page) -> dict:
    """The fallback layout when the AI gave no usable answer: one article over the whole page, so no text is lost."""
    return {"parts": [{"kind": "article", "title": "", "regions": [{"box": [0, 0, SCALE, SCALE], "orientation": "0"}]}]}


def page_regions(page: Page, answer: dict, snapped: dict) -> list[dict]:
    """The settled answer as one sequence of layout regions in reading order (see the module docstring); a part
    that continues the previous one ("continues", the person's join) adds its regions to that part's article."""
    boxes = iter(snapped["regions"])
    regions, article, column = [], 0, 0
    for number, part in enumerate(answer["parts"], start=1):
        if not (part.get("continues") and article):
            article, column = article + 1, 0
        for region in part["regions"]:
            pt = next(boxes)["pt"]
            column += 1
            regions.append({"id": f"p{page.n}-r{len(regions) + 1}", "article": article, "column": column,
                            "continues": column > 1, "part": number, "kind": part["kind"],
                            "title": part.get("title", ""), "orientation": region.get("orientation", "0"),
                            "box": page.to_grid(pt, outward=True), "bbox": [round(v, 2) for v in pt]})
    return regions


def layout_page(page: Page, ask, image: Path) -> dict:
    """One page's layout regions (the result entry of the module docstring).

    ask(data, image) returns the AI's parsed answer, or None when it was not asked (AI unavailable); it raises
    ValueError with the reason for an invalid answer. Without a usable first answer the page gets one region over
    the whole page (`source` "failed" with the reason)."""
    entry = {"index": page.n - 1, "method": "regions", "source": "ai", "reason": None, "round": 1, "note": None,
             "requests": 0, "chosen": "first"}
    first = None
    try:
        first = ask("Split this page into regions.", image)
        entry["requests"] += first is not None
    except ValueError as exc:
        entry["requests"] += 1
        entry["reason"] = str(exc)
    if first is not None and (problems := answer_problems(first)):
        entry["reason"], first = "; ".join(problems), None
    if first is None:
        entry["source"] = "failed"
        entry["reason"] = entry["reason"] or "the AI was not asked"
        final = whole_page(page)
        final_snap = snap(page, final)
        found = check(page, final, final_snap)
    else:
        grid_units(first, image)
        first = _normalised(first)
        final, final_snap, _ = settle(page, first)
        found = check(page, final, final_snap)
        if found["score"]:
            fixed = None
            try:
                fixed = ask({"task": "Correct your previous answer.",
                             "previous_answer": snapped_answer(page, final, final_snap),
                             "problems": found["problems"]}, image)
                entry["requests"] += fixed is not None
            except ValueError:
                entry["requests"] += 1
            if fixed is not None and not answer_problems(fixed):
                grid_units(fixed, image)
                fixed = _normalised(fixed)
                fixed, fixed_snap, _ = settle(page, fixed)
                again = check(page, fixed, fixed_snap)
                if again["score"] <= found["score"]:
                    final, final_snap, found, entry["chosen"] = fixed, fixed_snap, again, "fixed"
    entry["regions"] = page_regions(page, final, final_snap)
    entry["spots"] = [[round(v, 2) for v in spot["box"]] for spot in final_snap["spots"]]
    entry["check"] = found
    return entry


# ---------------------------------------------------------------- later rounds: the person's layout

def grid_box(bbox, width: float, height: float) -> list[float]:
    """A page box (source page coordinates) in grid units, precise enough to give the same page box back."""
    return [round(v * SCALE / size, 4) for v, size in zip(bbox, (width, height) * 2)]


def page_box(box, width: float, height: float) -> list[float]:
    """A grid box in source page coordinates, rounded as the result keeps them."""
    return [round(v * size / SCALE, 2) for v, size in zip(box, (width, height) * 2)]


def layout_parts(regions: list[dict], width: float, height: float) -> list[dict]:
    """A page's layout regions as the parts the person edits (`ocrmaker.layout_review`), every region locked."""
    parts, previous = [], None
    for region in regions:
        number = region.get("part", region["article"])
        if not parts or number != previous:
            parts.append({"kind": region["kind"], "title": region.get("title", ""),
                          "continues": bool(parts) and region["article"] == parts[-1]["article"],
                          "article": region["article"], "regions": []})
            previous = number
        parts[-1]["regions"].append({"box": grid_box(region["bbox"], width, height),
                                     "orientation": region["orientation"], "locked": True})
    for part in parts:
        del part["article"]
    return parts


def region_labels(parts: list[dict]) -> list[list[str]]:
    """The labels the person sees, per part: its number, with a letter in a part of several regions ("3b")."""
    def label(number, index, count):
        return str(number) if count == 1 else f"{number}{chr(97 + index) if index < 26 else f'.{index + 1}'}"
    return [[label(number, index, len(part["regions"])) for index in range(len(part["regions"]))]
            for number, part in enumerate(parts, start=1)]


def redo_data(parts: list[dict], note: str) -> dict:
    """The data of a later round's request: the person's layout with its labels and locks, and the layout note."""
    labels = region_labels(parts)
    return {"task": "Redo the layout of this page.",
            "current_layout": {"parts": [
                {"kind": part["kind"], "title": part["title"], "regions": [
                    {"id": label, "box": [round(v) for v in region["box"]], "orientation": region["orientation"],
                     "locked": region["locked"]} for label, region in zip(labels[p], part["regions"])]}
                for p, part in enumerate(parts)]},
            "note": note}


def resolve(page: Page, answer: dict, parts: list[dict]) -> tuple[dict, list[str]]:
    """A normalised redo answer with its kept regions taken from the person's layout `parts`: a kept region gets its
    current box and orientation, a locked one also its page box (`fixed`) and its part's kind, title and
    continuation. Returns that answer and how it breaks the locks (English, empty when it keeps them)."""
    labels = region_labels(parts)
    current = {label: (p, region) for p, part in enumerate(parts) for label, region in zip(labels[p], part["regions"])}
    locked = [label for label, (_, region) in current.items() if region["locked"]]
    problems, seen, placed, sequence, resolved = [], set(), {}, [], []
    width, height = page.rect.width, page.rect.height
    for part in answer["parts"]:
        regions, owner = [], None
        for region in part["regions"]:
            keep = region.get("keep") or ""
            if keep not in current:
                regions.append({"box": region["box"], "orientation": region["orientation"], "keep": ""})
                continue
            if keep in seen:
                problems.append(f"Region {keep} is listed twice: list every kept region once")
                continue
            seen.add(keep)
            p, kept = current[keep]
            item = {"box": kept["box"], "orientation": kept["orientation"], "keep": keep}
            if kept["locked"]:
                item["fixed"] = page_box(kept["box"], width, height)
                placed[keep] = len(resolved)
                sequence.append(keep)
                owner = p if owner is None else owner
            regions.append(item)
        if not regions:
            continue
        source = parts[owner] if owner is not None else part
        resolved.append({"kind": source["kind"], "title": source.get("title", ""),
                         "continues": bool(owner is not None and parts[owner].get("continues")), "regions": regions})
    missing = [label for label in locked if label not in placed]
    if missing:
        problems.append(f"Locked regions {', '.join(missing)} are missing: list each of them once, with its id in "
                        f"\"keep\"")
    present = [label for label in locked if label in placed]
    if sequence != present:
        problems.append(f"Locked regions must stay in their order: {', '.join(present)}")
    else:
        for a, b in zip(sequence, sequence[1:]):
            together = current[a][0] == current[b][0]
            if together and placed[a] != placed[b]:
                problems.append(f"Locked regions {a} and {b} belong to one part: keep them in one part")
            elif not together and placed[a] == placed[b]:
                problems.append(f"Locked regions {a} and {b} belong to different parts: keep them in different parts")
    return {"parts": resolved}, problems


def redo_page(page: Page, ask, image: Path, parts: list[dict], note: str) -> tuple[dict | None, str | None]:
    """One page's layout regions redone around the person's layout `parts` (a later layout round, see the module
    docstring). ask(data, image) as in `layout_page`, with the redo instructions and schema. Returns the result
    entry, or None with the reason when the AI gave no answer that keeps the locks (the page then stays as the
    person left it)."""
    entry = {"index": page.n - 1, "method": "regions", "source": "ai", "reason": None, "note": note or None,
             "requests": 0, "chosen": "first"}
    known = {label for labels in region_labels(parts) for label in labels}
    data = redo_data(parts, note)

    def attempt(request):
        try:
            answer = ask(request, image)
        except ValueError as exc:
            entry["requests"] += 1
            return None, str(exc)
        if answer is None:
            return None, "the AI was not asked"
        entry["requests"] += 1
        if problems := answer_problems(answer, known):
            return None, "; ".join(problems)
        grid_units(answer, image)
        resolved, broken = resolve(page, _normalised(answer), parts)
        settled, snapped, _ = settle(page, resolved)
        return (settled, snapped, check(page, settled, snapped), broken), None

    best, reason = attempt(data)
    if best is None:
        return None, reason
    settled, snapped, found, broken = best
    if broken or found["score"]:
        fixed, _ = attempt({**data, "task": "Correct your previous answer.",
                            "previous_answer": snapped_answer(page, settled, snapped),
                            "problems": broken + found["problems"]})
        if fixed is not None and not fixed[3] and (broken or fixed[2]["score"] <= found["score"]):
            best, entry["chosen"] = fixed, "fixed"
    settled, snapped, found, broken = best
    if broken:
        return None, f"the answer did not keep the locked regions: {'; '.join(broken)}"
    entry["regions"] = page_regions(page, settled, snapped)
    entry["spots"] = [[round(v, 2) for v in spot["box"]] for spot in snapped["spots"]]
    entry["check"] = found
    return entry, None


def drawn_page(page: Page, parts: list[dict]) -> dict:
    """A page's layout as the person drew it (`parts`), every region kept exactly and only checked; the result
    entry of an approved page the person changed (source "manual")."""
    width, height = page.rect.width, page.rect.height
    answer = {"parts": [{"kind": part["kind"], "title": part["title"], "continues": part["continues"],
                         "regions": [{"box": region["box"], "orientation": region["orientation"],
                                      "fixed": page_box(region["box"], width, height)}
                                     for region in part["regions"]]} for part in parts]}
    snapped = snap(page, answer)
    return {"index": page.n - 1, "method": "regions", "source": "manual", "reason": None, "requests": 0,
            "chosen": None, "regions": page_regions(page, answer, snapped),
            "spots": [[round(v, 2) for v in spot["box"]] for spot in snapped["spots"]],
            "check": check(page, answer, snapped)}


def region_degrees(region: dict) -> int:
    """The clockwise turn that makes a layout region's text upright; stacked letters are read unturned for now."""
    return 0 if region["orientation"] == "stacked" else int(region["orientation"])


def divided_pages(ctx) -> dict[int, list[dict]] | None:
    """For a step after Layout (regions): the layout regions of every page by page index, or None when the pages
    were not divided into regions (the setting `layout_method` "blocks", or no Layout regions result)."""
    if not ctx.has_result(REGIONS_STEP):
        return None
    data = ctx.result(REGIONS_STEP).read_json(REGIONS_FILE)
    if data["method"] != "regions":
        return None
    return {entry["index"]: entry["regions"] for entry in data["pages"]}
