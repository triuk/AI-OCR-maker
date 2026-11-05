"""Layout: blocks built from recognised lines and words, the format Layout stores and later steps read.

Every recognised word belongs to exactly one block (ADR 0005). Blocks are built locally from geometry:
Tesseract's lines are split at wide gaps into line pieces, grid-like rows of short pieces become tables,
the remaining pieces are chained downwards into paragraphs (a short closing line stays with its paragraph
even when Tesseract measures its letters larger), small stray artefacts (specks without letters or digits)
join the nearest block, and each block gets a kind from its size and position. Blocks are numbered in a geometric reading order (running headers first, then a recursive cut
into columns and bands, running footers last); that order is also the fallback reading order.

The Layout result (`steps/layout/`) holds `layout.json`:

    {"pages": [{"index": 0, "file": "pages/page-0001.json", "blocks": 7, "kinds": {"body": 4, "heading": 2, ...}}]}

and one page file per page, `pages/page-0001.json`:

    {"index": 0,
     "columns": [[x0, y0, x1, y1], ...],     # column regions, source page coordinates
     "blocks": [{                            # in geometric order; number = position + 1
        "id": "p1-b1",                       # unique in the project
        "number": 1,                         # the number shown to the AI and in the step previews
        "kind": "heading",                   # one of KINDS
        "bbox": [x0, y0, x1, y1],            # union of its word boxes, source page coordinates
        "column": 0,                         # index into "columns"
        "lines": [["p1-w1", "p1-w2"], ...]}]} # word ids: lines top to bottom, words left to right;
                                             # a table's lines are its rows, read across the cells

Blocks hold word ids only; the words' text and boxes stay in the Local OCR result (`ocrmaker.words`), so
later reconstructions and corrections apply to them without touching the layout. Geometry is computed on
the page as displayed (`pages.display_matrix`) and stored in source page coordinates.
"""
from __future__ import annotations

import re
import statistics
from collections.abc import Iterator

from .pages import apply_rect, display_matrix, display_size, invert, union

LAYOUT_STEP = "layout"
LAYOUT_FILE = "layout.json"
# Block kinds Layout decides from the words. Ads are recognised from the page image by the reading order
# AI (an article group of kind "ad", see ocrmaker.reading_order); Layout never marks them on its own, but its
# step preview shows them once Reading order has run (steps/layout_step.py).
KINDS = ("body", "heading", "caption", "header", "footer", "table")

# Text size is measured as the x-height: the height of these letters (no ascender, descender or accent).
X_LETTERS = frozenset("acemnorsuvwxz")
X_HEIGHT_SHARE = 0.45    # x-height assumed from a line's box height when it has none of those letters

# Tuning. Gaps are in line heights; sizes are relative to the page's body text (its median x-height).
SPLIT_GAP = 1.5          # a gap wider than this many line heights splits a line into pieces
SIZE_GAP = 0.5           # ...and so does a narrower one, wider than this, where the text size changes
CHAIN_GAP = 1.0          # pieces further apart vertically than this start a new block
SIZE_RATIO = 1.2         # pieces whose sizes differ more than this never share a block
HEADING_SIZE = 1.3       # a block at least this much larger than body text is a heading
CAPTION_SIZE = 0.85      # a block at most this large is a caption
EDGE_BAND = 0.1          # running headers and footers lie within this share of the page height
EDGE_GAP = 1.0           # ...and this many body x-heights away from the rest of the page
TABLE_WORDS = 4          # a table cell has at most this many words
TABLE_ROWS = 3           # a table has at least this many rows of at least two aligned cells
CLOSING_WIDTH = 0.5      # a closing line of a paragraph is at most this share of the line above wide
STRAY_SIZE = 2.0         # a stray artefact is at most this many body x-heights wide and high
STRAY_REACH = 3.0        # ...and joins a block at most this many body x-heights away
LABEL_GAP = 6.0          # a label joins the text right of it at most this many line heights away
LABEL_SIZE = 1.5         # ...and at most this much taller (accents and capitals make a short label tall)
KEY_WORDS = 6            # a key of a key column (a date, a time range, "Termín:") has at most this many words
KEY_REACH = 4.0          # ...and another key lining up with it lies at most this many text lines away

