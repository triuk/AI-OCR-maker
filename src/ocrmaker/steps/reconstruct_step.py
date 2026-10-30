"""AI reconstruction: one AI request per page with problem spots determines their printed text (ADR 0001).

The request, the answer, the outcome and the result format are described in `ocrmaker.reconstruction`.
An answer that addresses anything but the marked spots, or breaks the schema, is rejected and the page's
spots get the illegible marker, with a warning. Without any AI provider every spot gets the marker and the
step warns once; any other AI failure (usage limit, login, timeout) fails the step with its Czech message.
"""
from __future__ import annotations

import time

from .. import inkgaps, reconstruction as rules
from ..language import INVALID_OUTPUT, MISSING_CLI, UNAVAILABLE, LanguageError
from ..layout import LAYOUT_STEP, read_layout_page
from ..pages import PREPARED_STEP, apply_rect, read_pages
from ..reading_order import ORDER_FILE, ORDER_STEP
from ..reconstruction import ACCEPTED, PUBLISHER_ERROR, UNRESOLVED
from ..step import ReportSection, Step, StepContext, StepResult
from ..transcription import MARKER
from ..words import OCR_STEP, page_file, read_page

PROBLEMS_STEP = "problems"
CROPS = "crops"
THRESHOLD_SETTING = "reconstruction_accept_confidence"


def _page_list(numbers: list[int]) -> str:
    shown = ", ".join(str(number) for number in numbers[:10])
    return shown + (" …" if len(numbers) > 10 else "")


def _counts(records: list[dict]) -> dict:
    return {"spots": sum(len(record["ids"]) for record in records),
            "resolved": sum(len(record["ids"]) for record in records if record["status"] != UNRESOLVED),
            "markers": sum(len(record["words"]) for record in records if record["status"] == UNRESOLVED),
            "publisher_errors": sum(record["status"] == PUBLISHER_ERROR for record in records)}


