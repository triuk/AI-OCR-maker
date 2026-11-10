"""Layout (regions): divides every prepared page into AI layout regions in reading order before OCR (ADR 0006).

With the setting `layout_method` "blocks" (the method of ADR 0005) the step only records that and leaves the
page layout to the Layout (blocks) and Reading order steps. With "regions" every page gets one AI request and at
most one corrective request (`ocrmaker.regions`), checked against a whole-page Tesseract pass that is used only for
the check and never kept as text. A page whose answer is unusable, or every page when the AI is unavailable, gets
one region over the whole page and a warning, so no text is ever lost.

The result (`steps/regions/`) holds `regions.json` {"method", "pages": [page entries of `ocrmaker.regions`]}.
"""
from __future__ import annotations

import time

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
        language = ctx.settings["language"]
        try:
            model = tessdata.ensure_model(language)
        except tessdata.ModelError as exc:  # already a Czech message
            raise StepError(str(exc)) from exc
        results, failed = [], []
        ai = PageRequests()
        with tesseract.Recognizer(model, language) as recognizer:
            for page in pages:
                ctx.checkpoint()
                number = page["index"] + 1
                ctx.progress(page["index"], len(pages), f"Stránka {number} z {len(pages)}")
                started = time.monotonic()
                raster = page["raster"]
                raster_path = ctx.result(raster["step"]).path(raster["file"])
                checked = method.Page(page, recognise_page(recognizer, page, raster_path), raster_path)
                image = method.preview(raster_path, ctx.output.path(f"{PREVIEWS}/page-{number:04d}.png"))
                answers = []

                def ask(data, image):
                    asked, failure = ai.ask(lambda: ctx.language.ask(
                        method.INSTRUCTIONS, data, schema=method.SCHEMA, images=[image], purpose=method.PURPOSE))
                    if failure:
                        raise ValueError(failure)
                    if asked is None:
                        return None
                    answers.append(asked.request)
                    return asked.data

                entry = method.layout_page(checked, ask, image)
                if entry["source"] == "failed":
                    if ai.unavailable is not None:
                        entry["reason"] = f"{UNAVAILABLE}: {ai.unavailable}"
                    failed.append(number)
                entry["request"] = answers[-1] if answers else None
                results.append(entry)
                ctx.output.write_overlay(page["index"], self._overlay(entry))
                ctx.record("layout_regions", index=page["index"], source=entry["source"], reason=entry["reason"],
                           regions=len(entry["regions"]), requests=entry["requests"], chosen=entry["chosen"],
                           score=entry["check"]["score"], spots=len(entry["spots"]),
                           **request_fields(entry["request"]), seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        ctx.output.write_json(REGIONS_FILE, {"method": "regions", "pages": results})
        if ai.unavailable is not None:
            ctx.warn(f"AI není k dispozici ({ai.unavailable}), každá stránka je proto jedna oblast.")
        elif failed:
            ctx.warn(f"AI nevrátila použitelné rozvržení {on_pages(failed)}; tyto stránky jsou jedna oblast. "
                     "Žádný text nechybí.")

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
        pages = [{"index": entry["index"], "source": entry["source"], "regions": len(entry["regions"]),
                  "articles": len({region["article"] for region in entry["regions"]}),
                  "requests": entry["requests"], "chosen": entry["chosen"], "score": entry["check"]["score"],
                  "spots": len(entry["spots"])} for entry in data["pages"]]
        failed = [page["index"] + 1 for page in pages if page["source"] == "failed"]
        summary = [f"- {len(pages)} pages, {sum(page['regions'] for page in pages)} regions, "
                   f"{sum(page['requests'] for page in pages)} AI requests",
                   f"- pages left with problems after the check: "
                   f"{sum(1 for page in pages if page['score'])}, uncovered ink spots: "
                   f"{sum(page['spots'] for page in pages)}"]
        if failed:
            summary.append(f"- one region over the whole page (no usable answer): pages {failed}")
        return ReportSection("Layout regions", {"pages": pages}, summary)


STEP = RegionsStep()
