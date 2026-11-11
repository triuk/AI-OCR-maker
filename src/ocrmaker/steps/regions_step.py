"""Layout (regions): divides every prepared page into AI layout regions in reading order before OCR (ADR 0006).

With the setting `layout_method` "blocks" (the method of ADR 0005) the step only records that and leaves the
page layout to the Layout (blocks) and Reading order steps. With "regions" the step runs in layout rounds:

- the first round gives every page one AI request and at most one corrective request (`ocrmaker.regions`),
  checked against a whole-page Tesseract pass that is used only for the check and never kept as text. A page
  whose answer is unusable, or every page when the AI is unavailable, gets one region over the whole page and a
  warning, so no text is ever lost;
- a later round (the person's "Zpracovat") asks again only for the pages whose layout edit has an unlocked region
  or a layout note (`ocrmaker.layout_review`), around the person's locked regions; every other page is taken over
  as it was. A page the AI cannot redo keeps its edit for the next round, with a warning;
- once the person approved the layout (the project's "layout" is "approved"), the run takes every page the person
  changed into the result as drawn, without AI, so the steps after Layout read only this result.

The result (`steps/regions/`) holds `regions.json` {"method", "round": the number of AI rounds so far,
"pages": [page entries of `ocrmaker.regions`], "kept": [{"index", "reason"}]: pages this round could not redo}.
"""
from __future__ import annotations

import time

from .. import layout_review
from .. import regions as method
from .. import tessdata, tesseract
from ..errors import StepError
from ..language import UNAVAILABLE, PageRequests, request_fields
from ..pages import PREPARED_STEP, read_pages
from ..regions import REGIONS_FILE, REGIONS_STEP
from ..step import ReportSection, Step, StepContext, StepResult
from ..wording import on_pages
from .ocr_step import recognise_page

PREVIEWS = "previews"


