"""Web lookup: the last automatic attempt at the spots AI reconstruction left unresolved (format in `ocrmaker.lookup`).

One request per page with unresolved spots, with web search, carrying only short snippets and the spots'
crops. Settled spots get a reconstruction record with `"source": "web"`; the others keep what AI
reconstruction wrote (the illegible marker, or a doubtful reading kept as read). Switched off in the settings, the step searches nothing. Without any AI provider it warns once; an
invalid answer is rejected for its page, with a warning; any other AI failure fails the step.
"""
from __future__ import annotations

import time

from .. import inkgaps, lookup as rules, reconstruction
from ..language import INVALID_OUTPUT, MISSING_CLI, UNAVAILABLE, LanguageError
from ..pages import PREPARED_STEP, read_pages
from ..reconstruction import ACCEPTED, RECONSTRUCT_STEP, UNRESOLVED
from ..step import ReportSection, Step, StepContext, StepResult
from ..transcription import MARKER
from ..words import OCR_STEP, page_file, read_page

THRESHOLD_SETTING = "reconstruction_accept_confidence"
# Web-settled words listed one by one in the human summary; the report JSON has them all.
REPORTED_WORDS = 40


def _page_list(numbers: list[int]) -> str:
    shown = ", ".join(str(number) for number in numbers[:10])
    return shown + (" …" if len(numbers) > 10 else "")


def _counts(entry: dict, unresolved: list[dict]) -> dict:
    settled = {tuple(item["ids"]) for item in entry["lookups"] if item["outcome"] == rules.SETTLED}
    left = [word["text"] for record in unresolved if tuple(record["ids"]) not in settled for word in record["words"]]
    return {"spots": sum(len(record["ids"]) for record in unresolved),
            "searched": sum(len(item["ids"]) for item in entry["lookups"]
                            if item["outcome"] not in (rules.NO_QUERY, rules.UNAVAILABLE, rules.DISABLED)),
            "settled": sum(len(ids) for ids in settled),
            "markers": sum(text == MARKER for text in left), "kept": sum(text != MARKER for text in left)}


def _unresolved(result: StepResult | None, index: int) -> list[dict]:
    if result is None or not result.exists(page_file(index)):
        return []
    return [record for record in result.read_json(page_file(index))["reconstructions"]
            if record["status"] == UNRESOLVED]