# A label opening a list item or a timetable row: a time, an item number or a bullet.
LABEL = re.compile(r"\d{1,2}[.:]\d{2}|\d{1,2}[.)]|[-–•»·*]")


def page_file(index: int) -> str:
    return f"pages/page-{index + 1:04d}.json"


def read_layout_page(result, index: int) -> dict | None:
    """The blocks of one page (0-based index), or None for a page the result lacks."""
    name = page_file(index)
    return result.read_json(name) if result.exists(name) else None


def block_words(block: dict) -> Iterator[str]:
    """The word ids of a block in its reading order."""
    for line in block["lines"]:
        yield from line


# Geometry helpers on [x0, y0, x1, y1] lists (display coordinates while building). -----------------------

def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return min(a1, b1) - max(a0, b0)


class _Piece:
    """Words of one line between wide gaps, in display coordinates.

    Its size is the x-height: the median height of its letters without ascenders, descenders or accents.
    Unlike a box height it does not depend on which letters (or Czech capitals with a caron) a line holds.
    """

    def __init__(self, words: list[dict], text_start: int = 0):
        self.words = words  # each: {"id", "box" (display), "bbox" (source), "x_heights" [display heights]}
        self.box = union(word["box"] for word in words)
        heights = [height for word in words for height in word["x_heights"]]
        # None when the piece has no such letter (digits, capitals): its size is unknown, not small.
        self.size = statistics.median(heights) if heights else None
        # The first word of its text: after a joined label's words, its text starts further right.
        self.text_start = text_start

    @property
    def text_left(self) -> float:
        return self.words[self.text_start]["box"][0]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def centre_y(self) -> float:
        return (self.box[1] + self.box[3]) / 2


class _Block:
    def __init__(self, lines: list[_Piece], kind: str = "body"):
        self.lines = lines
        self.kind = kind
        self.box = union(line.box for line in lines)
        self.column = 0

    @property
    def size(self) -> float | None:
        known = [line.size for line in self.lines if line.size is not None]
        return statistics.median(known) if known else None


def _x_heights(word: dict, matrix) -> list[float]:
    heights = []
    for char in word.get("chars") or ():
        if char["text"] in X_LETTERS:
            box = apply_rect(matrix, char["bbox"])
            heights.append(box[3] - box[1])
    return heights


def _pieces(lines: list[dict], matrix) -> list[_Piece]:
    pieces = []
    for line in lines:
        words = sorted(({"id": word["id"], "bbox": word["bbox"], "box": apply_rect(matrix, word["bbox"]),
                         "x_heights": _x_heights(word, matrix)}
                        for word in line["words"]), key=lambda word: word["box"][0])
        if not words:
            continue
        height = statistics.median(word["box"][3] - word["box"][1] for word in words)
        current = [words[0]]
        for word in words[1:]:
            if word["box"][0] - current[-1]["box"][2] > SPLIT_GAP * height:
                pieces.extend(_Piece(part) for part in _size_parts(current))
                current = []
            current.append(word)
        pieces.extend(_Piece(part) for part in _size_parts(current))
    return pieces


