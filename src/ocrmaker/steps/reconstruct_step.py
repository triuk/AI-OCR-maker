"""AI reconstruction: one AI request per page with problem spots determines their printed text (ADR 0001).

The request, the answer, the outcome and the result format are described in `ocrmaker.reconstruction`.
A spot left unresolved keeps its printed OCR text when it is a doubtful reading and gets the illegible marker
when it is an unreadable word (ADR 0001); either way it stays listed for manual review. Answer items that
address anything but the marked spots, or break the schema, are rejected with a warning, and their spots stay
unresolved. Without any AI provider every spot stays unresolved and the step warns once; any other AI
failure (usage limit, login, timeout) fails the step with its Czech message.
"""
from __future__ import annotations

import time

from .. import inkgaps, reconstruction as rules
from ..language import PageRequests, request_fields
from ..layout import LAYOUT_STEP, read_layout_page
from ..pages import PREPARED_STEP, apply_rect, read_pages, union
from ..problems import continuation
from ..reading_order import ORDER_FILE, ORDER_STEP
from ..reconstruction import ACCEPTED, PUBLISHER_ERROR, UNRESOLVED
from ..step import ReportSection, Step, StepContext, StepResult
from ..transcription import MARKER
from ..words import OCR_STEP, page_file, read_page
from ..wording import on_pages

PROBLEMS_STEP = "problems"
CROPS = "crops"
THRESHOLD_SETTING = "reconstruction_accept_confidence"