class LookupStep(Step):
    id = rules.LOOKUP_STEP

    def run(self, ctx: StepContext) -> None:
        pages = read_pages(ctx.result(PREPARED_STEP))
        reconstructed = ctx.result(RECONSTRUCT_STEP)
        enabled = bool(ctx.settings.get(rules.ENABLED_SETTING, True))
        threshold = float(ctx.settings.get(THRESHOLD_SETTING, reconstruction.DEFAULT_THRESHOLD))
        summaries, rejected, invalid = [], [], []
        unavailable: str | None = None
        for page in pages:
            ctx.checkpoint()
            index, number = page["index"], page["index"] + 1
            ctx.progress(index, len(pages), f"Stránka {number} z {len(pages)}")
            started = time.monotonic()
            unresolved = _unresolved(reconstructed, index)
            entry = {"index": index, "request": None, "rejected": None, "lookups": [], "reconstructions": []}
            if unresolved and not enabled:
                entry["lookups"] = [self._item(record, rules.DISABLED, []) for record in unresolved]
            elif unresolved:
                if unavailable is None:
                    try:
                        self._look_up(ctx, page, reconstructed, unresolved, threshold, entry)
                    except LanguageError as exc:
                        if exc.code in (UNAVAILABLE, MISSING_CLI):
                            unavailable = str(exc)
                        elif exc.code != INVALID_OUTPUT:
                            raise
                        else:
                            entry["rejected"] = f"{exc.code}: {exc}"
                if not entry["lookups"]:
                    outcome = rules.REJECTED if entry["rejected"] else rules.UNAVAILABLE
                    entry["lookups"] = [self._item(record, outcome, rules.queries(record)["queries"])
                                        for record in unresolved]
                if entry["rejected"]:
                    rejected.append(number)
                if any(item["outcome"] == rules.INVALID for item in entry["lookups"]):
                    invalid.append(number)
            ctx.output.write_json(page_file(index), entry)
            summary = {"index": index, "file": page_file(index), **_counts(entry, unresolved),
                       "rejected": entry["rejected"]}
            summaries.append(summary)
            ctx.record("lookup_page", **summary, provider=(entry["request"] or {}).get("provider"),
                       model=(entry["request"] or {}).get("model"), usage=(entry["request"] or {}).get("usage"),
                       seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        total = {key: sum(summary[key] for summary in summaries)
                 for key in ("spots", "searched", "settled", "markers", "kept")}
        ctx.output.write_json(rules.LOOKUP_FILE, {"enabled": enabled, "threshold": threshold, "pages": summaries,
                                                  "total": total})
        if unavailable is not None:
            ctx.warn(f"AI není k dispozici ({unavailable}), dohledání na webu proto neproběhlo a nevyřešená "
                     f"místa zůstala, jak jsou (značka {MARKER}, nebo pochybné čtení z OCR).")
        if rejected:
            where = "na straně" if len(rejected) == 1 else "na stranách"
            ctx.warn(f"AI vrátila neplatnou odpověď dohledání na webu {where} {_page_list(rejected)}; odpověď byla "
                     "odmítnuta a tamní místa zůstala nevyřešená.")
        if invalid:
            where = "na straně" if len(invalid) == 1 else "na stranách"
            ctx.warn(f"Některé výsledky dohledání na webu {where} {_page_list(invalid)} byly neplatné (například "
                     f"příliš dlouhý dotaz nebo chybějící zdroj) a nebyly přijaty.")

    def _look_up(self, ctx: StepContext, page: dict, reconstructed: StepResult, unresolved: list[dict],
                 threshold: float, entry: dict) -> None:
        """Ask once for the page's unresolved spots and fill in its lookups and web-settled records."""
        snippets = {tuple(record["ids"]): rules.queries(record) for record in unresolved}
        asked = [record for record in unresolved if snippets[tuple(record["ids"])]["queries"]]
        images, numbers = [], {}
        for record in asked:
            for name in record["input"]["images"]:
                if name not in numbers and reconstructed.exists(name):
                    images.append(reconstructed.path(name))
                    numbers[name] = len(images)
        answer = None
        if asked:
            spots = [{"ids": record["ids"], "kind": record["input"]["spots"][0]["kind"],
                      **snippets[tuple(record["ids"])],
                      "images": [numbers[name] for name in record["input"]["images"] if name in numbers]}
                     for record in asked]
            data = {"page": page["index"] + 1, "language": ctx.settings.get("language"), "spots": spots}
            answer = ctx.language.ask(rules.INSTRUCTIONS, data, schema=rules.SCHEMA, images=images,
                                      web_search=True, purpose=rules.PURPOSE)
            entry["request"] = {"provider": answer.provider, "model": answer.model, "usage": answer.usage}
            if not isinstance(answer.data, dict) or not isinstance(answer.data.get("lookups"), list):
                raise LanguageError("Odpověď AI nemá očekávaný tvar.", code=INVALID_OUTPUT)
        listed = {tuple(record["ids"]): record for record in asked}
        items, seen = {}, set()
        for raw in (answer.data["lookups"] if answer is not None else []):
            key, problems = rules.check(raw, listed, seen)
            if key is None:
                entry.setdefault("ignored", []).append({"item": raw, "problems": problems})
                continue
            seen.add(key)
            items[key] = (raw, problems)
        recognised, ink = None, None
        for record in unresolved:
            key = tuple(record["ids"])
            built = snippets[key]["queries"]
            if not built:
                entry["lookups"].append(self._item(record, rules.NO_QUERY, []))
                continue
            if key not in items:
                entry["lookups"].append(self._item(record, rules.NOT_FOUND, built))
                continue
            raw, problems = items[key]
            if problems:
                entry["lookups"].append(self._item(record, rules.INVALID, raw.get("queries") or built,
                                                   problems=problems))
                continue
            confidence = float(raw["confidence"])
            settled = confidence >= threshold and bool(raw["sources"])
            entry["lookups"].append(self._item(record, rules.SETTLED if settled else rules.INSUFFICIENT,
                                               raw["queries"], raw))
            if not settled:
                continue
            if recognised is None:
                recognised = read_page(ctx.result(OCR_STEP), page["index"]) or {"lines": []}
            words = {word["id"]: word for line in recognised["lines"] for word in line["words"]}
            lines = {word["id"]: line for line in recognised["lines"] for word in line["words"]}
            if ink is None and not (len(key) == 1 and len(raw["words"]) == 1):
                raster = page["raster"]
                ink = inkgaps.load_ink(ctx.result(raster["step"]).path(raster["file"]))
            hints = [spot["reading"] for spot in record["input"]["spots"]]
            entry["reconstructions"].append({
                "ids": record["ids"], "status": ACCEPTED, "reason": None, "source": rules.SOURCE,
                "input": record["input"], "candidate": list(raw["words"]), "confidence": confidence,
                "reasoning": raw["reasoning"], "error": "none" if raw["words"] == hints else "recognition",
                "intended": None, "provider": answer.provider, "model": answer.model,
                "queries": list(raw["queries"]), "answer": raw["answer"][:rules.MAX_ANSWER_CHARS],
                "sources": list(raw["sources"]),
                "words": reconstruction.place(ink, page["raster"], lines[record["ids"][0]], words, record["ids"],
                                              raw["words"])})

    @staticmethod
    def _item(record: dict, outcome: str, queries: list[str], raw: dict | None = None, *,
              problems: list[str] = ()) -> dict:
        raw = raw or {}
        return {"ids": record["ids"], "outcome": outcome, "queries": list(queries),
                "words": list(raw["words"]) if raw.get("words") else None,
                "confidence": float(raw["confidence"]) if "confidence" in raw else None,
                "answer": raw["answer"][:rules.MAX_ANSWER_CHARS] if raw.get("answer") else None,
                "sources": list(raw.get("sources") or []), "reasoning": raw.get("reasoning"),
                "problems": list(problems),
                # What AI reconstruction wrote for the spot, which stays unless it is settled: the illegible
                # marker, or a doubtful reading kept as read.
                "left": [{key: word.get(key) for key in ("id", "text", "bbox", "hint")} for word in record["words"]]}

    def overlay(self, result: StepResult, page: int) -> dict | None:
        """The words the web settled and the illegible markers left, each with its queries, answer and sources."""
        name = page_file(page)
        if not result.exists(name):
            return None
        entry = result.read_json(name)
        summary = result.read_json(rules.LOOKUP_FILE)
        counts = next((item for item in summary["pages"] if item["index"] == page), None) or {}
        settled = {tuple(record["ids"]): record for record in entry["reconstructions"]}
        shown = []
        for item in entry["lookups"]:
            record = settled.get(tuple(item["ids"]))
            hint = " ".join(spot["reading"] for spot in (record or {}).get("input", {}).get("spots", []))
            base = {"ids": item["ids"], "outcome": item["outcome"], "queries": item["queries"],
                    "answer": item["answer"], "sources": item["sources"], "confidence": item["confidence"],
                    "reasoning": item["reasoning"], "candidate": " ".join(item["words"]) if item["words"] else None}
            if record is not None:
                shown += [{**base, "id": word["id"], "bbox": word["bbox"], "text": word["text"], "status": "web",
                           "hint": hint, "provider": record["provider"], "model": record["model"]}
                          for word in record["words"]]
            else:
                shown += [{**base, "id": word["id"], "bbox": word["bbox"], "text": word["text"],
                           "status": "unresolved", "hint": word["hint"]} for word in item["left"]]
        return {"enabled": summary["enabled"], "words": shown,
                "counts": {key: counts.get(key, 0) for key in ("spots", "searched", "settled", "markers", "kept")},
                "rejected": entry["rejected"]}

    def report(self, result: StepResult) -> ReportSection:
        """Spots looked up, settled by the web and left as markers, per page and in total; the settled words."""
        summary_data = result.read_json(rules.LOOKUP_FILE)
        pages = [{key: page[key] for key in ("index", "spots", "searched", "settled", "markers", "kept", "rejected")}
                 for page in summary_data["pages"]]
        settled = []
        for page in summary_data["pages"]:
            entry = result.read_json(page["file"])
            settled += [{"page": page["index"], "ids": record["ids"],
                         "words": [word["text"] for word in record["words"]],
                         "hint": " ".join(spot["reading"] for spot in record["input"]["spots"]),
                         "confidence": record["confidence"], "queries": record["queries"],
                         "answer": record["answer"], "sources": record["sources"]}
                        for record in entry["reconstructions"]]
        total = summary_data["total"]
        left = (f"{total['markers']} illegible markers and {total['kept']} doubtful "
                f"reading{'' if total['kept'] == 1 else 's'} kept as read")
        if not summary_data["enabled"]:
            summary = [f"Web lookup is switched off in the project settings: {total['spots']} unresolved spots "
                       f"left {left}."]
        else:
            summary = [f"{total['spots']} unresolved spots, {total['searched']} looked up on the web: "
                       f"{total['settled']} settled by a web source, {left} remain "
                       f"(accepted from confidence {summary_data['threshold']})."]
        rejected = [page for page in pages if page["rejected"]]
        if rejected:
            summary.append("Rejected answers on pages " + ", ".join(str(page["index"] + 1) for page in rejected)
                           + " (see the report JSON).")
        if settled:
            summary += ["", "Settled by the web:", ""]
            summary += [f"- page {item['page'] + 1}, {', '.join(item['ids'])}: {' '.join(item['words'])!r} "
                        f"(read {item['hint']!r}; {', '.join(item['sources'])})" for item in settled[:REPORTED_WORDS]]
            if len(settled) > REPORTED_WORDS:
                summary.append(f"- and {len(settled) - REPORTED_WORDS} more (see the report JSON)")
        return ReportSection("Web lookup", {"enabled": summary_data["enabled"],
                                            "threshold": summary_data["threshold"], "total": total,
                                            "pages": pages, "settled": settled}, summary)


STEP = LookupStep()