def _size_parts(words: list[dict]) -> list[list[dict]]:
    """Words of a line piece split where the text size changes at a gap wider than a word space: two headings
    side by side in different sizes, which Tesseract reads as one line a gap too narrow for SPLIT_GAP apart.

    Each side holds at least two words, and both their median x-height and their tallest word's height differ
    by more than SIZE_RATIO (character boxes alone are noisy: a body line's words measure 4.7 and 8.6, and short
    words like "s" have no ascender); the gap is wider than SIZE_GAP line heights and half again the word spaces
    on either side."""
    height = statistics.median(word["box"][3] - word["box"][1] for word in words)
    gaps = [right["box"][0] - left["box"][2] for left, right in zip(words, words[1:])]
    best = None
    for position in range(2, len(words) - 1):
        gap = gaps[position - 1]
        spaces = max(statistics.median(gaps[:position - 1]), statistics.median(gaps[position:]))
        if gap <= SIZE_GAP * height or gap <= 1.5 * spaces:
            continue
        parts = (words[:position], words[position:])
        sides = [[h for word in part for h in word["x_heights"]] for part in parts]
        if not all(sides):
            continue

        def ratio(values: list[float]) -> float:
            low, high = sorted(values)
            return high / max(low, 1e-6)
        x_ratio = ratio([statistics.median(side) for side in sides])
        box_ratio = ratio([max(word["box"][3] - word["box"][1] for word in part) for part in parts])
        if x_ratio > SIZE_RATIO and box_ratio > SIZE_RATIO and (best is None or x_ratio > best[0]):
            best = (x_ratio, position)
    if best is None:
        return [words]
    return _size_parts(words[:best[1]]) + _size_parts(words[best[1]:])


def _text_beside(label: _Piece, pieces: list[_Piece]) -> _Piece | None:
    """The nearest piece right of a label on its line, at most LABEL_GAP of its line heights away and not
    much smaller than the label (a heading's word beside body text is no label), or None."""
    right = [piece for piece in pieces if piece is not label and piece.box[0] >= label.box[2] - 0.5
             and piece.box[0] - label.box[2] <= LABEL_GAP * piece.height
             and _overlap(label.box[1], label.box[3], piece.box[1], piece.box[3]) >= 0.5 * label.height
             and label.height <= LABEL_SIZE * piece.height]
    return min(right, key=lambda piece: piece.box[0]) if right else None


def _join_labels(pieces: list[_Piece], texts: dict[str, str]) -> None:
    """Join each label piece to the text it opens, in place: the text beside it (`_text_beside`).

    A label is a lone time, item number or bullet (LABEL), or a key: at most KEY_WORDS words with a digit or
    ending in a colon (a date, a time range, a form's "Termín:"). A key joins only in a key column, where
    another key within KEY_REACH line heights lines up with it and so do their texts; two columns of text side
    by side never line up so. Such a label stands a gap wider than SPLIT_GAP before its text; as a piece of its
    own it became a block of its own, and the reading order read the labels apart from their texts."""
    def keyed(words: list[dict]) -> bool:
        text = " ".join(texts.get(word["id"], "") for word in words).strip()
        return len(words) <= KEY_WORDS and (any(char.isdigit() for char in text) or text.endswith(":"))

    pairs = []
    for label in pieces:
        lone = len(label.words) == 1 and LABEL.fullmatch(texts.get(label.words[0]["id"], "").strip()) is not None
        beside = _text_beside(label, pieces) if lone or keyed(label.words) else None
        if beside is not None:
            pairs.append((label, beside, lone))

    def lined_up(a: _Piece, b: _Piece) -> bool:
        return abs(a.box[0] - b.box[0]) < 0.6 * max(a.height, b.height)

    keys = [(label, text) for label, text, lone in pairs if not lone and any(
        other is not label and lined_up(label, other) and lined_up(text, other_text)
        and abs(label.centre_y - other.centre_y) <= KEY_REACH * max(text.height, other_text.height)
        for other, other_text, _ in pairs)]
    joins = [(label, text) for label, text, lone in pairs if lone] + keys
    # Right to left, so that a row of a key, a bullet and a text ("od 8.00 do 9.00 hod.", "-", "areál")
    # joins the bullet to the text first and then the key to both; `holder` follows each piece into its join.
    holder: dict[int, _Piece] = {}
    for label, text in sorted(joins, key=lambda pair: -pair[0].box[0]):
        label_now, text_now = holder.get(id(label), label), holder.get(id(text), text)
        if label_now is text_now:
            continue
        joined = _Piece(label_now.words + text_now.words, text_start=len(label_now.words))
        pieces[pieces.index(text_now)] = joined
        pieces.remove(label_now)
        for piece in (label, text, label_now, text_now):
            holder[id(piece)] = joined
        for original, now in holder.items():
            if now in (label_now, text_now):
                holder[original] = joined
    # A row of a key column whose key stands too close to its text to be a piece of its own: a piece starting
    # at the column's keys with a word, after a key, starting at the column's texts. Its text starts there.
    for piece in pieces:
        if piece.text_start:
            continue
        for label, text in keys:
            reach = 0.6 * text.height
            if abs(piece.centre_y - label.centre_y) > KEY_REACH * text.height \
                    or abs(piece.box[0] - label.box[0]) >= reach:
                continue
            starts = [position for position in range(1, len(piece.words)) if keyed(piece.words[:position])
                      and abs(piece.words[position]["box"][0] - text.box[0]) < reach]
            if starts:
                piece.text_start = starts[0]
                break


