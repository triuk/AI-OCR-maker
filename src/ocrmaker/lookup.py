"""Web lookup: short targeted web searches settle the problem spots AI reconstruction left unresolved.

The spots are AI reconstruction's records with status "unresolved" (`ocrmaker.reconstruction`): a low
confidence reading, a spot the AI left unanswered, or one whose answer was rejected. For each one the step
builds at most two short queries (`queries`): the AI's candidate, and the local reading when it differs
(a partial reading of an unreadable spot only without a candidate), or the context alone; each with up to
CONTEXT_WORDS printed words on either side, never longer than MAX_QUERY_CHARS. Only these snippets and the
spot's crops are sent, in one request per page with `web_search=True`; whole pages or long passages never are.
The answer lists the spots the web settled, with the queries run, the printed words, a confidence (0-100), a
short answer and the source URLs (`check`). A spot is settled when the item is valid, its confidence reaches
the setting `reconstruction_accept_confidence` and it names at least one source URL.

Only settled spots get a record in `reconstructions`, in the reconstruction record format with
`"source": "web"`, `"status": "accepted"` and the extra fields `queries`, `answer` and `sources`;
`ocrmaker.transcription` lays it over AI reconstruction's record. An unsettled spot keeps what AI
reconstruction wrote: the illegible marker, or a doubtful reading kept as read. With the setting `web_lookup`
switched off the step searches nothing and every unresolved spot stays as it is.

The Web lookup result (`steps/lookup/`) holds `lookup.json`:

    {"enabled": true, "threshold": 80.0,
     "pages": [{"index": 0, "file": "pages/page-0001.json", "spots": 2, "searched": 2, "settled": 1,
                "markers": 1, "kept": 0, "rejected": null}],
     "total": {"spots": 2, "searched": 2, "settled": 1, "markers": 1, "kept": 0}}

(spots: unresolved spots handed over, searched: those sent to the web, settled: those the web settled,
markers: illegible markers left, kept: doubtful readings left as read) and one page file per page,
`pages/page-0001.json`:

    {"index": 0,
     "request": {"provider", "model", "usage"} | null,
     "rejected": null,                   # why the page's whole answer was rejected (English), else null
     "lookups": [{                       # every unresolved spot of the page, settled or not
        "ids": ["p1-w5"], "outcome": "settled",  # settled | insufficient | not_found | invalid | no_query |
                                                 # unavailable | rejected | disabled
        "queries": ["..."],              # the queries built (sent), or those the AI reports it ran
        "words": ["Rychvald"] | null, "confidence": 90.0 | null, "answer": "..." | null,
        "sources": ["https://..."], "reasoning": "..." | null, "problems": [...],    # problems: invalid items
        "left": [{"id", "text", "bbox", "hint"}]}],  # what AI reconstruction wrote, kept unless settled
     "reconstructions": [<reconstruction record, "source": "web", plus "queries", "answer", "sources">]}
"""
from __future__ import annotations

import re

from .problems import DOUBTFUL

LOOKUP_STEP = "lookup"
LOOKUP_FILE = "lookup.json"
PURPOSE = "lookup"
ENABLED_SETTING = "web_lookup"
SOURCE = "web"

# A query is at most this many characters, built from the spot and at most CONTEXT_WORDS words on each side.
MAX_QUERY_CHARS = 60
CONTEXT_WORDS = 3
MAX_QUERIES = 2
# The answer quoted from a source is kept up to this many characters.
MAX_ANSWER_CHARS = 300

SETTLED, INSUFFICIENT, NOT_FOUND, INVALID = "settled", "insufficient", "not_found", "invalid"
NO_QUERY, UNAVAILABLE, REJECTED, DISABLED = "no_query", "unavailable", "rejected", "disabled"

INSTRUCTIONS = f"""\
You settle problem spots in the OCR of a scanned printed page (a newsletter, magazine or book, usually Czech) \
with web searches. A problem spot is a word, or a few words, that neither the local OCR nor an AI reading of \
the image could determine with confidence: typically a name, a place, a title, a number or a fixed phrase. \
For each spot you get its ids, the partial local reading, the AI's candidate (possibly wrong or missing), a \
few words printed before and after it, ready-made search queries, and the numbers of image crops of the page \
in which the spot is outlined in red.

Rules:
- Search the web only with the given queries or shorter variants of them (drop words, add quotes). Never \
search with longer text, with other text of the page or with anything else. A query has at most \
{MAX_QUERY_CHARS} characters. Report every query you ran.
- Settle a spot only when a web source shows the word in a matching context (the same name, place, title, \
number or phrase) and it agrees with the print in the crop. Faithful transcription: give the words exactly \
as printed on this page, with punctuation attached as printed, never a spelling the print does not show.
- words: the printed words for the spot's ids. Usually one word per id; one word for several ids when they \
are parts of one printed word; several words for one id when its box holds several printed words.
- confidence: 0 to 100, how sure you are that the words are what is printed, given the web evidence and \
the crop.
- answer: what the source says, one short sentence or quote. sources: the URLs of the pages that show it.
- reasoning: one short sentence.
- Leave out a spot the web does not settle rather than guess. Answer only with JSON matching the schema."""

