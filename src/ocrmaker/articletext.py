"""Article text: the Markdown that Export writes next to the OCR PDF, for machine processing.

The Markdown is built from the pages' articles in reading order (ocrmaker.reading_order), their blocks
(ocrmaker.layout) and the words' exported text (`ExportStep.exported_words`). Its format:

    ---
    source: "zpravodaj-2000-05.pdf"          # the source PDF's file name, always
    title: "Rychvaldský zpravodaj"           # only when detected: running header text repeated on pages
    issue: "č. 5/2000"                       # only when detected: "číslo 5", "č. 5/2000", "5/2000"
    date: "květen 2000"                      # only when detected: a month with a year, or d. m. yyyy
    pages: 2
    ---

    <!-- page pdf=1 printed=5 header="Rychvaldský zpravodaj květen 2000" footer="strana 5" -->

    <!-- article id=p1-a2 -->

    ## Heading of the article

    One paragraph per line, its printed lines joined, line-end hyphenation resolved.

    <!-- caption -->
    Obr. 1: caption text

    <!-- table -->
    Družstvo | Body
    Sokol | 21

    <!-- ad id=p1-a4 -->

    Text of the advertisement.

Rules:
- Every text is written verbatim (no Markdown escaping), as faithful transcription requires (ADR 0001).
- A page comment `<!-- page pdf=N printed=M header="…" footer="…" -->` says that the text after it is
  printed on PDF page N (1-based). `printed` is the printed page number found in the running header or
  footer, `header`/`footer` their text; each is left out when there is none. Attribute values are HTML
  escaped (`&quot;`, `&amp;`, `&lt;`, `&gt;`, and `&#45;` for a dash that would form `--`).
- A page comment opens every page, also an empty one. An article continued on the next page is written as
  one article; its continuation follows directly, after the next page's comment. Text after it that is
  printed on the earlier page gets that page's comment again, so every text stays attributable to its page.
- When a paragraph runs across the page boundary, the page comment is a line of its own inside the
  paragraph (no blank lines around it): dropping comment lines gives back the continuous paragraph.
- Running headers and footers are not repeated as text; they live in the page comments.
- Articles start with `<!-- article id=… -->`, ads with `<!-- ad id=… -->`. Headings are `##` (the first
  heading of an article) or `###`. Captions and tables are preceded by `<!-- caption -->` / `<!-- table -->`;
  a table writes one row per line with cells (found from wide word gaps) separated by " | ".
- Paragraphs are layout blocks. Two body blocks in a row of one article (across a column or page) form one
  paragraph when the first ends with a hyphen or without closing punctuation and the next starts in lower case.
- Line-end hyphenation is removed and genuine hyphens are kept (`join_hyphenated`); the text layer of the
  OCR PDF keeps the hyphenation as printed.
"""
from __future__ import annotations

import html
import json
import re
import statistics
import string
from collections import Counter
from collections.abc import Callable

# Characters that end a line as a hyphen: hyphen-minus, hyphen, soft hyphen, the not sign used for it.
HYPHENS = "-‐­¬"
# Discretionary hyphens: always a line-end hyphenation, never a genuine hyphen.
DISCRETIONARY = "­¬"
PUNCTUATION = string.punctuation + "„“”‚‘’«»…–—"
CLOSING = ".!?:;…"
TEXT_KINDS = ("article", "ad")
PARAGRAPH_KINDS = ("body", "header", "footer")  # blocks that read as running paragraphs

MONTHS = ("leden|ledna|únor|února|březen|března|duben|dubna|květen|května|červenec|července|červen|června|"
          "srpen|srpna|září|říjen|října|listopad|listopadu|prosinec|prosince")
DATE = re.compile(rf"(?<!\w)(?:\d{{1,2}}\.\s*)?(?:{MONTHS})(?:\s*[–-]\s*(?:{MONTHS}))?\s+\d{{4}}(?!\w)"
                  r"|(?<!\w)\d{1,2}\.\s*\d{1,2}\.\s*\d{4}(?!\w)", re.IGNORECASE)
ISSUE = re.compile(r"(?<!\w)(?:číslo|čís\.|č\.)\s*\d{1,3}(?:\s*/\s*\d{2,4})?(?!\w)|(?<![\w/])\d{1,2}\s*/\s*\d{4}(?!\w)",
                   re.IGNORECASE)