def _tables(pieces: list[_Piece], body: float, page_width: float) -> list[_Block]:
    """Grid-like runs of rows of short pieces; their pieces are removed from `pieces`."""
    candidates = sorted((piece for piece in pieces
                         if len(piece.words) <= TABLE_WORDS
                         and (piece.size is None or piece.size < HEADING_SIZE * body)),
                        key=lambda piece: piece.centre_y)
    bands: list[list[_Piece]] = []
    for piece in candidates:
        if bands and abs(piece.centre_y - bands[-1][0].centre_y) <= 0.5 * bands[-1][0].height:
            bands[-1].append(piece)
        else:
            bands.append([piece])
    rows = []
    for band in bands:
        band.sort(key=lambda piece: piece.box[0])
        row = [band[0]]
        for piece in band[1:]:
            if piece.box[0] - row[-1].box[2] > 0.3 * page_width:
                rows.append(row)
                row = []
            row.append(piece)
        rows.append(row)
    rows = [row for row in rows if len(row) >= 2]

    def lined_up(cell: _Piece, other: _Piece) -> bool:
        """Cells of one table column: left edges, right edges or centres in line."""
        tolerance = 0.6 * max(cell.height, other.height)
        return abs(cell.box[0] - other.box[0]) < tolerance or abs(cell.box[2] - other.box[2]) < tolerance \
            or abs((cell.box[0] + cell.box[2]) / 2 - (other.box[0] + other.box[2]) / 2) < tolerance

    def aligned(upper: list[_Piece], lower: list[_Piece]) -> bool:
        height = max(piece.height for piece in upper + lower)
        gap = min(p.box[1] for p in lower) - max(p.box[3] for p in upper)
        if not -0.2 * height <= gap <= 2.0 * height:
            return False
        return sum(1 for cell in upper if any(lined_up(cell, other) for other in lower)) >= 2

    rows.sort(key=lambda row: min(p.box[1] for p in row))
    tables, run = [], []
    for row in rows:
        if run and aligned(run[-1], row):
            run.append(row)
            continue
        if len(run) >= TABLE_ROWS:
            tables.append(run)
        run = [row]
    if len(run) >= TABLE_ROWS:
        tables.append(run)

    blocks, used = [], set()
    for run in tables:
        # Only cells lined up with a cell of a neighbouring row belong to the table; text beside it
        # (a column line that happens to be short) stays out.
        members = [cell for position, row in enumerate(run) for cell in row
                   if any(lined_up(cell, other) for near in run[max(position - 1, 0):position + 2]
                          if near is not row for other in near)]
        used.update(id(piece) for piece in members)
        area = union(piece.box for piece in members)
        # Longer cells inside the grid belong to it as well.
        for piece in pieces:
            if id(piece) not in used and area[0] <= (piece.box[0] + piece.box[2]) / 2 <= area[2] \
                    and area[1] <= piece.centre_y <= area[3]:
                members.append(piece)
                used.add(id(piece))
        bands = []
        for piece in sorted(members, key=lambda piece: piece.centre_y):
            if bands and abs(piece.centre_y - statistics.mean(p.centre_y for p in bands[-1])) \
                    <= 0.5 * bands[-1][0].height:
                bands[-1].append(piece)
            else:
                bands.append([piece])
        lines = [_Piece([word for piece in sorted(band, key=lambda p: p.box[0]) for word in piece.words])
                 for band in bands]
        # A row keeps its cells' words left to right, so a table reads row by row.
        for line in lines:
            line.words.sort(key=lambda word: word["box"][0])
        blocks.append(_Block(lines, "table"))
    pieces[:] = [piece for piece in pieces if id(piece) not in used]
    return blocks


