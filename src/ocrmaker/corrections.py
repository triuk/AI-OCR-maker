"""Manual corrections: the user's changes from manual review, a layer of their own applied at Export (ADR 0004).

The project keeps them in `corrections.json` (see ocrmaker.store), a list in the order they were first made.
Making one never runs a step; rerunning a step before manual review discards them all, and their count is
what the rerun confirmation states (one correction, one count). Each correction is a dict with:

    {"id": "3f9c2a7b1d04",        # stable, unique in the project
     "kind": "text",              # what it changes: "text", "note" or "order" (below)
     "page": 0,                   # 0-based page index
     "created": iso time, "updated": iso time,
     ...kind-specific fields}

kind "text" sets what is written for a group of OCR words (Local OCR word ids, `ocrmaker.words`):

     "ids": ["p1-w5"],            # the OCR word ids it covers: one word, or the whole group of ids a
                                  # reconstruction record covers (a merge or split stays one correction)
     "text": "Rychvald",          # what is written in their place; spaces split it into several words,
                                  # "" writes nothing there
     "previous": "[NEČITELNÉ]",   # what the automatic steps wrote there when it was made (for the report)
     "source": "queue" | "word" | "suggestion"   # where the user made it: the review queue, a clicked word,
                                  # or an accepted propagation suggestion (the text of another resolved marker)

A correction of a group replaces any earlier correction it shares an id with.

kind "note" is a region note: a note the user attached to a region dragged on the page. It changes no text;
manual review shows it over the scan and the report lists it.

     "bbox": [x0, y0, x1, y1],    # the region, source page coordinates (ocrmaker.pages), inside the page
     "text": "Ověřit jméno."      # the note, never empty

kind "order" is a manual article order: the page's articles (ocrmaker.reading_order, as
`steps.export_step.reading_articles` gives them) in the order the user dragged them into. At most one per
page; both outputs write the page's articles in this order (`ordered`).

     "articles": ["p1-a2", "p1-a1"],    # article ids in the new order
     "previous": ["p1-a1", "p1-a2"]     # the automatic order when it was made (for the report)

Every correction counts as one, whatever its kind; `apply` ignores kinds it does not write.

Bookkeeping on the stored list (each changes it in place; the caller saves it under the run lock):
`put_text`, `add_note`, `edit_note`, `put_order` and `remove`. They give new corrections their id and
stamps and keep a replaced correction's id and creation time.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from .pages import union
from .store import now

TEXT, NOTE, ORDER = "text", "note", "order"
SOURCES = ("queue", "word", "suggestion")
# The manual Review step, and the name under which its result keeps the corrections it handed to Export.
REVIEW_STEP = "review"
CORRECTIONS_FILE = "corrections.json"


def of_kind(kind: str, corrections: Iterable[dict], page: int | None = None) -> list[dict]:
    return [item for item in corrections if item.get("kind") == kind and (page is None or item["page"] == page)]


def text_corrections(corrections: Iterable[dict], page: int | None = None) -> list[dict]:
    return of_kind(TEXT, corrections, page)


def notes(corrections: Iterable[dict], page: int | None = None) -> list[dict]:
    return of_kind(NOTE, corrections, page)


def ordered(articles: list[dict], corrections: Iterable[dict], page: int) -> list[dict]:
    """A page's articles in its manual article order, if it has one; articles it does not name keep their
    order after those it names."""
    orders = of_kind(ORDER, corrections, page)
    if not orders:
        return articles
    rank = {article_id: number for number, article_id in enumerate(orders[-1]["articles"])}
    return sorted(articles, key=lambda article: rank.get(article["id"], len(rank)))


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


# Bookkeeping on the stored list (see the module docstring). ---------------------------------------------

def _stamped(made: dict, earlier: dict | None = None) -> dict:
    """A correction made now: a new id and stamps, or those of the correction it replaces."""
    stamp = now()
    return {"id": earlier["id"] if earlier else uuid.uuid4().hex[:12], **made,
            "created": earlier["created"] if earlier else stamp, "updated": stamp}


def put_text(stored: list[dict], made: dict, source: str) -> dict | None:
    """Store a text correction (kind, page, ids, text, previous) made from `source`, replacing every text
    correction of the page it shares an id with (in the place of the first). Text equal to `previous` only
    removes them. Returns the stored correction or None."""
    overlapping = [item for item in stored if item.get("kind") == TEXT and item["page"] == made["page"]
                   and set(item["ids"]) & set(made["ids"])]
    correction = None
    if made["text"] != made["previous"]:
        correction = _stamped({**made, "source": source}, overlapping[0] if overlapping else None)
    kept = []
    for item in stored:
        if item in overlapping:
            if item is overlapping[0] and correction:
                kept.append(correction)
            continue
        kept.append(item)
    if correction and not overlapping:
        kept.append(correction)
    stored[:] = kept
    return correction


def add_note(stored: list[dict], made: dict) -> dict:
    """Store a new region note (kind, page, bbox, text)."""
    note = _stamped(made)
    stored.append(note)
    return note


def find(stored: list[dict], correction_id: str, kind: str | None = None) -> dict:
    """The stored correction with this id (and kind, when given); KeyError with a Czech message if none."""
    found = next((item for item in stored if item.get("id") == correction_id
                  and (kind is None or item.get("kind") == kind)), None)
    if found is None:
        raise KeyError("Poznámka neexistuje." if kind == NOTE else "Ruční oprava neexistuje.")
    return found


def edit_note(stored: list[dict], note_id: str, text: str) -> dict:
    """Change a stored region note's text (already checked); KeyError for an unknown note."""
    position = stored.index(find(stored, note_id, NOTE))
    stored[position] = {**stored[position], "text": text, "updated": now()}
    return stored[position]


def put_order(stored: list[dict], made: dict) -> dict | None:
    """Store a page's manual article order (kind, page, articles, previous) in place of the page's earlier
    one; the automatic order only removes it. Returns the stored correction or None."""
    position = next((n for n, item in enumerate(stored)
                     if item.get("kind") == ORDER and item["page"] == made["page"]), None)
    earlier = stored.pop(position) if position is not None else None
    if made["articles"] == made["previous"]:
        return None
    correction = _stamped(made, earlier)
    stored.insert(len(stored) if position is None else position, correction)
    return correction


def remove(stored: list[dict], correction_id: str) -> None:
    """Remove one stored correction; KeyError for an unknown one."""
    stored.remove(find(stored, correction_id))
