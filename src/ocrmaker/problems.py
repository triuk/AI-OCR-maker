"""Problem detector: finds the problem spots of a recognised page with local signals only.

A problem spot is a word whose local reading is missing, partial or untrustworthy (see GLOSSARY.md). Each
word of a page file (ocrmaker.words) is checked for these signals:

    empty                   an empty word box: ink no reading covers (ocrmaker.inkgaps)
    partial_text            far fewer characters than the word box is wide: only part of the word was read
    adjoins_empty           touches an empty word box, so it is a read fragment of a larger damaged word
    low_confidence          Tesseract's confidence is below a threshold (see THRESHOLDS)
    not_in_dictionary       missing from both the hunspell dictionary of the language and the project dictionary
    digits_in_word          digits mixed into a word, e.g. "Rychva1d" (a number with a unit, "5kg", is fine)
    implausible_diacritics  a letter the language does not have, or Czech "ě" or "ů" where Czech never puts them
    mixed_case              an upper-case letter right after a lower-case one, e.g. "rYchvald"
    odd_symbols             a character that is neither a letter, a digit nor usual punctuation, e.g. "|omněl"
    joins_next_line         a line's last word, read without a hyphen, that forms a dictionary word with the next
                            line's first word: the line-end hyphen was lost (cut off by the scan, say)

and classified as one kind:

    unreadable  (unreadable word)    empty, partial_text, adjoins_empty, or confidence below the
                                     unreadable threshold: there is little or no text to trust
    doubtful    (doubtful reading)   any other signal: clearly read but implausible

A line-end hyphenated word is checked against the dictionary joined with its continuation on the next line.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from .inkgaps import WORD_GAP_SHARE

UNREADABLE = "unreadable"
DOUBTFUL = "doubtful"
KINDS = (UNREADABLE, DOUBTFUL)
UNREADABLE_SIGNALS = ("empty", "partial_text", "adjoins_empty")

# Project settings holding the thresholds (Tesseract confidence, 0-100) and their defaults.
THRESHOLDS = {"problem_unreadable_confidence": 40.0, "problem_doubtful_confidence": 70.0}
# A reading with fewer characters than this share of what its box width holds is partial.
PARTIAL_SHARE = 0.5

# Letters with diacritics each document language has; any other accented letter is implausible.
ALPHABETS = {"ces": "áčďéěíňóřšťúůýž", "slk": "áäčďéíĺľňóôŕšťúýž", "pol": "ąćęłńóśźż", "deu": "äöüß",
             "eng": ""}
# Punctuation stripped from the ends of a word before it is checked.
EDGE_PUNCTUATION = "\"'„“”‚‘’»«›‹()[]{}<>.,;:!?…–—-‐*/"
# Punctuation that may stand inside a word: compounds, abbreviations, dates, times, numbers.
INNER_PUNCTUATION = set("-‐'’./,:&+%°")
HYPHENS = ("-", "‐", "¬")
# A word ending in one of these closes a clause, so it cannot run on into the next line.
CLOSING_PUNCTUATION = set(".,;:!?…)]}»«“”\"")
_UNIT = re.compile(r"^\d+[^\W\d_]{1,3}$")  # 5kg, 2x, 10km, 1990s
_CZECH_E_CARON = set("bpvfmdtn")


def core(text: str) -> str:
    """A word without the punctuation at its ends."""
    return text.strip(EDGE_PUNCTUATION)


def _letters(text: str) -> list[str]:
    return [char for char in text if char.isalpha()]


def character_signals(text: str, language: str) -> list[str]:
    """The odd character mixes of a word's text."""
    word = core(text)
    signals = []
    letters = _letters(word)
    if letters and any(char.isdigit() for char in word):
        code_like = all(char.isupper() for char in letters)  # A4, MP3, H2O
        if not code_like and not _UNIT.match(word):
            signals.append("digits_in_word")
    alphabet = ALPHABETS.get(language)
    if alphabet is not None and letters:
        lower = [char.lower() for char in letters]
        foreign = any(not char.isascii() and char not in alphabet for char in lower)
        czech = language == "ces" and (lower[0] == "ů" or any(
            char == "ě" and (index == 0 or lower[index - 1] not in _CZECH_E_CARON) for index, char in enumerate(lower)))
        if foreign or czech:
            signals.append("implausible_diacritics")
    if len(letters) > 4 and any(a.islower() and b.isupper() for a, b in zip(word, word[1:])):
        signals.append("mixed_case")  # short ones are abbreviations such as MěÚ or ObÚ
    if any(not (char.isalnum() or char in INNER_PUNCTUATION) for char in word) or (
            not word and any(not (unicodedata.category(char).startswith("P") or char in EDGE_PUNCTUATION)
                             for char in text)):
        signals.append("odd_symbols")
    return signals