def _chains(pieces: list[_Piece]) -> list[list[_Piece]]:
    """Pieces linked downwards into runs of lines: each piece continues the nearest fitting piece above."""
    ordered = sorted(pieces, key=lambda piece: (piece.box[1], piece.box[0]))
    successor: dict[int, _Piece] = {}
    predecessor: dict[int, _Piece] = {}
    for piece in ordered:
        best = None
        for above in ordered:
            if above is piece or id(above) in successor:
                continue
            gap = piece.box[1] - above.box[3]
            limit = max(piece.height, above.height)
            if gap < -0.3 * limit or gap > CHAIN_GAP * limit:
                continue
            if above.box[1] >= piece.box[1]:
                continue
            overlap = _overlap(above.box[0], above.box[2], piece.box[0], piece.box[2])
            if overlap < 0.3 * min(above.width, piece.width):
                continue
            sizes = (above.size, piece.size)
            if None not in sizes and max(sizes) / max(min(sizes), 1e-6) > SIZE_RATIO \
                    and not _closing_line(above, piece):
                continue
            score = (gap, -overlap)
            if best is None or score < best[0]:
                best = (score, above)
        if best is not None:
            successor[id(best[1])] = piece
            predecessor[id(piece)] = best[1]
    chains = []
    for piece in ordered:
        if id(piece) in predecessor:
            continue
        chain = [piece]
        while id(chain[-1]) in successor:
            chain.append(successor[id(chain[-1])])
        chains.append(chain)
    return chains


def _closing_line(above: _Piece, piece: _Piece) -> bool:
    """Whether a piece looks like the short last line of the paragraph line above it: much narrower, within
    the line's width, no taller, and measured larger. Its size is then no reason to start a block: Tesseract
    measures the letters of a line of one or two words unreliably (often at the word's full height), never
    smaller. A smaller narrow line is body text under a heading, such as one column's under a spanning one."""
    reach = above.height
    return (piece.width <= CLOSING_WIDTH * above.width and piece.box[0] >= above.box[0] - reach
            and piece.box[2] <= above.box[2] + reach and piece.height <= SIZE_RATIO * above.height
            and None not in (above.size, piece.size) and piece.size > above.size)


def _stray(piece: _Piece, texts: dict[str, str], body: float) -> bool:
    """A small stray artefact: a piece without any letter or digit (a speck read as "-" or ".", an empty word
    box of a speck), at most STRAY_SIZE body x-heights wide and high."""
    return (not any(char.isalnum() for word in piece.words for char in texts.get(word["id"], ""))
            and piece.width <= STRAY_SIZE * body and piece.height <= STRAY_SIZE * body)