PAGE_WORDS = re.compile(r"(?<!\w)(?:strana|stránka|str\.|s\.)(?!\w)", re.IGNORECASE)
PAGE_NUMBER = re.compile(r"^\d{1,4}$")
TABLE_GAP = 1.0  # a gap wider than this many row heights separates two table cells

Dictionary = Callable[[str], bool]


# Hyphenation -----------------------------------------------------------------------------------------

def _core(text: str) -> str:
    return text.strip(PUNCTUATION)


def ends_with_hyphen(word: str) -> bool:
    """A word that ends a line with a hyphen after a letter or digit (not a lone dash)."""
    return len(word) >= 2 and word[-1] in HYPHENS and word[-2].isalnum()


def join_hyphenated(head: str, tail: str, *, seen: set[str] = frozenset(),
                    known: Dictionary | None = None) -> tuple[str, bool]:
    """The word printed as `head` (ending with a hyphen at a line end) and `tail` (starting the next line).

    Returns the joined text and whether the hyphen was kept as genuine. In order:
    - the next line repeats the hyphen (Czech typography for a genuine hyphen): kept once;
    - a soft hyphen or not sign: removed;
    - a digit on either side ("COVID-19") or an upper-case continuation ("Frýdek-Místek"): kept;
    - the document's own words (`seen`, lower case, read inside lines) name the hyphenated or the joined form;
    - the dictionary (`known`) knows the joined word: removed; it knows both parts but not the joined word: kept;
    - otherwise removed, the far more common case at a line end.
    """
    stem, hyphen = head[:-1], head[-1]
    if tail[:1] in HYPHENS and len(tail) > 1:
        return stem + hyphen + tail[1:], True
    if hyphen in DISCRETIONARY:
        return stem + tail, False
    first, second = _core(stem), _core(tail)
    if not first or not second:
        return stem + tail, False
    if first[-1].isdigit() or second[0].isdigit() or second[0].isupper():
        return stem + "-" + tail, True
    joined, hyphenated = (first + second).lower(), f"{first}-{second}".lower()
    if hyphenated in seen:
        return stem + "-" + tail, True
    if joined in seen:
        return stem + tail, False
    if known is not None:
        if known(first + second):
            return stem + tail, False
        if known(first) and known(second):
            return stem + "-" + tail, True
    return stem + tail, False


class _Hyphens:
    """Resolves line-end hyphenation and counts the decisions."""

    def __init__(self, seen: set[str], known: Dictionary | None):
        self.seen, self.known = seen, known
        self.joined = self.kept = 0

    def run(self, items: list) -> list:
        """items: words as (text, line_end) and page markers (ints); returns texts and markers."""
        out: list = []
        index = 0
        while index < len(items):
            item = items[index]
            if isinstance(item, int):
                out.append(item)
                index += 1
                continue
            text, line_end = item
            index += 1
            moved = []
            while line_end and ends_with_hyphen(text):
                ahead = index
                while ahead < len(items) and isinstance(items[ahead], int):
                    ahead += 1
                if ahead >= len(items):
                    break
                moved += items[index:ahead]
                text, kept = join_hyphenated(text, items[ahead][0], seen=self.seen, known=self.known)
                self.kept += kept
                self.joined += not kept
                line_end = items[ahead][1]
                index = ahead + 1
            out.append(text)
            out += moved  # a page marker inside a hyphenated word follows the whole word
        return out


# Page numbers and front matter --------------------------------------------------------------------------

def _printed_numbers(running: list[list[str]], numbers: list[int]) -> list[int | None]:
    """The printed page number of every page from its running header and footer words, when one is found.

    numbers are the pages' PDF page numbers (1-based). A number after "strana"/"str."/"s." wins; among
    several numbers the one at the document's most common offset from the PDF page number. Four-digit years
    are never page numbers.
    """
    candidates = []
    for words in running:
        found, preferred = [], []
        for position, word in enumerate(words):
            core = word.strip(PUNCTUATION)
            if not PAGE_NUMBER.match(core) or (len(core) == 4 and 1800 <= int(core) <= 2100):
                continue
            found.append(int(core))
            if position and PAGE_WORDS.fullmatch(words[position - 1]):
                preferred.append(int(core))
        candidates.append(preferred or found)
    offsets = Counter(values[0] - number for number, values in zip(numbers, candidates) if len(values) == 1)
    return [max(reversed(values), key=lambda value: offsets[value - number]) if values else None
            for number, values in zip(numbers, candidates)]


def _most_common(values: list[str]) -> str | None:
    return Counter(values).most_common(1)[0][0] if values else None


