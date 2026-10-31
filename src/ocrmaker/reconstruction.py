"""AI reconstruction: the AI determines the printed text of the problem spots, and only of those (ADR 0001).

One request per page with problem spots. For each paragraph (a Layout block) holding spots it carries the
paragraph's full text with the spots marked by word id (`[[p1-w5: Rych4ald]]`, `[[p1-e1: ]]` for an empty
word box), the neighbouring paragraphs or the whole article when it is short, and image crops of the
working raster with the spots outlined: the spot's line with one line above and below for isolated spots,
the whole paragraph when the spots are dense (`crop_ranges`).

The answer lists readings. A reading covers one or more marked ids and gives the printed words for them: one
word for one id, one word for several ids (the boxes are merged), several words for one id (the box is
split). It has a confidence (0-100), a short reasoning, and an error kind: "recognition" (local OCR misread
it; fixed), "publisher" (the print itself is wrong; kept as printed, listed in the report, with the
"intended" form) or "none" (the local reading was right). A reading that names an id not marked, repeats
one, joins ids that are not neighbours or breaks the schema is rejected on its own (`validate`): its spots
stay unresolved, the page's valid readings still count. Only an answer without a list of readings is
rejected as a whole.
New word boxes of merges and splits are measured locally on the raster from the gaps between glyphs and the
words' length ratios (`measure_words`), never taken from the AI.

A reading at or above the setting `reconstruction_accept_confidence` is accepted; below it the spot is
unresolved, as is a spot without an answer (no AI, a rejected item, none given). An unresolved spot never
loses legible text (ADR 0001, `unresolved_text`): a doubtful reading keeps its printed OCR text, and only an
unreadable word (or an empty word box) gets the illegible marker. Either way its local reading is kept as the
hint and the AI's candidate for later steps (Web lookup, Manual review, which lists every unresolved spot).

The AI reconstruction result (`steps/reconstruct/`) holds `reconstruct.json`:

    {"threshold": 80.0,
     "pages": [{"index": 0, "file": "pages/page-0001.json", "spots": 3, "resolved": 2, "markers": 1,
                "kept": 0, "publisher_errors": 0, "rejected": null}],
     "total": {"spots": 3, "resolved": 2, "markers": 1, "kept": 0, "publisher_errors": 0}}

(markers: unresolved words written as the illegible marker; kept: unresolved doubtful readings kept as read)

and one page file per page, `pages/page-0001.json`, holding the reconstruction records:

    {"index": 0,
     "request": {"provider": "claude", "model": "sonnet", "usage": {...} | null} | null,  # null: no request
     "rejected": null,                 # why the page's whole answer was rejected (English), else null
     "ignored": [{"item": {...}, "problems": ["..."]}],  # rejected readings of the answer, with why (English)
     "reconstructions": [{
        "ids": ["p1-w5"],              # the marked OCR word ids it covers, consecutive in one line
        "status": "accepted",          # "accepted", "publisher_error" (kept as printed) or "unresolved"
        "reason": null,                # unresolved only: "low_confidence", "no_answer", "rejected" (the whole
                                       # answer or the spot's reading), "unavailable"
        "source": "ai",                # who settled it ("ai"; Web lookup writes "web")
        "input": {"spots": [{"id", "kind", "signals", "reading", "confidence"}],  # the problem spots
                  "paragraph": "p1-b2", "text": "... [[p1-w5: Rych4ald]] ...",       # as sent
                  "context": {"scope": "article" | "neighbours", "before": "...", "after": "..."},
                  "images": ["crops/page-0001-01.png"]},                             # crops it was shown
        "candidate": ["Rychvald"],     # the AI's words for the ids, or null without an answer
        "confidence": 92.0,            # 0-100, or null
        "reasoning": "...",            # the AI's short reasoning, or null
        "error": "recognition",        # "recognition", "publisher", "none", or null
        "intended": null,              # publisher errors: the form the publisher meant
        "provider": "claude", "model": "sonnet",                                    # null without a request
        "words": [{"id": "p1-w5", "text": "Rychvald", "bbox": [x0, y0, x1, y1],    # what is written in place
                   "hint": "Rych4ald"}]}]}                                           # of the ids (hint: unresolved)

`ocrmaker.transcription` applies the records to the words every output writes.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy
import pymupdf

from . import inkgaps
from .pages import apply_rect, union
from .problems import UNREADABLE
from .transcription import MARKER

RECONSTRUCT_STEP = "reconstruct"
RECONSTRUCT_FILE = "reconstruct.json"
PURPOSE = "reconstruction"
ACCEPTED, PUBLISHER_ERROR, UNRESOLVED = "accepted", "publisher_error", "unresolved"
STATUSES = (ACCEPTED, PUBLISHER_ERROR, UNRESOLVED)
ERRORS = ("recognition", "publisher", "none")
DEFAULT_THRESHOLD = 80.0  # settings.DEFAULTS["reconstruction_accept_confidence"]

# An article of at most this many words is sent whole as the context of its paragraphs.
SHORT_ARTICLE_WORDS = 150
# Spots are dense when their lines (each with one line above and below) cover at least this share of the
# paragraph's lines, or when they would need more than MAX_LINE_CROPS separate crops: the crop is the paragraph.
DENSE_SHARE = 0.5
MAX_LINE_CROPS = 2
# Crops reach this many line heights beyond their lines; outlines are drawn this many pixels off the box.
CROP_MARGIN = 0.4
OUTLINE_OFFSET = 3
OUTLINE_COLOUR = (220, 30, 30)
# Measuring merged and split word boxes: the search reaches this many line heights beyond the AI's boxes
# (blurred ink spreads), but never into the line's other words.
MEASURE_MARGIN = 0.3

INSTRUCTIONS = """\
You reconstruct problem spots in the local OCR of a scanned printed page (a newsletter, magazine or book, \
usually Czech). A problem spot is a word the local OCR could not read reliably: an unreadable word (no text \
or only part of it, often blurred near the binding or a page edge) or a doubtful reading (read, but \
implausibly). Your task is a faithful transcription: give the text exactly as it is printed.