def _attach_strays(blocks: list[_Block], texts: dict[str, str], body: float) -> list[_Block]:
    """Blocks of one stray artefact joined to the nearest block above them (or else beside or below them) that
    lies within STRAY_REACH body x-heights; a stray with no block near stays a block of its own."""
    strays = [block for block in blocks if len(block.lines) == 1 and _stray(block.lines[0], texts, body)]
    kept = [block for block in blocks if block not in strays]
    reach = STRAY_REACH * body
    for stray in strays:
        x, y = (stray.box[0] + stray.box[2]) / 2, (stray.box[1] + stray.box[3]) / 2

        def distance(block: _Block) -> float:
            return max(block.box[0] - x, 0, x - block.box[2]) + max(block.box[1] - y, 0, y - block.box[3])
        near = [block for block in kept if block.kind != "table" and distance(block) <= reach]
        if not near:
            kept.append(stray)
            continue
        target = min(near, key=lambda block: (block.box[1] > y, distance(block)))
        target.lines = sorted(target.lines + stray.lines, key=lambda line: (line.box[1], line.box[0]))
        target.box = union(line.box for line in target.lines)
    return kept


def _paragraphs(chain: list[_Piece]) -> list[list[_Piece]]:
    """A run of lines split into paragraphs at wider gaps, first-line indents, closing lines and labels.

    A line closes its paragraph when the next line's first word would still have fitted after it, so the
    line break was not forced; this holds for justified and ragged text alike. A line opened by a label
    (`_join_labels`) starts a list item or timetable row of its own.
    """
    if len(chain) < 3:
        paragraphs = [[chain[0]]]
        for lower in chain[1:]:
            if lower.text_start:
                paragraphs.append([])
            paragraphs[-1].append(lower)
        return paragraphs
    # Edges are those of the text: a label before a line's text (a time, a bullet) hangs out to the left.
    left = statistics.median(line.text_left for line in chain)
    right = max(line.box[2] for line in chain)
    width = max(right - left, 1e-6)
    height = statistics.median(line.height for line in chain)
    gaps = [lower.box[1] - upper.box[3] for upper, lower in zip(chain, chain[1:])]
    usual = statistics.median(gaps)
    paragraphs = [[chain[0]]]
    for upper, lower, gap in zip(chain, chain[1:], gaps):
        wide = gap > max(1.8 * usual, usual + 0.5 * height)
        indented = lower.text_left - left > 0.8 * height and upper.text_left - left < 0.4 * height
        first = lower.words[lower.text_start]["box"]
        short = right - upper.box[2] > (first[2] - first[0]) + 0.6 * height \
            and lower.box[2] - left > 0.5 * width
        if wide or indented or short or lower.text_start:
            paragraphs.append([])
        paragraphs[-1].append(lower)
    return paragraphs


def _kinds(blocks: list[_Block], body: float, page_height: float) -> None:
    for block in blocks:
        if block.kind != "body" or block.size is None:
            continue
        if block.size >= HEADING_SIZE * body and len(block.lines) <= 4:
            block.kind = "heading"
        elif block.size <= CAPTION_SIZE * body:
            block.kind = "caption"

    # Running headers and footers: short, not large, inside the edge band and apart from everything else.
    def edge_candidate(block: _Block, top: bool) -> bool:
        if len(block.lines) > 2 or block.kind in ("table", "heading") \
                or (block.size is not None and block.size > 1.15 * body):
            return False
        return block.box[3] <= EDGE_BAND * page_height if top else block.box[1] >= (1 - EDGE_BAND) * page_height

    for top, kind in ((True, "header"), (False, "footer")):
        edge = [block for block in blocks if edge_candidate(block, top)]
        while edge:  # drop candidates that are not apart from the rest, until the rest stops growing
            rest = [block for block in blocks if block not in edge]
            if not rest:
                break
            if top:
                limit = min(block.box[1] for block in rest)
                kept = [block for block in edge if block.box[3] + EDGE_GAP * body <= limit]
            else:
                limit = max(block.box[3] for block in rest)
                kept = [block for block in edge if block.box[1] - EDGE_GAP * body >= limit]
            if len(kept) == len(edge):
                break
            edge = kept
        for block in edge:
            block.kind = kind