def front_matter(source_name: str, running: list[list[str]], first_headings: list[str], pages: int) -> dict:
    """source, and title, issue and date when detected, from the running headers and footers.

    running holds each running header or footer text (one per header/footer article and page);
    first_headings the first page's heading texts, searched for an issue or date only when the running
    texts have none. The title is running text repeated on at least two pages (or on the only page),
    after removing the date, issue and page number.
    """
    texts = [" ".join(words) for words in running]
    dates = [" ".join(match.split()) for text in texts for match in DATE.findall(text)]
    issues = [" ".join(match.split()) for text in texts for match in ISSUE.findall(text)]
    if not dates:
        dates = [" ".join(match.split()) for text in first_headings for match in DATE.findall(text)]
    if not issues:
        issues = [" ".join(match.split()) for text in first_headings for match in ISSUE.findall(text)]
    residues = []
    for text in texts:
        rest = PAGE_WORDS.sub(" ", ISSUE.sub(" ", DATE.sub(" ", text)))
        words = [word for word in rest.split() if not word.strip(PUNCTUATION).isdigit()]
        rest = " ".join(words).strip(PUNCTUATION + " ")
        if any(len(word) >= 3 and word.isalpha() for word in rest.split()):
            residues.append(rest)
    title = None
    if residues:
        title, count = Counter(residues).most_common(1)[0]
        if count < 2 and pages > 1:
            title = None
    matter = {"source": source_name}
    for key, value in (("title", title), ("issue", _most_common(issues)), ("date", _most_common(dates))):
        if value:
            matter[key] = value
    matter["pages"] = pages
    return matter


# Rendering -----------------------------------------------------------------------------------------------

def _attribute(value: str) -> str:
    """A comment attribute value: HTML escaped, and no "--" inside a comment."""
    return re.sub(r"-(?=-)", "&#45;", html.escape(value, quote=True))


def _block_words(block: dict) -> list[str]:
    return [word["text"] for line in block["lines"] for word in line]


def _table_rows(block: dict) -> list[str]:
    rows = []
    for line in block["lines"]:
        if not line:
            continue
        height = max(word["bbox"][3] - word["bbox"][1] for word in line)
        cells = [[line[0]["text"]]]
        for left, right in zip(line, line[1:]):
            if right["bbox"][0] - left["bbox"][2] > TABLE_GAP * height:
                cells.append([])
            cells[-1].append(right["text"])
        rows.append(" | ".join(" ".join(cell) for cell in cells))
    return rows


def _continues(upper: dict, lower: dict) -> bool:
    """Whether the body block `lower` continues the paragraph of `upper` (across a column or a page)."""
    above, below = _block_words(upper), _block_words(lower)
    if not above or not below:
        return False
    last, first = above[-1], below[0].lstrip(PUNCTUATION)
    if ends_with_hyphen(last):
        return True
    return last.rstrip("\"'“”»)]")[-1:] not in CLOSING and first[:1].islower()


