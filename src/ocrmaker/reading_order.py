"""Reading order: the AI groups a page's numbered blocks into articles; the rules are applied and checked here.

One request per page carries a page preview with the numbered blocks, the block kinds and short text
snippets, plus the articles of the previous page so that a continuation can be linked. The answer lists
groups of blocks ("articles") of kind article, header, footer or ad, each an ordered block list, and an
article may name the previous page's article it continues. The answer is valid only if every block is
assigned exactly once; otherwise the page gets the geometric order (Layout's numbering) and a warning.

The rules are enforced locally whatever order the answer uses: headers first, an article continued from the
previous page next, the other articles in the AI's order, ads after the articles, footers last. Within a
group the blocks keep the AI's order: Layout's kind caption only means small print, and only the AI sees
whether it captions a picture or is a row of its article. Tables are read row by row by their block
(ocrmaker.layout).

The Reading order result (`steps/order/`) holds `order.json`:

    {"pages": [{
        "index": 0,
        "source": "ai",                       # "ai", "geometric" (fallback), "regions" (articles of the layout
                                              # regions, `region_articles`) or "empty" (no blocks)
        "reason": null,                       # why the fallback was used (English), else null
        "request": {"provider": "claude", "model": "sonnet", "usage": {...} | null} | null,
        "articles": [{                        # in reading order
            "id": "p1-a1",                    # unique in the project, numbered in reading order per page
            "kind": "article",                # "article", "header", "footer" or "ad"
            "blocks": ["p1-b3", "p1-b4"],     # layout block ids in reading order
            "continues": null}]}],            # the previous page's article id this one continues, or null
     "links": [{"from": "p1-a3", "to": "p2-a1"}]}   # articles continued across pages, in page order

Block kinds (body, heading, caption, header, footer, table) come from the Layout result; an article's kind
tells which groups the AI recognised as running headers, footers or ads.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from .pages import apply_rect

ORDER_STEP = "order"
ORDER_FILE = "order.json"
PURPOSE = "reading_order"
GROUP_KINDS = ("article", "header", "footer", "ad")
PREVIEW_SIZE = 1600  # pixels on the long side of the page preview sent to the AI
SNIPPET_WORDS = 8

INSTRUCTIONS = """\
You determine the reading order of one scanned printed page (a newsletter, magazine or book page, usually \
Czech). The image shows the page with numbered rectangles; each rectangle is a block of recognised text. \
The data lists every block with its number, its kind as detected locally (body, heading, caption, header, \
footer, table), its first and last words, and the articles of the previous page.

Group the blocks into articles and give the reading order a person would use:
- An article is a heading with its text (and its captions). Order articles by where they start on the page; \
read each article completely, through all of its columns, before the next one starts.
- If an article continues from the previous page, give "continues" the id of that previous-page article; \
such an article comes first on the page. Otherwise "continues" is null.
- Running headers and footers at the page edges (page numbers, the publication name, the date) form groups \
of kind "header" or "footer".
- A block of kind caption is set in small print. If it stands at a picture, it captions it: it belongs to \
the article the picture illustrates and goes after its text. Otherwise it is part of its article's text: \
read it where it stands.
- Advertisements form groups of kind "ad"; they come after the articles. Recognise them from the image.
- A table is one block; keep it in its article.
- Use the kind "article" for everything else.