SCHEMA = {
    "type": "object",
    "properties": {"lookups": {"type": "array", "items": {
        "type": "object",
        "properties": {"ids": {"type": "array", "items": {"type": "string"}},
                       "words": {"type": "array", "items": {"type": "string"}},
                       "confidence": {"type": "number"},
                       "queries": {"type": "array", "items": {"type": "string"}},
                       "answer": {"type": "string"},
                       "sources": {"type": "array", "items": {"type": "string"}},
                       "reasoning": {"type": "string"}},
        "required": ["ids", "words", "confidence", "queries", "answer", "sources", "reasoning"],
        "additionalProperties": False}}},
    "required": ["lookups"],
    "additionalProperties": False,
}

_TOKEN = re.compile(r"\[\[([^:\]]+): ([^\]]*)\]\]|\S+")
_EDGE = "\"'„“”‚‘’»«()[]{}<>.,;:!?…–—"
_URL = re.compile(r"https?://\S+$")


def _clean(text: str) -> str:
    return text.strip().strip(_EDGE).strip()


def _tokens(text: str) -> list[tuple[str | None, str]]:
    """The words of a marked paragraph text as (spot id or None, text); line-end hyphenation is joined."""
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text or "")
    return [(match.group(1), match.group(2)) if match.group(1) else (None, match.group(0))
            for match in _TOKEN.finditer(text)]


def _context(tokens: list[tuple[str | None, str]], ids: set[str]) -> tuple[list[str], list[str]]:
    """Up to CONTEXT_WORDS clean words printed before and after the spot; another spot ends the context."""
    places = [number for number, (spot, _) in enumerate(tokens) if spot in ids]
    if not places:
        return [], []

    def walk(numbers) -> list[str]:
        found = []
        for number in numbers:
            spot, text = tokens[number]
            if spot is not None or len(found) == CONTEXT_WORDS:
                break
            word = _clean(text)
            if word:
                found.append(word)
        return found

    before = walk(range(places[0] - 1, -1, -1))[::-1]
    after = walk(range(places[-1] + 1, len(tokens)))
    return before, after


def _fit(before: list[str], term: str, after: list[str]) -> str:
    """The query: the term with its context, outer context words dropped until it fits MAX_QUERY_CHARS."""
    before, after = list(before), list(after)
    while True:
        query = " ".join(part for part in (*before, term, *after) if part)
        if len(query) <= MAX_QUERY_CHARS or not (before or after):
            return query[:MAX_QUERY_CHARS].strip()
        if len(before) >= len(after):
            before.pop(0)
        else:
            after.pop()


def queries(record: dict) -> dict:
    """The short snippets sent for one unresolved reconstruction record: reading, candidate, context, queries."""
    ids = record["ids"]
    reading = " ".join(spot["reading"] for spot in record["input"]["spots"] if spot["reading"])
    candidate = " ".join(record["candidate"]) if record.get("candidate") else None
    before, after = _context(_tokens(record["input"].get("text")), set(ids))
    # The partial reading of an unreadable spot is a fragment: it is searched only when there is no candidate.
    whole = all(spot["kind"] == DOUBTFUL for spot in record["input"]["spots"])
    terms = [_clean(term) for term in (candidate, reading if whole or not candidate else None) if term]
    built = []
    for term in terms:
        if any(char.isalnum() for char in term):
            query = _fit(before, term, after)
            if query not in built:
                built.append(query)
    if not built and len(before) + len(after) >= 2:
        built.append(_fit(before, "", after))
    return {"reading": reading, "candidate": candidate, "before": " ".join(before), "after": " ".join(after),
            "queries": built[:MAX_QUERIES]}


def check(item, listed: dict[tuple[str, ...], dict], seen: set[tuple[str, ...]]) -> tuple[tuple | None, list[str]]:
    """The spot an answer item settles (its ids as a tuple) and the item's problems (English, empty if valid)."""
    if not isinstance(item, dict):
        return None, ["the item is not an object"]
    ids = item.get("ids")
    key = tuple(ids) if isinstance(ids, list) and all(isinstance(word_id, str) for word_id in ids) else None
    if key not in listed:
        return None, [f"the item names ids that are not a listed spot: {ids!r}"]
    problems = []
    if key in seen:
        problems.append("the spot is answered twice")
    words = item.get("words")
    if not isinstance(words, list) or not words or not all(
            isinstance(word, str) and word and not any(char.isspace() for char in word) for word in words):
        problems.append("no valid words (each one printed word without spaces)")
    confidence = item.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
        problems.append(f"invalid confidence {confidence!r}")
    ran = item.get("queries")
    if not isinstance(ran, list) or not ran or not all(isinstance(query, str) and query.strip() for query in ran):
        problems.append("no queries reported")
    else:
        long = [query for query in ran if len(query) > MAX_QUERY_CHARS]
        if long:
            problems.append(f"queries longer than {MAX_QUERY_CHARS} characters: {long!r}")
    sources = item.get("sources")
    if not isinstance(sources, list) or not all(isinstance(url, str) and _URL.match(url) for url in sources):
        problems.append("sources are not a list of http(s) URLs")
    if not isinstance(item.get("answer"), str) or not isinstance(item.get("reasoning"), str):
        problems.append("no answer or reasoning")
    return key, problems
