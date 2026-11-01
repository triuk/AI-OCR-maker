"""Read-through: the AI proofreads a page's recognised text and names words that do not fit their sentence.

Local signals miss a confident misreading that is a real word ("čárka" for the name "Šárka", "květnu" where
the sentence needs "května") or a lost space ("8do"). One text-only request per page carries the page's
text article by article in reading order, one numbered line per printed line; the answer names words by
line number and their text as it stands there, each with a short reason. A named word becomes a doubtful
reading with the signal `context` (ocrmaker.problems) and the reason as its `note`; AI reconstruction then
reads it in the print. The read-through only nominates: the print decides (ADR 0001).

A named word that is not on its line (or several words not standing together there) is ignored on its own
and recorded with why; an answer without a list of words is rejected as a whole.
"""
from __future__ import annotations

from .problems import DOUBTFUL, core

PURPOSE = "readthrough"
SIGNAL = "context"
# Shown in place of an empty word box: ink no reading covers, which is a problem spot already.
UNREAD = "[?]"

INSTRUCTIONS = """\
You proofread the local OCR text of one scanned printed page (a newsletter, magazine or book page, usually \
Czech) to find words the OCR probably misread. The OCR is confident about them, so they are not marked; only \
the sense of the text can give them away. Typical misreadings:
- a letter confused with a similar one, giving another real word that does not fit the sentence: \
"čárka" where the name "Šárka" is meant, "květnu" where the grammar needs "května", a wrong case ending, \
"tu" for "to";
- a lost or added space: "8do" for "8 do", "92let", "místo starosty" for "místostarosty";
- a line-end word whose hyphen was lost, so the word broken over two lines reads as two odd words;
- a digit or punctuation mark in a number, date or time that cannot be right (a day 35, a year 1099 in \
recent news, a comma inside a date).

The data gives the page's text grouped by article in reading order. Each line of text is one printed line, \
prefixed with its number ("12| ..."); a word broken at a line end keeps its hyphen there. "[?]" stands for \
ink the OCR could not read at all; it is handled elsewhere, ignore it. Headers, advertisements and tables \
may read as fragments; judge only the words within them.

Name each word that does not fit its sentence or looks like such a misreading: the line number, the word \
exactly as it stands on that line (several words together, separated by spaces, when the space between them \
is the problem), and a short reason saying what it probably should be. Do not name a word that fits, even a \
rare, regional, archaic or colloquial one, a name or an abbreviation, nor a word you would only write in a \
different style. Name nothing when the text reads right. Answer only with JSON matching the schema."""

SCHEMA = {
    "type": "object",
    "properties": {"words": {"type": "array", "items": {
        "type": "object",
        "properties": {"line": {"type": "integer"},
                       "word": {"type": "string"},
                       "reason": {"type": "string"}},
        "required": ["line", "word", "reason"],
        "additionalProperties": False}}},
    "required": ["words"],
    "additionalProperties": False,
}


def page_text(recognised: dict, layout: dict | None, articles: list[dict] | None) -> tuple[list[dict], list[list[dict]]]:
    """The request's articles, {"kind", "text"} with numbered lines in reading order, and the OCR words of
    each numbered line (line 1 is index 0).

    Without Reading order's articles the blocks go in Layout's numbering as one article; without Layout, the
    OCR lines do.
    """
    words = {word["id"]: word for line in recognised["lines"] for word in line["words"]}
    blocks = {block["id"]: block for block in (layout or {}).get("blocks", [])}
    if blocks:
        groups = [(article["kind"], [blocks[block]["lines"] for block in article["blocks"] if block in blocks])
                  for article in articles or []]
        placed = {block for article in articles or [] for block in article["blocks"]}
        rest = [block["lines"] for block in sorted(blocks.values(), key=lambda block: block["number"])
                if block["id"] not in placed]
        if rest:
            groups.append(("article", rest))
    else:
        groups = [("article", [[[word["id"] for word in line["words"]] for line in recognised["lines"]]])]
    shown, numbered = [], []
    for kind, paragraphs in groups:
        lines = []
        for paragraph in paragraphs:
            for line in paragraph:
                line_words = [words[word] for word in line if word in words]
                if not line_words:
                    continue
                numbered.append(line_words)
                lines.append(f"{len(numbered)}| " + " ".join(word["text"] or UNREAD for word in line_words))
        if lines:
            shown.append({"kind": kind, "text": "\n".join(lines)})
    return shown, numbered


def nominations(answer, numbered: list[list[dict]]) -> tuple[dict[str, str], list[dict]]:
    """The named words of an answer, word id -> reason, and the ignored items, each {"item", "problems"}
    (English). An answer without a list of words raises ValueError: it is rejected as a whole."""
    if not isinstance(answer, dict) or not isinstance(answer.get("words"), list):
        raise ValueError("the answer has no list of words")
    named, ignored = {}, []
    for item in answer["words"]:
        ids, problem = _find(item, numbered)
        if problem:
            ignored.append({"item": item, "problems": [problem]})
            continue
        for word_id in ids:
            named.setdefault(word_id, item["reason"].strip())
    return named, ignored


def add_spots(spots: list[dict], named: dict[str, str], recognised: dict) -> list[dict]:
    """A page's problem spots with the named words flagged by `context` and noted with the reason, in the
    page file's line and word order; a named word that is no spot yet becomes a doubtful reading."""
    found = {spot["id"]: spot for spot in spots}
    result = []
    for line in recognised["lines"]:
        for word in line["words"]:
            spot = found.get(word["id"])
            if word["id"] in named and word["text"]:
                if spot is None:
                    spot = {"id": word["id"], "kind": DOUBTFUL, "signals": [], "text": word["text"],
                            "confidence": word["confidence"], "bbox": word["bbox"], "line": line["id"]}
                spot["signals"].append(SIGNAL)
                spot["note"] = named[word["id"]]
            if spot is not None:
                result.append(spot)
    return result


def _find(item, numbered: list[list[dict]]) -> tuple[list[str], str | None]:
    """The ids of the words an item names on its line, or why it names none."""
    if not (isinstance(item, dict) and isinstance(item.get("line"), int) and not isinstance(item["line"], bool)
            and isinstance(item.get("word"), str) and isinstance(item.get("reason"), str)):
        return [], "the item breaks the schema"
    if not 1 <= item["line"] <= len(numbered):
        return [], f"there is no line {item['line']}"
    line = numbered[item["line"] - 1]
    wanted = item["word"].split()
    if not wanted:
        return [], "the item names no word"
    for match in (lambda text, named: text == named,
                  lambda text, named: core(text).casefold() == core(named).casefold() != ""):
        for start in range(len(line) - len(wanted) + 1):
            run = line[start:start + len(wanted)]
            if all(word["text"] and match(word["text"], named) for word, named in zip(run, wanted)):
                return [word["id"] for word in run], None
    return [], f"line {item['line']} has no {item['word']!r}"
