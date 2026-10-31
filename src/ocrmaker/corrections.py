"""Manual corrections: the user's changes from manual review, a layer of their own applied at Export (ADR 0004).

The project keeps them in `corrections.json` (see ocrmaker.store), a list in the order they were first made.
Making one never runs a step; rerunning a step before manual review discards them all, and their count is
what the rerun confirmation states (one correction, one count). Each correction is a dict with:

    {"id": "3f9c2a7b1d04",        # stable, unique in the project
     "kind": "text",              # what it changes; "text" is the only kind so far
     "page": 0,                   # 0-based page index
     "created": iso time, "updated": iso time,
     ...kind-specific fields}

kind "text" sets what is written for a group of OCR words (Local OCR word ids, `ocrmaker.words`):

     "ids": ["p1-w5"],            # the OCR word ids it covers: one word, or the whole group of ids a
                                  # reconstruction record covers (a merge or split stays one correction)
     "text": "Rychvald",          # what is written in their place; spaces split it into several words,
                                  # "" writes nothing there
     "previous": "[NEČITELNÉ]",   # what the automatic steps wrote there when it was made (for the report)
     "source": "queue" | "word"   # where the user made it: the review queue or a clicked word

A correction of a group replaces any earlier correction it shares an id with. Later kinds (region notes,
article order) add their own fields under their own kind; `apply` ignores kinds it does not write.
"""
from __future__ import annotations

from collections.abc import Iterable

TEXT = "text"
# The Review step's result keeps the corrections it handed to Export under this name.
CORRECTIONS_FILE = "corrections.json"


def text_corrections(corrections: Iterable[dict], page: int | None = None) -> list[dict]:
    return [item for item in corrections if item.get("kind") == TEXT and (page is None or item["page"] == page)]


def union(boxes: Iterable[list[float]]) -> list[float]:
    boxes = list(boxes)
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def written_words(ids: list[str], text: str, box: list[float]) -> list[dict]:
    """The words a text correction writes in a box: one per space-separated part, the box cut by length.

    The parts take the group's ids in order, extra parts `<first id>-N` (as reconstruction names split parts).
    """
    parts = text.split()
    if not parts:
        return []
    weights = [max(1, len(part)) for part in parts]
    total, x, words = sum(weights), box[0], []
    names = ids[:len(parts)] + [f"{ids[0]}-{number}" for number in range(len(ids) + 1, len(parts) + 1)]
    for name, part, weight in zip(names, parts, weights):
        width = (box[2] - box[0]) * weight / total
        words.append({"id": name, "text": part, "bbox": [round(x, 2), box[1], round(x + width, 2), box[3]]})
        x += width
    return words


def apply(words: dict[str, list[dict]], recognised: dict, corrections: Iterable[dict]) -> dict[str, list[dict]]:
    """The page's words (`ocrmaker.transcription.page_words`) with the page's text corrections written in.

    A corrected group writes its words under its first id, in the union of its OCR word boxes; its other ids
    write nothing.
    """
    boxes = {word["id"]: word["bbox"] for line in recognised["lines"] for word in line["words"]}
    words = dict(words)
    for correction in text_corrections(corrections, recognised["index"]):
        ids = [word_id for word_id in correction["ids"] if word_id in words]
        if not ids:
            continue
        for word_id in ids:
            words[word_id] = []
        words[ids[0]] = written_words(ids, correction["text"], union(boxes[word_id] for word_id in ids))
    return words
