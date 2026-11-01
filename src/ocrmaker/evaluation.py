"""Scoring the automatic result against a reference transcription (GLOSSARY: Reference transcription).

`compare(ours, reference)` takes both as pages of words in reading order, every box in PDF points
(x0, y0, x1, y1) of the page as displayed. Our words are {"id", "text", "bbox", "review"} (review: manual
review lists the word); reference words are {"text", "bbox"}.

Words are paired by geometry, not by text: a reference word and one of ours overlap when their intersection
covers at least OVERLAP of the smaller box; punctuation (a word without letters or digits) also pairs when
the smaller box's centre lies within NEAR points of the larger one, as a dash boxed at its ink height on one
side and at line height on the other. Overlapping words form groups; a group with one word on each side
is a match, others are a split (one reference word, several of ours), a merge (the reverse) or a tangle.
A word without any partner is missing (reference only) or extra (ours only). Boxes need not agree to the
pixel: a matched pair of words with letters or digits is a misplaced box only when its intersection over
union is below BOX_IOU; punctuation is boxed too differently to judge. A group's text is judged whole, with
a space between its words as a reader copies it (a lost or added space is wrong text), after TEXT_FORMS
unify hyphen characters.

    {"totals": {"reference_words", "correct", <finding kind>: count, ...},
     "pages": [{"index", "totals"}],
     "findings": [{"page" (from 1), "kind", "reference", "ours", "ids", "bbox", "review"}]}

Every count is a number of findings, except reference_words, correct and order_words, which count
reference words. Finding kinds: silent (wrong text manual review does not list), flagged (wrong text it
lists: a review item the reference reads, so a candidate unnecessary review item to judge on the scan),
unnecessary (right text it lists all the same), missing (a silent error too, GLOSSARY), extra, split, merged, tangled, box (a
matched box far off) and order (a run of words, consecutive in the reference, that lies outside the longest
sequence keeping the reference order: one misplaced block of text is one finding).
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

import pymupdf

OVERLAP = 0.5
NEAR = 4.0
BOX_IOU = 0.6
# Characters two transcriptions write differently for the same print: soft, non-breaking and Unicode hyphens.
TEXT_FORMS = str.maketrans({"\u00ad": "-", "\u2010": "-", "\u2011": "-"})


def compare(ours: list[list[dict]], reference: list[list[dict]]) -> dict:
    """The score of our pages of words against the reference pages (format in the module docstring)."""
    pages = []
    for index in range(max(len(ours), len(reference))):
        page_ours = ours[index] if index < len(ours) else []
        page_reference = reference[index] if index < len(reference) else []
        pages.append(_compare_page(index, page_ours, page_reference))
    totals = {}
    for page in pages:
        for key, value in page["totals"].items():
            totals[key] = totals.get(key, 0) + value
    return {"totals": totals, "pages": [{"index": p["index"], "totals": p["totals"]} for p in pages],
            "findings": [finding for page in pages for finding in page["findings"]]}


def _compare_page(index: int, ours: list[dict], reference: list[dict]) -> dict:
    totals = {"reference_words": len(reference), "correct": 0}
    findings = []

    def record(kind: str, reference_words: list[dict], our_words: list[dict]) -> None:
        totals[kind] = totals.get(kind, 0) + 1
        findings.append({"page": index + 1, "kind": kind, "reference": _abbreviated(_joined(reference_words)),
                         "ours": _abbreviated(_joined(our_words)), "ids": [word["id"] for word in our_words],
                         "bbox": _union(reference_words or our_words),
                         "review": any(word.get("review") for word in our_words)})

    groups = _groups(ours, reference)
    paired = [group for group in groups if group["reference"] and group["ours"]]
    in_order = _increasing([min(group["ours"]) for group in paired])
    run: list[dict] = []
    for position, group in enumerate(paired + [None]):
        if group is not None and position not in in_order:
            run.append(group)
        elif run:
            reference_words = [reference[i] for g in run for i in g["reference"]]
            record("order", reference_words, [ours[i] for g in run for i in g["ours"]])
            totals["order_words"] = totals.get("order_words", 0) + len(reference_words)
            run = []
    for group in groups:
        reference_words = [reference[i] for i in group["reference"]]
        our_words = [ours[i] for i in group["ours"]]
        if not our_words:
            record("missing", reference_words, our_words)
            continue
        if not reference_words:
            record("extra", reference_words, our_words)
            continue
        geometry = _geometry(reference_words, our_words)
        if geometry:
            record(geometry, reference_words, our_words)
        same = _text(reference_words) == _text(our_words)
        review = any(word.get("review") for word in our_words)
        if same:
            totals["correct"] += len(reference_words)
            if review:
                record("unnecessary", reference_words, our_words)
        else:
            record("flagged" if review else "silent", reference_words, our_words)
    return {"index": index, "totals": totals, "findings": findings}


def _groups(ours: list[dict], reference: list[dict]) -> list[dict]:
    """Connected groups of overlapping words: {"reference": [indices], "ours": [indices]}, in reference order."""
    parent = list(range(len(reference) + len(ours)))

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for r, reference_word in enumerate(reference):
        for o, our_word in enumerate(ours):
            if _paired(reference_word, our_word):
                parent[find(r)] = find(len(reference) + o)
    groups: dict[int, dict] = {}
    for node in range(len(parent)):
        group = groups.setdefault(find(node), {"reference": [], "ours": []})
        if node < len(reference):
            group["reference"].append(node)
        else:
            group["ours"].append(node - len(reference))
    return sorted(groups.values(), key=lambda g: (min(g["reference"], default=len(reference)),
                                                  min(g["ours"], default=len(ours))))


def _increasing(values: list[int]) -> set[int]:
    """Positions of a longest strictly increasing subsequence: the words that keep the reference order."""
    tails, tail_positions, previous = [], [], [None] * len(values)
    for position, value in enumerate(values):
        low, high = 0, len(tails)
        while low < high:
            middle = (low + high) // 2
            if tails[middle] < value:
                low = middle + 1
            else:
                high = middle
        previous[position] = tail_positions[low - 1] if low else None
        if low == len(tails):
            tails.append(value)
            tail_positions.append(position)
        else:
            tails[low], tail_positions[low] = value, position
    kept, position = set(), tail_positions[-1] if tail_positions else None
    while position is not None:
        kept.add(position)
        position = previous[position]
    return kept


def _geometry(reference_words: list[dict], our_words: list[dict]) -> str | None:
    """What is wrong with the boxes of a group that has words on both sides, or None."""
    if len(reference_words) > 1 and len(our_words) > 1:
        return "tangled"
    if len(our_words) > 1:
        return "split"
    if len(reference_words) > 1:
        return "merged"
    if not any(_letters(word) for word in reference_words + our_words):
        return None
    a, b = reference_words[0]["bbox"], our_words[0]["bbox"]
    intersection = _intersection(a, b)
    return "box" if intersection < BOX_IOU * (_area(a) + _area(b) - intersection) else None


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a, b) -> float:
    return _area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def _overlaps(a, b) -> bool:
    smaller = min(_area(a), _area(b))
    return smaller > 0 and _intersection(a, b) >= OVERLAP * smaller


def _paired(reference_word: dict, our_word: dict) -> bool:
    a, b = reference_word["bbox"], our_word["bbox"]
    if _overlaps(a, b):
        return True
    if any(_letters(word) for word in (reference_word, our_word)):
        return False
    small, large = sorted((a, b), key=_area)
    x, y = (small[0] + small[2]) / 2, (small[1] + small[3]) / 2
    return large[0] - NEAR <= x <= large[2] + NEAR and large[1] - NEAR <= y <= large[3] + NEAR


def _letters(word: dict) -> bool:
    return any(char.isalnum() for char in word["text"])


def _text(words: list[dict]) -> str:
    """A group's text as compared: its words in normal form, one space apart."""
    return _joined(words).translate(TEXT_FORMS)