def render(source_name: str, pages: list[dict], links: list[dict], known: Dictionary | None = None) -> tuple[str, dict]:
    """The article text Markdown and its statistics.

    pages: in PDF order, each {"index", "articles": [{"id", "kind", "continues", "blocks": [{"id", "kind",
    "lines": [[{"text", "bbox"}]]}]}]}, articles in reading order (kinds as in ocrmaker.reading_order,
    block kinds as in ocrmaker.layout). links: [{"from", "to"}] articles continued on the next page.
    known: the dictionary that decides unclear line-end hyphens, or None.
    """
    # An empty word box (text unknown, ocrmaker.inkgaps) has no text to write until it is reconstructed.
    pages = [{**page, "articles": [{**article, "blocks": [
        {**block, "lines": [[word for word in line if word["text"]] for line in block["lines"]]}
        for block in article["blocks"]]} for article in page["articles"]]} for page in pages]
    running, headers, footers = [], [], []
    for page in pages:
        texts = {"header": [], "footer": []}
        for article in page["articles"]:
            if article["kind"] in texts:
                words = [word for block in article["blocks"] for word in _block_words(block)]
                if words:
                    texts[article["kind"]].append(words)
        headers.append(" | ".join(" ".join(words) for words in texts["header"]))
        footers.append(" | ".join(" ".join(words) for words in texts["footer"]))
        running.append(texts["header"] + texts["footer"])
    numbers = [page["index"] + 1 for page in pages]
    printed = _printed_numbers([[word for words in item for word in words] for item in running], numbers)
    first_headings = [" ".join(_block_words(block)) for article in (pages[0]["articles"] if pages else [])
                      for block in article["blocks"] if block["kind"] == "heading"]
    matter = front_matter(source_name, [words for item in running for words in item], first_headings, len(pages))

    # The document's own words read inside lines decide hyphens they show (see join_hyphenated).
    seen = set()
    for page in pages:
        for article in page["articles"]:
            for block in article["blocks"]:
                for line in block["lines"]:
                    seen.update(_core(word["text"]).lower() for word in line[:-1])
    hyphens = _Hyphens(seen, known)

    def comment(position: int) -> str:
        attributes = [f"pdf={numbers[position]}"]
        if printed[position] is not None:
            attributes.append(f"printed={printed[position]}")
        for name, value in (("header", headers[position]), ("footer", footers[position])):
            if value:
                attributes.append(f'{name}="{_attribute(value)}"')
        return f"<!-- page {' '.join(attributes)} -->"

    where = {article["id"]: (position, article) for position, page in enumerate(pages)
             for article in page["articles"]}
    following = {link["from"]: link["to"] for link in links if link["to"] in where}
    chunks: list[str] = []
    state = {"page": None}
    marked: set[int] = set()
    done: set[str] = set()
    counts = Counter()

    def mark(position: int) -> None:
        if state["page"] != position:
            chunks.append(comment(position))
            state["page"] = position
            marked.add(position)

    def words_of(run: list[tuple[int, dict]]) -> list:
        """A run's words as (text, ends a line) with the page index wherever the run turns to a new page."""
        items, page = [], run[0][0]
        for position, block in run:
            if position != page:
                items.append(position)
                page = position
            for line in block["lines"]:
                items += [(word["text"], number == len(line) - 1) for number, word in enumerate(line)]
        return items

    def text_of(pieces: list) -> list[str]:
        lines, current = [], []
        for piece in pieces:
            if isinstance(piece, int):
                if current:
                    lines.append(" ".join(current))
                    current = []
                lines.append(comment(piece))
                state["page"] = piece
                marked.add(piece)
            else:
                current.append(piece)
        if current:
            lines.append(" ".join(current))
        return lines

    def write_article(parts: list[tuple[int, dict]]) -> None:
        article = parts[0][1]
        blocks = [(position, block) for position, part in parts for block in part["blocks"]]
        if not blocks:
            return
        mark(blocks[0][0])
        tag = "ad" if article["kind"] == "ad" else "article"
        chunks.append(f"<!-- {tag} id={article['id']} -->")
        counts[tag] += 1
        headings = 0
        index = 0
        while index < len(blocks):
            position, block = blocks[index]
            mark(position)
            kind = block["kind"]
            if kind == "table":
                chunks.append("\n".join(["<!-- table -->"] + _table_rows(block)))
                index += 1
                continue
            run = [(position, block)]
            if kind in PARAGRAPH_KINDS:
                while index + len(run) < len(blocks) and blocks[index + len(run)][1]["kind"] in PARAGRAPH_KINDS \
                        and _continues(run[-1][1], blocks[index + len(run)][1]):
                    run.append(blocks[index + len(run)])
            index += len(run)
            lines = text_of(hyphens.run(words_of(run)))
            if not lines:
                continue
            if kind == "heading":
                headings += 1
                chunks.append(("## " if headings == 1 else "### ") + " ".join(lines))
            elif kind == "caption":
                chunks.append("\n".join(["<!-- caption -->"] + lines))
                counts["caption"] += 1
            else:
                chunks.append("\n".join(lines))

    for position, page in enumerate(pages):
        if position not in marked:
            mark(position)
        for article in page["articles"]:
            if article["kind"] not in TEXT_KINDS or article["id"] in done:
                continue
            parts = [(position, article)]
            done.add(article["id"])
            while parts[-1][1]["id"] in following and following[parts[-1][1]["id"]] not in done:
                parts.append(where[following[parts[-1][1]["id"]]])
                done.add(parts[-1][1]["id"])
            write_article(parts)

    text = "---\n" + "".join(f"{key}: {json.dumps(value, ensure_ascii=False)}\n" for key, value in matter.items()) \
        + "---\n\n" + "\n\n".join(chunks) + "\n"
    stats = {"articles": counts["article"], "ads": counts["ad"], "captions": counts["caption"],
             "printed_pages": printed, "hyphens": {"joined": hyphens.joined, "kept": hyphens.kept},
             "front_matter": matter}
    return text, stats

