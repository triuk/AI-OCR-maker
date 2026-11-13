"""Reading order: the articles, their kinds and their order are the layout regions' (ADR 0006,
`reading_order.region_articles`); the AI only links articles continued from the previous page, from the text of
both pages and without an image (one request per page with a previous page). It never regroups or reorders the
articles: an invalid answer or no AI leaves the page unlinked with a warning, so no text is ever lost. Any other
AI failure (usage limit, login, timeout) fails the step with its Czech message so it can be rerun.
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


class OrderStep(Step):
    id = "order"

    def run(self, ctx: StepContext) -> None:
        pages = read_pages(ctx.result(PREPARED_STEP))
        layout_result, ocr = ctx.result(LAYOUT_STEP), ctx.result(OCR_STEP)
        results, links, unlinked, previous = [], [], [], None
        divided = divided_pages(ctx)
        ai = PageRequests()
        for page in pages:
            ctx.checkpoint()
            number = page["index"] + 1
            ctx.progress(page["index"], len(pages), f"Stránka {number} z {len(pages)}")
            started = time.monotonic()
            layout = read_layout_page(layout_result, page["index"]) or {"blocks": []}
            texts = {word["id"]: word["text"] for word in page_words(read_page(ocr, page["index"]) or {"lines": []})}
            entry = {"index": page["index"], "source": "regions" if layout["blocks"] else "empty", "reason": None,
                     "request": None}
            articles = rules.region_articles(layout, divided.get(page["index"], []), number)
            data = rules.continuation_data(page, len(pages), articles, layout, texts, previous)
            if data is not None:
                asked, failure = ai.ask(lambda: self._ask_links(ctx, data))
                if asked is not None:
                    answer, entry["request"], problems = asked
                    articles = articles if problems else rules.link(articles, answer)
                else:
                    problems = [failure] if failure else []
                if problems:
                    entry["reason"] = "; ".join(problems)
                    unlinked.append(number)
                elif ai.unavailable is not None:
                    entry["reason"] = f"{UNAVAILABLE}: {ai.unavailable}"
            entry["articles"] = articles
            results.append(entry)
            links += [{"from": article["continues"], "to": article["id"]} for article in articles
                      if article["continues"]]
            if articles:  # a page without text (a picture, a blank scan) does not break a continuation
                previous = {"page": number, "articles": rules.previous_articles(articles, layout, texts)}
            ctx.output.write_overlay(page["index"], self._overlay(entry, layout))
            ctx.record("reading_order", index=page["index"], source=entry["source"], reason=entry["reason"],
                       blocks=len(layout["blocks"]), articles=len(articles),
                       continues=[article["continues"] for article in articles if article["continues"]],
                       **request_fields(entry["request"]), seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        ctx.output.write_json(rules.ORDER_FILE, {"pages": results, "links": links})
        if ai.unavailable is not None:
            ctx.warn(f"AI není k dispozici ({ai.unavailable}), články proto nejsou propojené přes strany.")
        if unlinked:
            ctx.warn(f"AI vrátila neplatné pokračování článků {on_pages(unlinked)}; tyto strany nejsou propojené "
                     "s předchozí stranou. Žádný text nechybí.")

    @staticmethod
    def _ask_links(ctx: StepContext, data: dict):
        answer = ctx.language.ask(rules.CONTINUATION_INSTRUCTIONS, data, schema=rules.CONTINUATION_SCHEMA,
                                  purpose=rules.CONTINUATION_PURPOSE)
        articles = {item["id"] for item in data["articles"]}
        previous_ids = {item["id"] for item in data["previous_page"]["articles"]}
        problems = rules.validate_links(answer.data, articles, previous_ids)
        return answer.data, answer.request, problems

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