class RegionsStep(Step):
    id = REGIONS_STEP

    def run(self, ctx: StepContext) -> None:
        if ctx.settings.get("layout_method", "blocks") != "regions":
            ctx.output.write_json(REGIONS_FILE, {"method": "blocks", "pages": []})
            return
        pages = read_pages(ctx.result(PREPARED_STEP))
        previous = self._previous(ctx, len(pages))
        state = ctx.layout_review()
        edits = state["edits"]
        approving = previous is not None and state["state"] == layout_review.APPROVED
        number = 1 if previous is None else previous.get("round", 1) + (not approving)
        language = ctx.settings["language"]
        try:
            model = tessdata.ensure_model(language)
        except tessdata.ModelError as exc:  # already a Czech message
            raise StepError(str(exc)) from exc
        results, failed, kept = [], [], []
        ai = PageRequests()
        with tesseract.Recognizer(model, language) as recognizer:
            for page in pages:
                ctx.checkpoint()
                index = page["index"]
                ctx.progress(index, len(pages), f"Stránka {index + 1} z {len(pages)}")
                old = previous["pages"][index] if previous is not None else None
                edit = layout_review.current(edits, old) if old is not None else None
                if old is None:
                    entry = self._first(ctx, ai, recognizer, page)
                elif approving:
                    drawn = layout_review.drawn(old, edit, page)
                    entry = old if drawn is None else self._drawn(ctx, recognizer, page, old, drawn)
                elif layout_review.wanted(edit):
                    entry, reason = self._redo(ctx, ai, recognizer, page, old, edit, number)
                    if entry is None:
                        entry = old
                        kept.append({"index": index, "reason": reason})
                else:
                    entry = old
                if entry is not old and entry["source"] == "failed":
                    failed.append(index + 1)
                results.append(entry)
                ctx.output.write_overlay(index, self._overlay(entry))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        ctx.output.write_json(REGIONS_FILE, {"method": "regions", "round": number, "pages": results, "kept": kept})
        if ai.unavailable is not None:
            ctx.warn(f"AI není k dispozici ({ai.unavailable}), "
                     + ("každá stránka je proto jedna oblast." if previous is None
                        else "strany proto zůstaly, jak jste je upravili."))
        elif failed:
            ctx.warn(f"AI nevrátila použitelné rozvržení {on_pages(failed)}; tyto stránky jsou jedna oblast. "
                     "Žádný text nechybí.")
        if kept and ai.unavailable is None:
            one = len(kept) == 1
            ctx.warn(f"AI nevrátila použitelné rozvržení {on_pages([item['index'] + 1 for item in kept])}; "
                     + ("strana zůstala, jak jste ji upravili, a pošle se znovu v dalším kole." if one else
                        "strany zůstaly, jak jste je upravili, a pošlou se znovu v dalším kole."))

    @staticmethod
    def _previous(ctx: StepContext, count: int) -> dict | None:
        """The layout of the last round to go on from, or None when this is the first round."""
        result = ctx.previous_result()
        if result is None or not result.exists(REGIONS_FILE):
            return None
        data = result.read_json(REGIONS_FILE)
        return data if data["method"] == "regions" and len(data["pages"]) == count else None

    @staticmethod
    def _checked(recognizer, page: dict, ctx: StepContext) -> tuple[method.Page, object]:
        raster = page["raster"]
        raster_path = ctx.result(raster["step"]).path(raster["file"])
        return method.Page(page, recognise_page(recognizer, page, raster_path), raster_path), raster_path

    def _asker(self, ctx: StepContext, ai: PageRequests, answers: list, instructions: str, schema: dict,
               purpose: str):
        def ask(data, image):
            asked, failure = ai.ask(lambda: ctx.language.ask(instructions, data, schema=schema, images=[image],
                                                             purpose=purpose))
            if failure:
                raise ValueError(failure)
            if asked is None:
                return None
            answers.append(asked.request)
            return asked.data
        return ask

    def _first(self, ctx: StepContext, ai: PageRequests, recognizer, page: dict) -> dict:
        started = time.monotonic()
        checked, raster_path = self._checked(recognizer, page, ctx)
        image = method.preview(raster_path, ctx.output.path(f"{PREVIEWS}/page-{page['index'] + 1:04d}.png"))
        answers = []
        entry = method.layout_page(checked, self._asker(ctx, ai, answers, method.INSTRUCTIONS, method.SCHEMA,
                                                        method.PURPOSE), image)
        if entry["source"] == "failed" and ai.unavailable is not None:
            entry["reason"] = f"{UNAVAILABLE}: {ai.unavailable}"
        entry["request"] = answers[-1] if answers else None
        self._record(ctx, entry, started)
        return entry

    def _redo(self, ctx: StepContext, ai: PageRequests, recognizer, page: dict, old: dict, edit: dict,
              number: int) -> tuple[dict | None, str | None]:
        started = time.monotonic()
        checked, raster_path = self._checked(recognizer, page, ctx)
        image = method.preview(raster_path, ctx.output.path(f"{PREVIEWS}/page-{page['index'] + 1:04d}.png"))
        answers = []
        parts = layout_review.effective(old, edit, page)
        entry, reason = method.redo_page(checked, self._asker(ctx, ai, answers, method.REDO_INSTRUCTIONS,
                                                              method.REDO_SCHEMA, method.REDO_PURPOSE),
                                         image, parts, edit["note"].strip())
        if entry is None:
            if ai.unavailable is not None:
                reason = f"{UNAVAILABLE}: {ai.unavailable}"
            ctx.record("layout_regions", index=page["index"], source="kept", reason=reason, round=number,
                       seconds=round(time.monotonic() - started, 3))
            return None, reason
        entry["round"] = number
        entry["request"] = answers[-1] if answers else None
        self._record(ctx, entry, started)
        return entry, None

    def _drawn(self, ctx: StepContext, recognizer, page: dict, old: dict, parts: list[dict]) -> dict:
        started = time.monotonic()
        checked, _ = self._checked(recognizer, page, ctx)
        entry = method.drawn_page(checked, parts) | {"round": old.get("round", 1), "note": old.get("note"),
                                                     "proposal": old.get("proposal", old["regions"]),
                                                     "request": None}
        self._record(ctx, entry, started)
        return entry

    @staticmethod
    def _record(ctx: StepContext, entry: dict, started: float) -> None:
        ctx.record("layout_regions", index=entry["index"], source=entry["source"], reason=entry["reason"],
                   round=entry.get("round", 1), regions=len(entry["regions"]), requests=entry["requests"],
                   chosen=entry["chosen"], score=entry["check"]["score"], spots=len(entry["spots"]),
                   **request_fields(entry["request"]), seconds=round(time.monotonic() - started, 3))

    @staticmethod
    def _overlay(entry: dict) -> dict:
        return {"source": entry["source"], "reason": entry["reason"],
                "regions": [{key: region[key] for key in ("id", "article", "column", "continues", "kind", "title",
                                                          "orientation", "bbox")}
                            for region in entry["regions"]],
                "spots": entry["spots"], "problems": entry["check"]["problems"]}

    def report(self, result: StepResult) -> ReportSection | None:
        data = result.read_json(REGIONS_FILE)
        if data["method"] != "regions":
            return None
        pages = [{"index": entry["index"], "source": entry["source"], "round": entry.get("round", 1),
                  "regions": len(entry["regions"]),
                  "articles": len({region["article"] for region in entry["regions"]}),
                  "requests": entry["requests"], "chosen": entry["chosen"], "score": entry["check"]["score"],
                  "spots": len(entry["spots"]), "lost": entry["check"].get("lost", 0)} for entry in data["pages"]]
        failed = [page["index"] + 1 for page in pages if page["source"] == "failed"]
        drawn = [page["index"] + 1 for page in pages if page["source"] == "manual"]
        redone = [page["index"] + 1 for page in pages if page["round"] > 1]
        rounds = data.get("round", 1)
        summary = [f"- {len(pages)} pages, {sum(page['regions'] for page in pages)} regions, "
                   f"{sum(page['requests'] for page in pages)} AI requests in the pages' last rounds, "
                   f"{rounds} layout round{'' if rounds == 1 else 's'}",
                   f"- pages left with problems after the check: "
                   f"{sum(1 for page in pages if page['score'])}, uncovered ink spots: "
                   f"{sum(page['spots'] for page in pages)}, lost text lines: {sum(page['lost'] for page in pages)}"]
        if redone:
            summary.append(f"- laid out again in a later round: pages {redone}")
        if drawn:
            summary.append(f"- approved as the person drew them: pages {drawn}")
        if data.get("kept"):
            summary.append(f"- not redone in the last round (kept as edited): "
                           f"pages {[item['index'] + 1 for item in data['kept']]}")
        if failed:
            summary.append(f"- one region over the whole page (no usable answer): pages {failed}")
        return ReportSection("Layout regions", {"pages": pages, "round": data.get("round", 1),
                                                "kept": data.get("kept", [])}, summary)


STEP = RegionsStep()