The data lists the spots and, for each paragraph holding spots, the paragraph's full text in which every \
spot is marked as [[id: local reading]] (an empty reading means no text was recognised), the text around \
it, and image crops of the page. Each crop lists the spots it shows; they are outlined in red, in reading \
order (line by line, left to right). Read the print there.

Rules:
- Answer only for the marked ids. Never change, add or mention any other word: everything outside the \
marks is read correctly and stays as it is.
- Give for each reading the ids it covers and the printed words for them, exactly as printed, with \
punctuation attached as printed (a line-end hyphen stays). Never join words across lines.
- Usually one id is one word. If the local OCR split one printed word into several marked boxes, give one \
reading with all of those ids (consecutive in one line) and one word. If one marked box holds several \
printed words, give one reading with that id and the words in order.
- confidence: 0 to 100, how sure you are that the words are what is printed. Use the print as evidence \
first, the context second; when the print does not show the word, say so with a low confidence.
- error: "recognition" when the local reading is wrong or missing and you fixed it; "publisher" when the \
print itself contains a mistake (a typo of the publisher): then give the words exactly as misprinted and \
put the form the publisher meant into "intended"; "none" when the local reading is already right (a name \
or a word missing from the dictionary, say). "intended" is null unless the error is "publisher".
- reasoning: one short sentence.
- Leave out a spot you cannot read at all rather than guess. Answer only with JSON matching the schema."""

SCHEMA = {
    "type": "object",
    "properties": {"readings": {"type": "array", "items": {
        "type": "object",
        "properties": {"ids": {"type": "array", "items": {"type": "string"}},
                       "words": {"type": "array", "items": {"type": "string"}},
                       "confidence": {"type": "number"},
                       "error": {"type": "string", "enum": list(ERRORS)},
                       "reasoning": {"type": "string"},
                       "intended": {"type": ["string", "null"]}},
        "required": ["ids", "words", "confidence", "error", "reasoning", "intended"],
        "additionalProperties": False}}},
    "required": ["readings"],
    "additionalProperties": False,
}


def unresolved_text(spot: dict, word: dict) -> str:
    """What an unresolved spot keeps written (ADR 0001): a doubtful reading its printed OCR text, an
    unreadable word (or an empty word box) the illegible marker."""
    return MARKER if spot["kind"] == UNREADABLE or not word["text"] else word["text"]


def marked(word: dict) -> str:
    return f"[[{word['id']}: {word['text']}]]"


def text_of(lines: list[list[str]], words: dict[str, dict], spots: set[str]) -> str:
    """A paragraph's text, one printed line per text line, with the spots marked by id."""
    return "\n".join(" ".join(marked(words[word]) if word in spots else words[word]["text"]
                              for word in line if word in words and (word in spots or words[word]["text"]))
                     for line in lines)