def _counts(records: list[dict]) -> dict:
    left = [word["text"] for record in records if record["status"] == UNRESOLVED for word in record["words"]]
    return {"spots": sum(len(record["ids"]) for record in records),
            "resolved": sum(len(record["ids"]) for record in records if record["status"] != UNRESOLVED),
            "markers": sum(text == MARKER for text in left), "kept": sum(text != MARKER for text in left),
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
        summaries, rejected, ignored = [], [], []
        ai = PageRequests()
        for page in pages:
            ctx.checkpoint()
            index, number = page["index"], page["index"] + 1
            ctx.progress(index, len(pages), f"Stránka {number} z {len(pages)}")
            started = time.monotonic()
            spots = (problems.read_json(page_file(index))["spots"] if problems.exists(page_file(index)) else [])
            entry = {"index": index, "request": None, "rejected": None, "ignored": [], "reconstructions": []}
            if spots:
                recognised = read_page(ocr, index) or {"index": index, "lines": []}
                layout = read_layout_page(layout_result, index) if layout_result else None
                articles = (order_pages.get(index) or {}).get("articles")
                prepared = self._prepare(ctx, page, recognised, layout, articles, spots)
                readings = []
                answer, entry["rejected"] = ai.ask(lambda: ctx.language.ask(
                    rules.INSTRUCTIONS, prepared["data"], schema=rules.SCHEMA, images=prepared["images"],
                    purpose=rules.PURPOSE))
                if answer is not None:
                    entry["request"] = answer.request
                    try:
                        readings, entry["ignored"] = rules.validate(answer.data, prepared["spots"],
                                                                    prepared["positions"], prepared["breaks"])
                    except ValueError as exc:
                        entry["rejected"] = str(exc)
                        answer = None
                if entry["rejected"]:
                    rejected.append(number)
                if entry["ignored"]:
                    ignored.append(number)
                reason = "rejected" if entry["rejected"] else "unavailable" if ai.unavailable else "no_answer"
                refused = {word_id for item in entry["ignored"] if isinstance(item["item"], dict)
                           and isinstance(item["item"].get("ids"), list) for word_id in item["item"]["ids"]
                           if isinstance(word_id, str)}
                entry["reconstructions"] = self._records(prepared, answer, readings, threshold, reason, refused)
            ctx.output.write_json(page_file(index), entry)
            counts = _counts(entry["reconstructions"])
            summary = {"index": index, "file": page_file(index), **counts, "rejected": entry["rejected"]}
            summaries.append(summary)
            ctx.record("reconstruction_page", **summary, **request_fields(entry["request"]),
                       seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        total = {key: sum(summary[key] for summary in summaries)
                 for key in ("spots", "resolved", "markers", "kept", "publisher_errors")}
        ctx.output.write_json(rules.RECONSTRUCT_FILE, {"threshold": threshold, "pages": summaries, "total": total})
        if ai.unavailable is not None:
            ctx.warn(f"AI není k dispozici ({ai.unavailable}), problémová místa proto zůstala nevyřešená: nečitelná "
                     f"slova dostala značku {MARKER}, pochybná čtení si ponechala text z OCR; obojí čeká na ruční "
                     "kontrolu.")
        if rejected:
            ctx.warn(f"AI vrátila neplatnou rekonstrukci {on_pages(rejected)} (odpověď v chybném tvaru); "
                     "odpověď byla odmítnuta a tamní problémová místa zůstala nevyřešená.")
        if ignored:
            ctx.warn(f"AI vrátila neplatná čtení {on_pages(ignored)} (mimo označená místa, opakovaná nebo "
                     "v chybném tvaru); ta byla odmítnuta a jejich místa zůstala nevyřešená, ostatní čtení platí.")

    def _prepare(self, ctx: StepContext, page: dict, recognised: dict, layout: dict | None,
                 articles: list[dict] | None, spots: list[dict]) -> dict:
        """The request data and crops of one page, plus what checking and recording the answer needs."""
        index = page["index"]
        words = {word["id"]: word for line in recognised["lines"] for word in line["words"]}
        lines = {word["id"]: line for line in recognised["lines"] for word in line["words"]}
        positions = {word["id"]: (number, place) for number, line in enumerate(recognised["lines"])
                     for place, word in enumerate(line["words"])}
        breaks = {line["words"][-1]["id"]: following["words"][0]["id"] for line in recognised["lines"]
                  if line["words"] for following in [continuation(line, recognised["lines"])]
                  if following and following["words"]}
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
                box = union(words[word]["bbox"] for word in chosen)
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
                       "line": inputs[spot_id]["line"]} | ({"note": spot["note"]} if spot.get("note") else {})
                      for spot_id, spot in marked.items()]
        data = {"page": index + 1, "language": ctx.settings.get("language"), "paragraphs": paragraphs,
                "spots": data_spots}
        return {"data": data, "images": images, "spots": marked, "positions": positions, "breaks": breaks,
                "words": words,
                "lines": lines, "inputs": inputs, "ink": ink, "raster": raster}

    def _records(self, prepared: dict, answer, readings: list[dict], threshold: float, reason: str,
                 refused: set[str]) -> list[dict]:
        """One reconstruction record per valid reading, and one unresolved record per spot no valid reading
        covers (reason "rejected" for the spots of rejected readings)."""
        spots = prepared["spots"]
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
                why = "rejected" if spot_id in refused else reason
                records.append({**self._record(prepared, [spot_id], UNRESOLVED, why), **source})
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
                "words": [{"id": word_id, "text": rules.unresolved_text(spots[word_id], words[word_id]),
                           "bbox": words[word_id]["bbox"], "hint": words[word_id]["text"]} for word_id in ids]}

    @staticmethod
    def _placed(prepared: dict, ids: list[str], texts: list[str]) -> list[dict]:
        """The accepted words in their boxes (see `reconstruction.place`)."""
        return rules.place(prepared["ink"], prepared["raster"], prepared["lines"][ids[0]], prepared["words"], ids,
                           texts)

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
        pages = [{key: page[key] for key in ("index", "spots", "resolved", "markers", "kept", "publisher_errors",
                                             "rejected")} for page in summary_data["pages"]]
        errors, ignored = [], []
        for page in summary_data["pages"]:
            entry = result.read_json(page["file"])
            if entry.get("ignored"):
                ignored.append(page["index"] + 1)
            errors += [{"page": page["index"], "ids": record["ids"],
                        "printed": " ".join(record["candidate"]), "intended": record["intended"],
                        "reasoning": record["reasoning"]}
                       for record in entry["reconstructions"] if record["status"] == PUBLISHER_ERROR]
        total = summary_data["total"]
        kept = total["kept"]
        summary = [f"{total['spots']} problem spots: {total['resolved']} resolved, {total['markers']} illegible "
                   f"markers, {kept} doubtful reading{'' if kept == 1 else 's'} kept as read (listed for review), "
                   f"{total['publisher_errors']} publisher errors kept as printed (reconstructions accepted from "
                   f"confidence {summary_data['threshold']})."]
        rejected = [page for page in pages if page["rejected"]]
        if rejected:
            summary.append("Rejected answers on pages " + ", ".join(str(page["index"] + 1) for page in rejected)
                           + " (see the report JSON).")
        if ignored:
            summary.append("Rejected readings (their spots stay unresolved) on pages "
                           + ", ".join(str(number) for number in ignored) + " (see the page files).")
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