class ReconstructStep(Step):
    id = "reconstruct"

    def run(self, ctx: StepContext) -> None:
        pages = read_pages(ctx.result(PREPARED_STEP))
        ocr, problems = ctx.result(OCR_STEP), ctx.result(PROBLEMS_STEP)
        layout_result = ctx.result(LAYOUT_STEP) if ctx.has_result(LAYOUT_STEP) else None
        order = ctx.result(ORDER_STEP).read_json(ORDER_FILE) if ctx.has_result(ORDER_STEP) else {"pages": []}
        order_pages = {item["index"]: item for item in order["pages"]}
        threshold = float(ctx.settings.get(THRESHOLD_SETTING, rules.DEFAULT_THRESHOLD))
        summaries, rejected = [], []
        unavailable: str | None = None
        for page in pages:
            ctx.checkpoint()
            index, number = page["index"], page["index"] + 1
            ctx.progress(index, len(pages), f"Stránka {number} z {len(pages)}")
            started = time.monotonic()
            spots = (problems.read_json(page_file(index))["spots"] if problems.exists(page_file(index)) else [])
            entry = {"index": index, "request": None, "rejected": None, "reconstructions": []}
            if spots:
                recognised = read_page(ocr, index) or {"index": index, "lines": []}
                layout = read_layout_page(layout_result, index) if layout_result else None
                articles = (order_pages.get(index) or {}).get("articles")
                prepared = self._prepare(ctx, page, recognised, layout, articles, spots)
                answer = None
                if unavailable is None:
                    try:
                        answer = ctx.language.ask(rules.INSTRUCTIONS, prepared["data"], schema=rules.SCHEMA,
                                                  images=prepared["images"], purpose=rules.PURPOSE)
                    except LanguageError as exc:
                        if exc.code in (UNAVAILABLE, MISSING_CLI):
                            unavailable = str(exc)
                        elif exc.code != INVALID_OUTPUT:
                            raise
                        else:
                            entry["rejected"] = f"{exc.code}: {exc}"
                if answer is not None:
                    entry["request"] = {"provider": answer.provider, "model": answer.model, "usage": answer.usage}
                    found = rules.validate(answer.data, prepared["spots"], prepared["positions"])
                    if found:
                        entry["rejected"] = "; ".join(found)
                        answer = None
                if entry["rejected"]:
                    rejected.append(number)
                reason = "rejected" if entry["rejected"] else "unavailable" if unavailable else "no_answer"
                entry["reconstructions"] = self._records(prepared, answer, threshold, reason)
            ctx.output.write_json(page_file(index), entry)
            counts = _counts(entry["reconstructions"])
            summary = {"index": index, "file": page_file(index), **counts, "rejected": entry["rejected"]}
            summaries.append(summary)
            ctx.record("reconstruction_page", **summary, provider=(entry["request"] or {}).get("provider"),
                       model=(entry["request"] or {}).get("model"), usage=(entry["request"] or {}).get("usage"),
                       seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        total = {key: sum(summary[key] for summary in summaries)
                 for key in ("spots", "resolved", "markers", "publisher_errors")}
        ctx.output.write_json(rules.RECONSTRUCT_FILE, {"threshold": threshold, "pages": summaries, "total": total})
        if unavailable is not None:
            ctx.warn(f"AI není k dispozici ({unavailable}), problémová místa proto dostala značku {MARKER} "
                     "a čekají na ruční kontrolu.")
        if rejected:
            where = "na straně" if len(rejected) == 1 else "na stranách"
            ctx.warn(f"AI vrátila neplatnou rekonstrukci {where} {_page_list(rejected)} (odpověď mimo označená "
                     f"místa nebo v chybném tvaru); odpověď byla odmítnuta a tamní problémová místa dostala "
                     f"značku {MARKER}.")

    def _prepare(self, ctx: StepContext, page: dict, recognised: dict, layout: dict | None,
                 articles: list[dict] | None, spots: list[dict]) -> dict:
        """The request data and crops of one page, plus what checking and recording the answer needs."""
        index = page["index"]
        words = {word["id"]: word for line in recognised["lines"] for word in line["words"]}
        lines = {word["id"]: line for line in recognised["lines"] for word in line["words"]}
        positions = {word["id"]: (number, place) for number, line in enumerate(recognised["lines"])
                     for place, word in enumerate(line["words"])}
        marked = {spot["id"]: spot for spot in spots if spot["id"] in words}
        raster = page["raster"]
        ink = inkgaps.load_ink(ctx.result(raster["step"]).path(raster["file"]))
        paragraphs, images = [], []
        inputs: dict[str, dict] = {}
        for paragraph in rules.paragraphs(recognised, layout, list(marked.values())):
            spot_lines = [number for number, line in enumerate(paragraph["lines"]) for word in line if word in marked]
            covers, ranges = rules.crop_ranges(len(paragraph["lines"]), spot_lines)
            crops = []
            for first, last in ranges:
                chosen = [word for line in paragraph["lines"][first:last + 1] for word in line if word in words]
                box = rules.union(words[word]["bbox"] for word in chosen)
                height = max(pixels[3] - pixels[1] for word in chosen
                             for pixels in [apply_rect(raster["to_raster"], words[word]["bbox"])])
                shown = [word for word in chosen if word in marked]
                name = f"{CROPS}/page-{index + 1:04d}-{len(images) + 1:02d}.png"
                images.append(rules.render_crop(ink, raster["to_raster"], box,
                                                [words[word]["bbox"] for word in shown], abs(height),
                                                ctx.output.path(name)))
                crops.append({"image": len(images), "file": name, "covers": covers,
                              "lines": [first + 1, last + 1], "spots": shown})
            text = rules.text_of(paragraph["lines"], words, set(marked))
            around = rules.context(paragraph, layout, articles, words, set(marked))
            paragraphs.append({"id": paragraph["id"], "kind": paragraph["kind"], "text": text, "context": around,
                               "images": [{key: crop[key] for key in ("image", "covers", "lines", "spots")}
                                          for crop in crops]})
            for number, line in enumerate(paragraph["lines"], start=1):
                for word in line:
                    if word in marked:
                        inputs[word] = {"paragraph": paragraph["id"], "line": number, "text": text,
                                        "context": around,
                                        "images": [crop["file"] for crop in crops if word in crop["spots"]]}
        data_spots = [{"id": spot_id, "kind": spot["kind"], "signals": spot["signals"], "reading": spot["text"],
                       "confidence": spot["confidence"], "paragraph": inputs[spot_id]["paragraph"],
                       "line": inputs[spot_id]["line"]} for spot_id, spot in marked.items()]
        data = {"page": index + 1, "language": ctx.settings.get("language"), "paragraphs": paragraphs,
                "spots": data_spots}
        return {"data": data, "images": images, "spots": marked, "positions": positions, "words": words,
                "lines": lines, "inputs": inputs, "ink": ink, "raster": raster}

    def _records(self, prepared: dict, answer, threshold: float, reason: str) -> list[dict]:
        """One reconstruction record per reading, and one unresolved record per spot no reading covers."""
        spots = prepared["spots"]
        readings = answer.data["readings"] if answer is not None else []
        source = {"provider": answer.provider, "model": answer.model} if answer is not None \
            else {"provider": None, "model": None}
        covered = {word_id for reading in readings for word_id in reading["ids"]}
        records = []
        for reading in readings:
            ids = sorted(reading["ids"], key=lambda word_id: prepared["positions"][word_id])
            confidence = float(reading["confidence"])
            error = reading["error"]
            if error == "none" and reading["words"] != [spots[word_id]["text"] for word_id in ids]:
                error = "recognition"
            status = UNRESOLVED if confidence < threshold else PUBLISHER_ERROR if error == "publisher" else ACCEPTED
            record = self._record(prepared, ids, status, "low_confidence" if status == UNRESOLVED else None)
            record.update(candidate=list(reading["words"]), confidence=confidence, reasoning=reading["reasoning"],
                          error=error, intended=reading["intended"] if error == "publisher" else None, **source)
            if status != UNRESOLVED:
                record["words"] = self._placed(prepared, ids, reading["words"])
            records.append(record)
        for spot_id in spots:
            if spot_id not in covered:
                records.append({**self._record(prepared, [spot_id], UNRESOLVED, reason), **source})
        order = prepared["positions"]
        return sorted(records, key=lambda record: order[record["ids"][0]])

    @staticmethod
    def _record(prepared: dict, ids: list[str], status: str, reason: str | None) -> dict:
        spots, words = prepared["spots"], prepared["words"]
        first = prepared["inputs"].get(ids[0], {})
        return {"ids": ids, "status": status, "reason": reason, "source": "ai",
                "input": {"spots": [{key: spots[word_id][key] for key in ("id", "kind", "signals", "confidence")}
                                    | {"reading": spots[word_id]["text"]} for word_id in ids],
                          "paragraph": first.get("paragraph"), "text": first.get("text"),
                          "context": first.get("context"),
                          "images": sorted({image for word_id in ids
                                            for image in prepared["inputs"].get(word_id, {}).get("images", [])})},
                "candidate": None, "confidence": None, "reasoning": None, "error": None, "intended": None,
                "words": [{"id": word_id, "text": MARKER, "bbox": words[word_id]["bbox"],
                           "hint": words[word_id]["text"]} for word_id in ids]}

    @staticmethod
    def _placed(prepared: dict, ids: list[str], texts: list[str]) -> list[dict]:
        """The accepted words in their boxes: the spot's own box for one word in one box, else measured."""
        words = prepared["words"]
        if len(ids) == 1 and len(texts) == 1:
            return [{"id": ids[0], "text": texts[0], "bbox": words[ids[0]]["bbox"]}]
        line = prepared["lines"][ids[0]]
        others = [word["bbox"] for word in line["words"] if word["id"] not in ids]
        raster = prepared["raster"]
        boxes = rules.measure_words(prepared["ink"], raster["to_raster"], raster["to_source"],
                                    [words[word_id]["bbox"] for word_id in ids], line["bbox"], others, texts)
        names = ids[:len(texts)] + [f"{ids[0]}-{number}" for number in range(len(ids) + 1, len(texts) + 1)]
        return [{"id": name, "text": text, "bbox": box} for name, text, box in zip(names, texts, boxes)]

    def overlay(self, result: StepResult, page: int) -> dict | None:
        """The words reconstruction wrote, each with its status, confidence, candidate, hint and reasoning."""
        name = page_file(page)
        if not result.exists(name):
            return None
        entry = result.read_json(name)
        records = entry["reconstructions"]
        shown = [{"id": word["id"], "ids": record["ids"], "bbox": word["bbox"], "text": word["text"],
                  "status": record["status"], "reason": record["reason"], "confidence": record["confidence"],
                  "candidate": " ".join(record["candidate"]) if record["candidate"] else None,
                  "hint": " ".join(record["input"]["spots"][i]["reading"] for i in range(len(record["ids"]))),
                  "reasoning": record["reasoning"], "error": record["error"], "intended": record["intended"],
                  "provider": record["provider"], "model": record["model"]}
                 for record in records for word in record["words"]]
        return {"words": shown, "counts": _counts(records), "rejected": entry["rejected"]}

    def report(self, result: StepResult) -> ReportSection:
        """Resolved spots, illegible markers and publisher errors per page and in total; the publisher errors."""
        summary_data = result.read_json(rules.RECONSTRUCT_FILE)
        pages = [{key: page[key] for key in ("index", "spots", "resolved", "markers", "publisher_errors",
                                             "rejected")} for page in summary_data["pages"]]
        errors = []
        for page in summary_data["pages"]:
            entry = result.read_json(page["file"])
            errors += [{"page": page["index"], "ids": record["ids"],
                        "printed": " ".join(record["candidate"]), "intended": record["intended"],
                        "reasoning": record["reasoning"]}
                       for record in entry["reconstructions"] if record["status"] == PUBLISHER_ERROR]
        total = summary_data["total"]
        summary = [f"{total['spots']} problem spots: {total['resolved']} resolved, {total['markers']} illegible "
                   f"markers, {total['publisher_errors']} publisher errors kept as printed (reconstructions "
                   f"accepted from confidence {summary_data['threshold']})."]
        rejected = [page for page in pages if page["rejected"]]
        if rejected:
            summary.append("Rejected answers on pages " + ", ".join(str(page["index"] + 1) for page in rejected)
                           + " (see the report JSON).")
        if errors:
            summary += ["", "Publisher errors:", ""]
            summary += [f"- page {error['page'] + 1}, {', '.join(error['ids'])}: printed {error['printed']!r}"
                        + (f", meant {error['intended']!r}" if error["intended"] else "")
                        for error in errors[:REPORTED_ERRORS]]
            if len(errors) > REPORTED_ERRORS:
                summary.append(f"- and {len(errors) - REPORTED_ERRORS} more (see the report JSON)")
        return ReportSection("AI reconstruction", {"threshold": summary_data["threshold"], "total": total,
                                                   "pages": pages, "publisher_errors": errors}, summary)


# Publisher errors listed one by one in the human summary; the report JSON has them all.
REPORTED_ERRORS = 40

STEP = ReconstructStep()