def paragraphs(recognised: dict, layout: dict | None, spots: list[dict]) -> list[dict]:
    """The paragraphs holding spots, in Layout's numbering: {"id", "kind", "lines" (word ids), "block"}.

    A spot no block holds (there should be none) gets its OCR line as a paragraph of its own.
    """
    blocks = (layout or {}).get("blocks", [])
    holder = {word: block for block in blocks for line in block["lines"] for word in line}
    found, seen = [], set()
    for spot in spots:
        block = holder.get(spot["id"])
        key = block["id"] if block else spot["line"]
        if key in seen:
            continue
        seen.add(key)
        if block:
            found.append({"id": block["id"], "kind": block["kind"], "lines": block["lines"], "block": block})
        else:
            line = next(line for line in recognised["lines"] if line["id"] == spot["line"])
            found.append({"id": line["id"], "kind": "body", "lines": [[word["id"] for word in line["words"]]],
                          "block": None})
    order = {block["id"]: block["number"] for block in blocks}
    return sorted(found, key=lambda paragraph: order.get(paragraph["id"], math.inf))


def context(paragraph: dict, layout: dict | None, articles: list[dict] | None, words: dict[str, dict],
            spots: set[str]) -> dict:
    """The text around a paragraph: the whole article when it is short, else the neighbouring paragraphs."""
    blocks = {block["id"]: block for block in (layout or {}).get("blocks", [])}
    if paragraph["block"] is None:
        return {"scope": "neighbours", "before": "", "after": ""}
    article = next((article["blocks"] for article in articles or [] if paragraph["id"] in article["blocks"]),
                   [block["id"] for block in (layout or {}).get("blocks", [])])
    position = article.index(paragraph["id"])
    size = sum(len(line) for block in article for line in blocks[block]["lines"])

    def joined(ids: list[str]) -> str:
        return "\n\n".join(text_of(blocks[block]["lines"], words, spots) for block in ids)

    if size <= SHORT_ARTICLE_WORDS:
        return {"scope": "article", "before": joined(article[:position]), "after": joined(article[position + 1:])}
    return {"scope": "neighbours", "before": joined(article[max(0, position - 1):position]),
            "after": joined(article[position + 1:position + 2])}


def crop_ranges(line_count: int, spot_lines: list[int]) -> tuple[str, list[tuple[int, int]]]:
    """Which lines (0-based, inclusive ranges) the crops of a paragraph cover, and whether it is the paragraph.

    Isolated spots get their line with one line above and below, neighbouring ranges joined; dense spots get
    the whole paragraph."""
    ranges: list[list[int]] = []
    for line in sorted(set(spot_lines)):
        first, last = max(0, line - 1), min(line_count - 1, line + 1)
        if ranges and first <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], last)
        else:
            ranges.append([first, last])
    covered = sum(last - first + 1 for first, last in ranges)
    if line_count > 1 and (covered >= DENSE_SHARE * line_count or len(ranges) > MAX_LINE_CROPS):
        return "paragraph", [(0, line_count - 1)]
    return "lines", [(first, last) for first, last in ranges]


def render_crop(ink: inkgaps.InkPage, to_raster, box: list[float], outlined: list[list[float]],
                line_height: float, target: Path) -> Path:
    """Save the crop of the working raster around a source page box, with the outlined boxes drawn in red."""
    x0, y0, x1, y1 = apply_rect(to_raster, box)
    margin = CROP_MARGIN * line_height
    left, top = max(0, int(x0 - margin)), max(0, int(y0 - margin))
    right, bottom = min(ink.width, math.ceil(x1 + margin)), min(ink.height, math.ceil(y1 + margin))
    grey = ink.grey[top:bottom, left:right].astype(numpy.uint8)
    rgb = numpy.repeat(grey[:, :, None], 3, axis=2)
    for spot in outlined:
        sx0, sy0, sx1, sy1 = apply_rect(to_raster, spot)
        a, b = int(sx0 - left) - OUTLINE_OFFSET, math.ceil(sx1 - left) + OUTLINE_OFFSET
        c, d = int(sy0 - top) - OUTLINE_OFFSET, math.ceil(sy1 - top) + OUTLINE_OFFSET
        a, c = max(0, a), max(0, c)
        b, d = min(rgb.shape[1] - 1, b), min(rgb.shape[0] - 1, d)
        if a >= b or c >= d:
            continue
        for rows, cols in ((slice(c, c + 2), slice(a, b + 1)), (slice(d - 1, d + 1), slice(a, b + 1)),
                           (slice(c, d + 1), slice(a, a + 2)), (slice(c, d + 1), slice(b - 1, b + 1))):
            rgb[rows, cols] = OUTLINE_COLOUR
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, rgb.shape[1], rgb.shape[0], numpy.ascontiguousarray(rgb).tobytes(), False)
    pixmap.save(str(target))
    return target


