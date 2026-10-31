"""Transcription: what each recognised word says once the automatic steps have settled its problem spots.

Local OCR's words are the base. Steps that settle problem spots (AI reconstruction, then Web lookup)
store reconstruction records (format in `ocrmaker.reconstruction`); each record covers one or more marked word
ids and says which words replace them: the reconstructed text in locally measured word boxes, the
illegible marker `[NEČITELNÉ]` (an unresolved unreadable word), or the printed text kept as it is (a
publisher error, an unresolved doubtful reading). This module applies
them, in step order, so that every output takes the same text:

    words = page_words(ctx, recognised)   # {OCR word id: [words written in its place]}

`ctx` is anything with `has_result(step id)` and `result(step id)` (a StepContext). Each value is a list of
{"id", "text", "bbox"}: usually the one word itself, none for a box merged into its neighbour (the merged
word sits under the group's first id), several for a split box. A line of OCR word ids is expanded with
`expand(words, ids)`. Words no record covers are Local OCR's, unchanged (ADR 0001).

Layering: the steps in `RECORD_STEPS` are applied in order. A later step's record replaces every earlier
record it shares an id with; ids of a replaced record that the later one does not cover fall back to Local
OCR's word. Manual corrections (ADR 0004) are applied after this, at Export.
"""
from __future__ import annotations

from collections.abc import Iterable

from .words import page_file, page_words as ocr_words

# The illegible marker: the whole text of an unreadable word no automatic attempt resolved.
MARKER = "[NEČITELNÉ]"
# Steps whose result holds reconstruction records per page (`pages/page-NNNN.json`), applied in this order.
RECORD_STEPS = ("reconstruct", "lookup")


def records(ctx, index: int) -> list[dict]:
    """The reconstruction records in force on one page (0-based), after layering (see the module docstring)."""
    current: list[dict] = []
    for step_id in RECORD_STEPS:
        if not ctx.has_result(step_id):
            continue
        result = ctx.result(step_id)
        name = page_file(index)
        if not result.exists(name):
            continue
        for record in result.read_json(name).get("reconstructions", []):
            ids = set(record["ids"])
            current = [kept for kept in current if not ids & set(kept["ids"])] + [record]
    return current


def page_words(ctx, recognised: dict) -> dict[str, list[dict]]:
    """Every OCR word id of a page file with the words written in its place, in recognition order."""
    words = {word["id"]: [{key: word[key] for key in ("id", "text", "bbox")}] for word in ocr_words(recognised)}
    for record in records(ctx, recognised["index"]):
        ids = [word_id for word_id in record["ids"] if word_id in words]
        if not ids:
            continue
        for word_id in ids:
            words[word_id] = []
        words[ids[0]] = [{key: word[key] for key in ("id", "text", "bbox")} for word in record["words"]]
    return words


def expand(words: dict[str, list[dict]], ids: Iterable[str]) -> list[dict]:
    """The words written for a sequence of OCR word ids (a line of a block, say)."""
    return [word for word_id in ids for word in words.get(word_id, ())]
