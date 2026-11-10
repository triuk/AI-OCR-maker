"""Reading order: one AI request per page groups Layout's numbered blocks into articles in reading order.

On pages divided into layout regions (ADR 0006) the articles, their kinds and their order are the regions'
(`reading_order.region_articles`) and no request is made; links of articles continued across pages follow (#19).

An invalid answer gives the page the geometric order and a warning, so no text is ever lost. Without any AI
provider (none installed or configured) every page gets the geometric order and the step warns once; any
other AI failure (usage limit, login, timeout) fails the step with its Czech message so it can be rerun.
The result format is described in `ocrmaker.reading_order`.
"""
from __future__ import annotations

import time

from .. import reading_order as rules
from ..language import UNAVAILABLE, PageRequests, request_fields
from ..layout import LAYOUT_STEP, read_layout_page
from ..pages import PREPARED_STEP, read_pages
from ..regions import divided_pages
from ..step import Step, StepContext
from ..words import OCR_STEP, page_words, read_page
from ..wording import on_pages

PREVIEWS = "previews"


class OrderStep(Step):
    id = "order"

    def run(self, ctx: StepContext) -> None:
        pages = read_pages(ctx.result(PREPARED_STEP))
        layout_result, ocr = ctx.result(LAYOUT_STEP), ctx.result(OCR_STEP)
        results, links, invalid, previous = [], [], [], None
        divided = divided_pages(ctx)
        ai = PageRequests()
        for page in pages:
            ctx.checkpoint()
            number = page["index"] + 1
            ctx.progress(page["index"], len(pages), f"Stránka {number} z {len(pages)}")
            started = time.monotonic()
            layout = read_layout_page(layout_result, page["index"]) or {"blocks": []}
            texts = {word["id"]: word["text"] for word in page_words(read_page(ocr, page["index"]) or {"lines": []})}
            entry = {"index": page["index"], "source": "ai", "reason": None, "request": None}
            groups = None
            if not layout["blocks"]:
                entry["source"] = "empty"
            elif divided is not None:
                entry["source"] = "regions"
            else:
                asked, failure = ai.ask(lambda: self._ask(ctx, page, len(pages), layout, texts, previous))
                if asked is not None:
                    groups, entry["request"], problems = asked
                else:
                    problems = [failure] if failure else []
                if problems:
                    groups = None
                    entry["reason"] = "; ".join(problems)
                    invalid.append(number)
            if groups is None and layout["blocks"] and entry["source"] != "regions":
                entry["source"] = "geometric"
                entry["reason"] = entry["reason"] or f"{UNAVAILABLE}: {ai.unavailable}"
                groups = rules.geometric_groups(layout)
            if entry["source"] == "regions":
                articles = rules.region_articles(layout, divided.get(page["index"], []), number)
            else:
                articles = rules.compose(groups or [], layout, number)
            entry["articles"] = articles
            results.append(entry)
            links += [{"from": article["continues"], "to": article["id"]} for article in articles
                      if article["continues"]]
            previous = {"page": number, "articles": rules.previous_articles(articles, layout, texts)}
            ctx.output.write_overlay(page["index"], self._overlay(entry, layout))
            ctx.record("reading_order", index=page["index"], source=entry["source"], reason=entry["reason"],
                       blocks=len(layout["blocks"]), articles=len(articles),
                       continues=[article["continues"] for article in articles if article["continues"]],
                       **request_fields(entry["request"]), seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        ctx.output.write_json(rules.ORDER_FILE, {"pages": results, "links": links})
        if ai.unavailable is not None:
            ctx.warn(f"AI není k dispozici ({ai.unavailable}), pořadí čtení je proto geometrické: sloupce zleva "
                     "doprava, každý shora dolů.")
        if invalid:
            ctx.warn(f"AI vrátila neplatné pořadí čtení {on_pages(invalid)}; použito geometrické "
                     "pořadí (sloupce zleva doprava). Žádný text nechybí.")

    def _ask(self, ctx: StepContext, page: dict, page_count: int, layout: dict, texts: dict, previous):
        raster = page["raster"]
        image = rules.render_preview(ctx.result(raster["step"]).path(raster["file"]), raster["to_raster"], layout,
                                     ctx.output.path(f"{PREVIEWS}/page-{page['index'] + 1:04d}.png"))
        answer = ctx.language.ask(rules.INSTRUCTIONS,
                                  rules.request_data(page, page_count, layout, texts, previous),
                                  schema=rules.SCHEMA, images=[image], purpose=rules.PURPOSE)
        request = answer.request
        previous_ids = {article["id"] for article in (previous or {}).get("articles", [])
                        if article["kind"] == "article"}
        problems = rules.validate(answer.data, len(layout["blocks"]), previous_ids)
        return (None if problems else answer.data["articles"]), request, problems

    @staticmethod
    def _overlay(entry: dict, layout: dict) -> dict:
        blocks = {block["id"]: block for block in layout["blocks"]}
        return {"source": entry["source"], "reason": entry["reason"],
                "articles": [{"id": article["id"], "number": position, "kind": article["kind"],
                              "continues": article["continues"],
                              "blocks": [{"id": block, "number": blocks[block]["number"],
                                          "kind": blocks[block]["kind"], "bbox": blocks[block]["bbox"]}
                                         for block in article["blocks"]]}
                             for position, article in enumerate(entry["articles"], start=1)]}


STEP = OrderStep()