def validate(answer, spots: dict[str, dict], positions: dict[str, tuple[int, int]]) -> tuple[list[dict], list[dict]]:
    """The valid readings of an answer and the rejected items, each {"item", "problems"} (English).

    positions: OCR word id -> (line, place in line). A reading is rejected when it names an id not marked,
    joins ids that are not neighbours in one line, or breaks the schema; every reading naming an id another
    reading also names is rejected, since the two contradict each other. An answer without a list of readings
    raises ValueError: it is rejected as a whole.
    """
    if not isinstance(answer, dict) or not isinstance(answer.get("readings"), list):
        raise ValueError("the answer has no list of readings")
    checked = [(reading, _problems(reading, spots, positions)) for reading in answer["readings"]]
    named: dict[str, int] = {}
    for reading, problems in checked:
        if not problems:
            for word_id in set(reading["ids"]):
                named[word_id] = named.get(word_id, 0) + 1
    valid, rejected = [], []
    for reading, problems in checked:
        if not problems:
            repeated = [word_id for word_id in reading["ids"] if named[word_id] > 1]
            if repeated or len(set(reading["ids"])) != len(reading["ids"]):
                problems = [f"the reading repeats ids: {', '.join(repeated or reading['ids'])}"]
        if problems:
            rejected.append({"item": reading, "problems": problems})
        else:
            valid.append(reading)
    return valid, rejected


def _problems(reading, spots: dict[str, dict], positions: dict[str, tuple[int, int]]) -> list[str]:
    """What is wrong with one reading of an answer (English, empty when valid)."""
    if not isinstance(reading, dict):
        return ["the reading is not an object"]
    ids, words = reading.get("ids"), reading.get("words")
    if not isinstance(ids, list) or not ids or not all(isinstance(word_id, str) for word_id in ids):
        return ["the reading names no ids"]
    problems = []
    unmarked = [word_id for word_id in ids if word_id not in spots]
    if unmarked:
        problems.append(f"the reading addresses ids that are not marked: {', '.join(unmarked)}")
    else:
        places = sorted(positions[word_id] for word_id in set(ids))
        if len({line for line, _ in places}) != 1 or \
                [place for _, place in places] != list(range(places[0][1], places[0][1] + len(places))):
            problems.append("the reading joins ids that are not neighbours in one line")
    if not isinstance(words, list) or not words or not all(
            isinstance(word, str) and word.strip() == word and word and not any(c.isspace() for c in word)
            for word in words):
        problems.append("the reading has no valid words (each one printed word without spaces)")
    confidence = reading.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
        problems.append(f"the reading has an invalid confidence {confidence!r}")
    if reading.get("error") not in ERRORS:
        problems.append(f"the reading has an unknown error kind {reading.get('error')!r}")
    intended = reading.get("intended")
    if intended is not None and not isinstance(intended, str):
        problems.append("the reading has an invalid intended form")
    if not isinstance(reading.get("reasoning"), str):
        problems.append("the reading has no reasoning")
    return problems