def _abbreviation(word: str) -> bool:
    """Short capitals (DPS, RPIC) and dotted short parts (a.s, s.r.o) are abbreviations, not dictionary words."""
    letters = _letters(word)
    if len(letters) <= 5 and letters[0].isupper() and letters[-1].isupper():
        return True
    parts = word.split(".")
    return len(parts) > 1 and all(len(part) <= 3 for part in parts)


def _known(word: str, known: Callable[[str], bool], dotted: bool = False) -> bool:
    """Whether a word (without edge punctuation) is in the dictionaries; compounds are checked part by part.

    dotted: the printed word ends with a full stop, so it may be an abbreviation the dictionary lists with it.
    """
    if not _letters(word) or any(char.isdigit() for char in word):
        return True  # numbers, dates, units and codes are not dictionary words; digits_in_word judges them
    if _abbreviation(word) or (dotted and len(_letters(word)) <= 2) or known(word) or (dotted and known(word + ".")):
        return True
    parts = [part for part in re.split(r"[-‐/]", word) if part]
    return len(parts) > 1 and all(not _letters(part) or known(part) for part in parts)


def _median(values: list[float]) -> float | None:
    values = sorted(values)
    return values[len(values) // 2] if values else None


def detect(page: dict, *, language: str, unreadable_below: float, doubtful_below: float,
           known: Callable[[str], bool] | None) -> list[dict]:
    """The problem spots of one page file, in its line and word order.

    known tells whether a word is in the dictionaries; None skips the dictionary check.
    Each spot is {id, kind, signals, text, confidence, bbox, line}.
    """
    lines = page["lines"]
    # Character width relative to the line height, so that it carries over to headings in larger print.
    page_ratio = _median([(word["bbox"][2] - word["bbox"][0]) / len(word["text"]) / _height(line)
                          for line in lines for word in line["words"]
                          if word["text"] and word["confidence"] >= doubtful_below])
    dictionary = _dictionary_verdicts(lines, known, doubtful_below) if known else {}
    run_on = _lost_hyphens(lines, known, doubtful_below) if known else set()
    spots = []
    for line in lines:
        words = line["words"]
        confident = [(word["bbox"][2] - word["bbox"][0]) / len(word["text"])
                     for word in words if word["text"] and word["confidence"] >= doubtful_below]
        height = _height(line)
        char_width = _median(confident) if len(confident) >= 3 else page_ratio and page_ratio * height
        spacing = _median([b["bbox"][0] - a["bbox"][2] for a, b in zip(words, words[1:])
                           if a["text"] and b["text"] and b["bbox"][0] > a["bbox"][2]])
        touching = WORD_GAP_SHARE * spacing if spacing else 0.2 * height
        for index, word in enumerate(words):
            signals = []
            text = word["text"]
            if not text:
                signals.append("empty")
            else:
                width = word["bbox"][2] - word["bbox"][0]
                if char_width and width / char_width >= 3 and len(text) < PARTIAL_SHARE * width / char_width:
                    signals.append("partial_text")
                neighbours = words[max(0, index - 1):index] + words[index + 1:index + 2]
                if any(not other["text"] and _gap(word, other) < touching for other in neighbours):
                    signals.append("adjoins_empty")
                if word["confidence"] < doubtful_below:
                    signals.append("low_confidence")
                if dictionary.get(word["id"]) is False:
                    signals.append("not_in_dictionary")
                signals += character_signals(text, language)
                if word["id"] in run_on:
                    signals.append("joins_next_line")
            if not signals:
                continue
            unreadable = any(signal in UNREADABLE_SIGNALS for signal in signals) or (
                bool(text) and word["confidence"] < unreadable_below)
            spots.append({"id": word["id"], "kind": UNREADABLE if unreadable else DOUBTFUL, "signals": signals,
                          "text": text, "confidence": word["confidence"], "bbox": word["bbox"], "line": line["id"]})
    return spots


def _height(line: dict) -> float:
    return max(line["bbox"][3] - line["bbox"][1], 1e-6)


def _gap(a: dict, b: dict) -> float:
    return max(b["bbox"][0] - a["bbox"][2], a["bbox"][0] - b["bbox"][2])


def _dictionary_verdicts(lines: list[dict], known: Callable[[str], bool], trusted: float) -> dict[str, bool]:
    """Per word id, whether the dictionaries know the word; a line-end hyphenation is joined first.

    When the continuation is unreadable or untrusted (confidence below `trusted`), the hyphenated head is
    left unjudged: the continuation is a problem spot itself, and the head goes to AI in its context anyway.
    """
    verdicts: dict[str, bool] = {}
    for number, line in enumerate(lines):
        for word in line["words"]:
            if word["text"] and word["id"] not in verdicts:
                text = word["text"]
                verdicts[word["id"]] = _known(core(text), known, dotted=text[len(text.rstrip(EDGE_PUNCTUATION)):].startswith("."))
        last = next((word for word in reversed(line["words"]) if word["text"]), None)
        if not last or not last["text"].endswith(HYPHENS):
            continue
        following = _continuation(line, lines)
        first = following["words"][0] if following and following["words"] else None
        if first is None or not first["text"] or first["confidence"] < trusted:
            # The rest of the word is in another column or on the next page: a fragment cannot be judged.
            verdicts[last["id"]] = True
            continue
        head, tail = core(last["text"]), core(first["text"])
        text = first["text"]
        dotted = text[len(text.rstrip(EDGE_PUNCTUATION)):].startswith(".")
        joined = _known(head + tail, known, dotted) or _known(f"{head}-{tail}", known, dotted)
        verdicts[last["id"]] = verdicts[first["id"]] = joined
    return verdicts


def _lost_hyphens(lines: list[dict], known: Callable[[str], bool], trusted: float) -> set[str]:
    """Ids of line-end words read without a hyphen that form a dictionary word with the next line's first word.

    Only a trusted continuation in lower case counts, and only when one of the two is no word on its own:
    "do" + "opravených" are two words even though "doopravených" is one too.
    """
    lost = set()
    for line in lines:
        last = next((word for word in reversed(line["words"]) if word["text"]), None)
        if not last or last["text"].endswith(HYPHENS) or not core(last["text"]).isalpha():
            continue  # numbers and codes do not break into the next line
        if CLOSING_PUNCTUATION & set(last["text"][len(last["text"].rstrip(EDGE_PUNCTUATION)):]):
            continue
        following = _continuation(line, lines)
        first = following["words"][0] if following and following["words"] else None
        if first is None or not first["text"] or first["confidence"] < trusted or not first["text"][0].islower():
            continue
        text = first["text"]
        dotted = text[len(text.rstrip(EDGE_PUNCTUATION)):].startswith(".")
        head, tail = core(last["text"]), core(text)
        if _known(head + tail, known, dotted) and not (_known(head, known) and _known(tail, known, dotted)):
            lost.add(last["id"])
    return lost


def _continuation(line: dict, lines: list[dict]) -> dict | None:
    """The line right below in the same column: where a line-end hyphenated word goes on."""
    x0, y0, x1, y1 = line["bbox"]
    height = y1 - y0
    below = [other for other in lines if other is not line and y0 + height / 2 < other["bbox"][1] < y1 + 1.5 * height
             and min(x1, other["bbox"][2]) - max(x0, other["bbox"][0]) > 0.5 * min(x1 - x0, other["bbox"][2] - other["bbox"][0])]
    return min(below, key=lambda other: other["bbox"][1], default=None)
