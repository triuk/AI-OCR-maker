"""Reading order: a page's articles in reading order, taken from its layout regions (ADR 0006), and the AI's links
of articles continued across pages; the rules are applied and checked here.

The articles come from the regions (`region_articles`): the regions of one article in their order, each with its
blocks in Layout's order, the article's kind the regions' kind. One request per page without an image (purpose
CONTINUATION_PURPOSE) carries only the text: the page's articles of kind article with their first words and the
previous page's with their last words. The answer lists the links {"article", "continues"}; it never regroups or
reorders the articles (`validate_links`, `link`), and an invalid answer leaves the page unlinked ("reason" says
why). Tables are read row by row by their block (ocrmaker.layout).

The Reading order result (`steps/order/`) holds `order.json`:

    {"pages": [{
        "index": 0,
        "source": "regions",                  # "regions", or "empty" (no blocks)
        "reason": null,                       # why the page was left unlinked (English), else null
        "request": {"provider": "claude", "model": "sonnet", "usage": {...} | null} | null,
        "articles": [{                        # in reading order
            "id": "p1-a1",                    # unique in the project, numbered in reading order per page
            "kind": "article",                # "article", "header", "footer" or "ad"
            "blocks": ["p1-b3", "p1-b4"],     # layout block ids in reading order
            "continues": null}]}],            # the previous page's article id this one continues, or null
     "links": [{"from": "p1-a3", "to": "p2-a1"}]}   # articles continued across pages, in page order

Block kinds (body, heading, caption, header, footer, table) come from the Layout result.
"""
from __future__ import annotations

ORDER_STEP = "order"
ORDER_FILE = "order.json"

CONTINUATION_PURPOSE = "continuations"
CONTINUATION_WORDS = 40  # words of an article's start (this page) or end (previous page) in the request

CONTINUATION_INSTRUCTIONS = """\
You link the articles of a scanned printed periodical (usually Czech) that continue from one page to the next. \
The data lists the articles of the previous page with their last words and the articles of this page with their \
first words, as recognised by OCR. The articles and their order are final; you only decide which of this page's \
articles goes on with an article of the previous page.

An article continues a previous-page article when its text goes on from that article's last words: it starts in \
the middle of a sentence or of a hyphenated word, without a heading of its own, or it is marked as a continuation \
(for example "dokončení", "pokračování"), and the previous article ends unfinished or says that it continues. An \
article that starts with its own heading is a new article. Each article continues at most one previous-page \
article, and each previous-page article is continued at most once.

List only the articles that continue, with the id of the previous-page article they continue; an empty list when \
none does. Answer only with JSON matching the schema."""

CONTINUATION_SCHEMA = {
    "type": "object",
    "properties": {"links": {"type": "array", "items": {
        "type": "object",
        "properties": {"article": {"type": "string"}, "continues": {"type": "string"}},
        "required": ["article", "continues"],
        "additionalProperties": False}}},
    "required": ["links"],
    "additionalProperties": False,
}

def article_words(article: dict, blocks: dict, texts: dict) -> list[str]:
    """An article's recognised words in order, its captions left out unless it has nothing else."""
    def ids(captions: bool) -> list[str]:
        return [word for block in article["blocks"] for line in blocks[block]["lines"] for word in line
                if captions or blocks[block]["kind"] != "caption"]
    return [texts[word] for word in ids(False) or ids(True) if word in texts]


def previous_articles(articles: list[dict], layout: dict, texts: dict) -> list[dict]:
    """A page's articles as the next page's request shows them: id, kind and their last CONTINUATION_WORDS words."""
    blocks = {block["id"]: block for block in layout["blocks"]}
    return [{"id": article["id"], "kind": article["kind"],
             "ends": " ".join(article_words(article, blocks, texts)[-CONTINUATION_WORDS:])} for article in articles]


def region_articles(layout: dict, layout_regions: list[dict], page_number: int) -> list[dict]:
    """The page's articles as the layout regions give them (ADR 0006): regions of one article in their order, each
    with its blocks in Layout's order, the article's kind the regions' kind; articles without a block are left out."""
    groups = []
    for region in layout_regions:
        blocks = [block["id"] for block in layout["blocks"] if block.get("region") == region["id"]]
        if not groups or groups[-1]["number"] != region["article"]:
            groups.append({"number": region["article"], "kind": region["kind"], "blocks": []})
        groups[-1]["blocks"] += blocks
    return [{"id": f"p{page_number}-a{position}", "kind": group["kind"], "blocks": group["blocks"], "continues": None}
            for position, group in enumerate((group for group in groups if group["blocks"]), start=1)]


def continuation_data(page: dict, page_count: int, articles: list[dict], layout: dict, texts: dict,
                      previous: dict | None) -> dict | None:
    """The data of a regions page's continuation request: the page's articles of kind article with their first
    words and the previous page's with their last words; None when there is nothing to link (no previous page, or
    no article on either page)."""
    shown = [item for item in (previous or {}).get("articles", []) if item["kind"] == "article"]
    blocks = {block["id"]: block for block in layout["blocks"]}
    current = [{"id": article["id"], "starts": " ".join(article_words(article, blocks, texts)[:CONTINUATION_WORDS])}
               for article in articles if article["kind"] == "article"]
    if not shown or not current:
        return None
    return {"page": page["index"] + 1, "pages": page_count, "articles": current,
            "previous_page": {"page": previous["page"], "articles": shown}}


def validate_links(answer, articles: set[str], previous_ids: set[str]) -> list[str]:
    """Problems of a continuation answer (English, empty when valid): known articles of kind article on both
    pages, each linked at most once."""
    if not isinstance(answer, dict) or not isinstance(answer.get("links"), list):
        return ["the answer has no list of links"]
    problems, linked, continued = [], set(), set()
    for position, link in enumerate(answer["links"], start=1):
        if not isinstance(link, dict):
            problems.append(f"link {position} is not an object")
            continue
        article, continues = link.get("article"), link.get("continues")
        article, continues = (value if isinstance(value, str) else repr(value) for value in (article, continues))
        if article not in articles:
            problems.append(f"link {position} names an unknown article {article!r} of this page")
        elif article in linked:
            problems.append(f"article {article!r} continues two articles")
        else:
            linked.add(article)
        if continues not in previous_ids:
            problems.append(f"link {position} continues an unknown article {continues!r} of the previous page")
        elif continues in continued:
            problems.append(f"article {continues!r} is continued twice")
        else:
            continued.add(continues)
    return problems


def link(articles: list[dict], answer: dict) -> list[dict]:
    """The page's articles, in their order, with the continuations of a valid answer."""
    continues = {item["article"]: item["continues"] for item in answer["links"]}
    return [{**article, "continues": continues.get(article["id"])} for article in articles]