def place(ink: inkgaps.InkPage | None, raster: dict, line: dict, words: dict[str, dict], ids: list[str],
          texts: list[str]) -> list[dict]:
    """The words written in place of consecutive marked ids of one OCR line: [{"id", "text", "bbox"}].

    One word for one box keeps the box; anything else (a merge, a split) gets boxes measured on the ink
    (`measure_words`). Split parts beyond the ids get the ids `<first id>-N`. ink may be None when only
    one-to-one words are placed.
    """
    if len(ids) == 1 and len(texts) == 1:
        return [{"id": ids[0], "text": texts[0], "bbox": words[ids[0]]["bbox"]}]
    others = [word["bbox"] for word in line["words"] if word["id"] not in ids]
    boxes = measure_words(ink, raster["to_raster"], raster["to_source"], [words[word_id]["bbox"] for word_id in ids],
                          line["bbox"], others, texts)
    names = ids[:len(texts)] + [f"{ids[0]}-{number}" for number in range(len(ids) + 1, len(texts) + 1)]
    return [{"id": name, "text": text, "bbox": box} for name, text, box in zip(names, texts, boxes)]


def measure_words(ink: inkgaps.InkPage, to_raster, to_source, boxes: list[list[float]], line_box: list[float],
                  others: list[list[float]], texts: list[str]) -> list[list[float]]:
    """Word boxes (source page coordinates) for texts printed where the given boxes lie, measured on the ink.

    The ink is searched from the boxes' union a little outwards along their line, but never into the line's
    other words. Several texts are cut apart at the gaps between glyphs nearest to where the texts' length
    ratios put the cuts; each word box is then the tight box of its ink.
    """
    pixels = [apply_rect(to_raster, box) for box in boxes]
    line = apply_rect(to_raster, line_box)
    top = max(0, int(min([line[1]] + [box[1] for box in pixels])))
    bottom = min(ink.height, math.ceil(max([line[3]] + [box[3] for box in pixels])))
    height = max(1, bottom - top)
    start = min(box[0] for box in pixels) - MEASURE_MARGIN * height
    end = max(box[2] for box in pixels) + MEASURE_MARGIN * height
    lo, hi = min(box[0] for box in pixels), max(box[2] for box in pixels)
    for other in (apply_rect(to_raster, box) for box in others):
        if other[2] <= lo + 1:
            start = max(start, other[2])
        elif other[0] >= hi - 1:
            end = min(end, other[0])
    left, right = max(0, int(start)), min(ink.width, math.ceil(end))
    whole = union(boxes)
    if right <= left:
        return _proportional(whole, texts)
    mask = ink.ink(top, bottom, around=max(2, round(height / 2)))[:, left:right]
    columns = mask.any(axis=0)
    runs = inkgaps.ink_runs(columns)
    if not runs:
        return _proportional(whole, texts)
    first, last = runs[0][0], runs[-1][1]
    weights = [max(1, len(text)) for text in texts]
    total = sum(weights)
    unit = (last - first) / total
    gaps = [(a[1], b[0]) for a, b in zip(runs, runs[1:])]
    cuts, floor = [], first
    for position in range(1, len(texts)):
        expected = first + unit * sum(weights[:position])
        choices = [gap for gap in gaps if gap[0] >= floor]
        if choices:
            gap = max(choices, key=lambda g: (g[1] - g[0]) / (1 + abs((g[0] + g[1]) / 2 - expected) / max(unit, 1)))
            if abs((gap[0] + gap[1]) / 2 - expected) > 1.5 * unit * max(weights[position - 1], weights[position]):
                gap = (round(expected), round(expected))
        else:
            gap = (max(floor, round(expected)), max(floor, round(expected)))
        cuts.append(gap)
        floor = gap[1]
    bounds = [first] + [value for gap in cuts for value in gap] + [last]
    measured = []
    for a, b in zip(bounds[::2], bounds[1::2]):
        part = mask[:, a:b] if b > a else mask[:, a:a + 1]
        cols = numpy.flatnonzero(part.any(axis=0))
        rows = numpy.flatnonzero(part.any(axis=1))
        if not cols.size:
            box = [left + a, top, left + max(b, a + 1), bottom]
        else:
            box = [left + a + cols[0], top + rows[0], left + a + cols[-1] + 1, top + rows[-1] + 1]
        measured.append([round(value, 2) for value in apply_rect(to_source, box)])
    return measured


def _proportional(box: list[float], texts: list[str]) -> list[list[float]]:
    """The box cut by the texts' length ratios: the fallback when there is no ink to measure."""
    weights = [max(1, len(text)) for text in texts]
    total, x, boxes = sum(weights), box[0], []
    for weight in weights:
        width = (box[2] - box[0]) * weight / total
        boxes.append([round(x, 2), box[1], round(x + width, 2), box[3]])
        x += width
    return boxes