def _joined(words: list[dict]) -> str:
    return " ".join(unicodedata.normalize("NFC", word["text"].strip()) for word in words)


def _abbreviated(text: str, words: int = 8) -> str:
    """A long text (a misplaced block) as its first and last words."""
    parts = text.split(" ")
    return text if len(parts) <= 2 * words else " ".join(parts[:words] + ["…"] + parts[-words:])


def _union(words: list[dict]) -> list[float]:
    boxes = [word["bbox"] for word in words]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def read_reference(pdf: Path, corrections: list[dict] | None = None) -> list[list[dict]]:
    """The reference transcription's pages of words {"text", "bbox"}, in the order of its text layer.

    corrections (findings about the reference itself) are {"page" (from 1), "bbox", "text", "note"}: the
    reference words a correction's box overlaps are replaced by one word with its text and box, or removed
    when its text is empty; a correction overlapping no word adds one, next to its neighbour on the line.
    """
    with pymupdf.open(pdf) as document:
        pages = [[{"text": word[4], "bbox": [round(value, 2) for value in word[:4]]}
                  for word in page.get_text("words", sort=False)] for page in document]
    for correction in corrections or []:
        words = pages[correction["page"] - 1]
        replaced = [i for i, word in enumerate(words) if _overlaps(word["bbox"], correction["bbox"])]
        word = {"text": correction["text"], "bbox": list(correction["bbox"])}
        if replaced:
            position = replaced[0]
            for i in reversed(replaced):
                del words[i]
        else:
            position = _insertion(words, word["bbox"])
        if word["text"]:
            words.insert(position, word)
    return pages


def _insertion(words: list[dict], box) -> int:
    """Where a word missing from the reference goes: after its left neighbour on the line, before its right
    one, or else after the nearest word above it."""
    def same_line(other):
        overlap = min(box[3], other[3]) - max(box[1], other[1])
        return overlap >= OVERLAP * min(box[3] - box[1], other[3] - other[1])

    line = [(i, word["bbox"]) for i, word in enumerate(words) if same_line(word["bbox"])]
    left = [(other[0], i) for i, other in line if other[0] < box[0]]
    if left:
        return max(left)[1] + 1
    right = [(other[0], i) for i, other in line]
    if right:
        return min(right)[1]
    above = [(other["bbox"][3], i) for i, other in enumerate(words) if other["bbox"][3] <= box[1]]
    return max(above)[1] + 1 if above else 0