def _geometric(blocks: list[_Block], columns: list[list[_Block]]) -> list[_Block]:
    """Recursive cut: columns left to right first, then bands top to bottom; ties by position."""
    if len(blocks) <= 1:
        return list(blocks)
    for axis in (0, 1):  # 0: vertical cut into columns, 1: horizontal cut into bands
        ordered = sorted(blocks, key=lambda block: (block.box[axis], block.box[1 - axis]))
        groups, reach = [[ordered[0]]], ordered[0].box[axis + 2]
        for block in ordered[1:]:
            if block.box[axis] > reach:
                groups.append([])
            groups[-1].append(block)
            reach = max(reach, block.box[axis + 2])
        if len(groups) > 1:
            if axis == 0:
                for group in groups:
                    columns.append(group)
            return [block for group in groups for block in _geometric(group, columns)]
    return sorted(blocks, key=lambda block: (block.box[1], block.box[0]))


def build_page(page: dict, recognised: dict) -> dict:
    """The layout page file for one prepared page and its recognised lines (see the module docstring)."""
    matrix = display_matrix(page)
    width, height = display_size(page)
    number = page["index"] + 1
    pieces = _pieces(recognised["lines"], matrix)
    if not pieces:
        return {"index": page["index"], "columns": [], "blocks": []}
    # Body text size: the median ascent of lines with several words (single words are often headings).
    sized = [piece.size for piece in pieces if len(piece.words) > 1 and piece.size is not None] \
        or [piece.size for piece in pieces if piece.size is not None]
    body = statistics.median(sized) if sized else X_HEIGHT_SHARE * statistics.median(p.height for p in pieces)
    texts = {word["id"]: word["text"] for line in recognised["lines"] for word in line["words"]}
    blocks = _tables(pieces, body, width)
    _join_labels(pieces, texts)
    for chain in _chains(pieces):
        blocks.extend(_Block(paragraph) for paragraph in _paragraphs(chain))
    blocks = _attach_strays(blocks, texts, body)
    _kinds(blocks, body, height)

    edges = {kind: sorted((block for block in blocks if block.kind == kind),
                          key=lambda block: (block.box[1], block.box[0])) for kind in ("header", "footer")}
    middle = [block for block in blocks if block.kind not in ("header", "footer")]
    column_groups: list[list[_Block]] = []
    ordered = edges["header"] + _geometric(middle, column_groups) + edges["footer"]

    # A block's column is the narrowest column region holding it; blocks outside every column (headers,
    # footers, spanning headings) get a region of their own band.
    regions: list[list[float]] = []
    for group in column_groups:
        region = union(block.box for block in group)
        if region not in regions:
            regions.append(region)
    for block in ordered:
        holding = [i for i, region in enumerate(regions)
                   if region[0] <= block.box[0] + 0.01 and block.box[2] <= region[2] + 0.01
                   and region[1] <= block.box[1] + 0.01 and block.box[3] <= region[3] + 0.01]
        if holding:
            block.column = min(holding, key=lambda i: (regions[i][2] - regions[i][0]) * (regions[i][3] - regions[i][1]))
        else:
            regions.append(list(block.box))
            block.column = len(regions) - 1
    used = sorted({block.column for block in ordered})
    renumber = {old: new for new, old in enumerate(used)}
    inverse = invert(matrix)
    return {"index": page["index"],
            "columns": [_round(apply_rect(inverse, regions[old])) for old in used],
            "blocks": [{"id": f"p{number}-b{position}", "number": position, "kind": block.kind,
                        "bbox": _round(union(word["bbox"] for line in block.lines for word in line.words)),
                        "column": renumber[block.column],
                        "lines": [[word["id"] for word in line.words] for line in block.lines]}
                       for position, block in enumerate(ordered, start=1)]}


def _round(box) -> list[float]:
    return [round(value, 2) for value in box]