Every block number must appear exactly once in the whole answer; do not skip or repeat blocks and do not \
invent numbers. Answer only with JSON matching the schema."""

SCHEMA = {
    "type": "object",
    "properties": {"articles": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": list(GROUP_KINDS)},
                       "blocks": {"type": "array", "items": {"type": "integer"}},
                       "continues": {"type": ["string", "null"]}},
        "required": ["kind", "blocks", "continues"],
        "additionalProperties": False}}},
    "required": ["articles"],
    "additionalProperties": False,
}

# Rectangle colours of the page preview, by block kind (RGB 0..1).
KIND_COLOURS = {"body": (0.11, 0.49, 0.84), "heading": (0.52, 0.18, 0.75), "caption": (0.17, 0.54, 0.24),
                "header": (0.85, 0.42, 0.0), "footer": (0.85, 0.42, 0.0), "table": (0.0, 0.55, 0.55)}


def request_data(page: dict, page_count: int, layout: dict, texts: dict, previous: list[dict] | None) -> dict:
    """The data of one page's request: blocks with kinds and snippets, and the previous page's articles."""
    def words(ids: list[str]) -> list[str]:
        return [texts[word] for word in ids if word in texts]

    blocks = []
    for block in layout["blocks"]:
        ids = [word for line in block["lines"] for word in line]
        blocks.append({"number": block["number"], "kind": block["kind"], "words": len(ids),
                       "starts": " ".join(words(ids[:SNIPPET_WORDS])),
                       "ends": " ".join(words(ids[-SNIPPET_WORDS:]))})
    return {"page": page["index"] + 1, "pages": page_count, "blocks": blocks,
            "previous_page": previous}


def previous_articles(articles: list[dict], layout: dict, texts: dict) -> list[dict]:
    """A page's articles as the next page's request shows them: id, kind and their last words."""
    blocks = {block["id"]: block for block in layout["blocks"]}
    shown = []
    for article in articles:
        ids = [word for block in article["blocks"] for line in blocks[block]["lines"] for word in line
               if blocks[block]["kind"] != "caption"] or \
              [word for block in article["blocks"] for line in blocks[block]["lines"] for word in line]
        shown.append({"id": article["id"], "kind": article["kind"],
                      "ends": " ".join(texts[word] for word in ids[-SNIPPET_WORDS:] if word in texts)})
    return shown


def validate(answer, block_count: int, previous_ids: set[str]) -> list[str]:
    """Problems of an answer (English, empty when valid): every block exactly once, known kinds and links."""
    if not isinstance(answer, dict) or not isinstance(answer.get("articles"), list):
        return ["the answer has no list of articles"]
    problems, seen, continued = [], {}, set()
    for position, article in enumerate(answer["articles"], start=1):
        if not isinstance(article, dict):
            problems.append(f"article {position} is not an object")
            continue
        if article.get("kind") not in GROUP_KINDS:
            problems.append(f"article {position} has an unknown kind {article.get('kind')!r}")
        blocks = article.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            problems.append(f"article {position} has no blocks")
            continue
        for number in blocks:
            if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= block_count:
                problems.append(f"article {position} names an unknown block {number!r}")
            elif number in seen:
                problems.append(f"block {number} is assigned twice (articles {seen[number]} and {position})")
            else:
                seen[number] = position
        continues = article.get("continues")
        if continues is not None:
            if article.get("kind") != "article":
                problems.append(f"article {position} of kind {article.get('kind')!r} cannot continue an article")
            elif continues not in previous_ids:
                problems.append(f"article {position} continues an unknown article {continues!r}")
            elif continues in continued:
                problems.append(f"article {continues!r} is continued twice")
            else:
                continued.add(continues)
    missing = [number for number in range(1, block_count + 1) if number not in seen]
    if missing:
        problems.append("blocks not assigned: " + ", ".join(map(str, missing)))
    return problems


def compose(groups: list[dict], layout: dict, page_number: int) -> list[dict]:
    """The page's articles in reading order under the rules; groups name blocks by number."""
    blocks = {block["number"]: block for block in layout["blocks"]}

    def rank(group: dict) -> int:
        if group["kind"] == "header":
            return 0
        if group["kind"] == "article":
            return 1 if group.get("continues") else 2
        return 3 if group["kind"] == "ad" else 4

    ordered = sorted(groups, key=rank)  # stable: the AI's order within each rank
    articles = []
    for position, group in enumerate(ordered, start=1):
        articles.append({"id": f"p{page_number}-a{position}", "kind": group["kind"],
                         "blocks": [blocks[number]["id"] for number in group["blocks"]],
                         "continues": group.get("continues")})
    return articles


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


def geometric_groups(layout: dict) -> list[dict]:
    """The fallback: running headers, everything else in Layout's geometric order, running footers."""
    groups = []
    for kind, members in (("header", ["header"]), ("article", ["body", "heading", "caption", "table"]),
                          ("footer", ["footer"])):
        numbers = [block["number"] for block in layout["blocks"] if block["kind"] in members]
        if numbers:
            groups.append({"kind": kind, "blocks": numbers, "continues": None})
    # A kind this module does not know still keeps its text, as part of the article.
    known = {number for group in groups for number in group["blocks"]}
    rest = [block["number"] for block in layout["blocks"] if block["number"] not in known]
    if rest:
        article = next((group for group in groups if group["kind"] == "article"), None)
        if article is None:
            groups.append({"kind": "article", "blocks": rest, "continues": None})
        else:
            article["blocks"] = sorted(article["blocks"] + rest)
    return groups


def render_preview(raster_path: Path, to_raster, layout: dict, target: Path) -> Path:
    """The page preview the AI sees: the working raster with numbered block rectangles coloured by kind."""
    with pymupdf.open() as document:
        image = pymupdf.Pixmap(str(raster_path))
        width, height = image.width, image.height
        scale = min(1.0, PREVIEW_SIZE / max(width, height))
        page = document.new_page(width=width * scale, height=height * scale)
        page.insert_image(page.rect, pixmap=image)
        label = max(11.0, 18.0 * max(width, height) * scale / PREVIEW_SIZE)
        for block in layout["blocks"]:
            colour = KIND_COLOURS.get(block["kind"], (0.4, 0.4, 0.4))
            x0, y0, x1, y1 = (value * scale for value in apply_rect(to_raster, block["bbox"]))
            page.draw_rect(pymupdf.Rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2), color=colour, width=2)
            text = str(block["number"])
            tag = pymupdf.Rect(x0 - 2, y0 - 2, x0 - 2 + label * (0.62 * len(text) + 0.5), y0 - 2 + label * 1.15)
            page.draw_rect(tag, color=colour, fill=colour, width=0)
            page.insert_text((tag.x0 + label * 0.25, tag.y1 - label * 0.25), text, fontsize=label,
                             fontname="hebo", color=(1, 1, 1))
        page.get_pixmap(alpha=False).save(str(target))
    return target
