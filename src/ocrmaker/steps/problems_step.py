"""Problem detection: flags unreadable words and doubtful readings with local signals (ocrmaker.problems),
then lets the AI read each page's text through and flags the words that do not fit (ocrmaker.readthrough).

Without an AI provider the read-through is skipped (AI reconstruction warns about the missing AI where it
matters, at the spots it cannot resolve); an invalid answer leaves the page with
its local spots and a warning; any other AI failure (usage limit, login, timeout) fails the step.

Its result (`steps/problems/`) holds `problems.json`:

    {"thresholds": {"problem_unreadable_confidence": 40.0, "problem_doubtful_confidence": 70.0},
     "dictionary": {"language": "ces", "hunspell": "cs_CZ (LibreOffice 6aaf119ded9a)", "project_words": 3},
                   # null when the hunspell dictionary was unavailable and the check was skipped
     "pages": [{"index": 0, "file": "pages/page-0001.json", "unreadable": 4, "doubtful": 2}],
     "total": {"unreadable": 4, "doubtful": 2}}

and one page file per page, `pages/page-0001.json`, listing its problem spots in the OCR page file's line
and word order:

    {"index": 0,
     "spots": [{"id": "p1-e1",                # the word id in the Local OCR result (ocrmaker.words)
                "kind": "unreadable",          # "unreadable" (unreadable word) or "doubtful" (doubtful reading)
                "signals": ["empty"],          # why it was flagged, see ocrmaker.problems
                "text": "",                    # the local reading: "" for an empty word box, else a hint
                "confidence": 0.0,             # Tesseract's word confidence, 0-100
                "bbox": [x0, y0, x1, y1],      # the word box, source page coordinates
                "line": "p1-l1",               # the id of the word's line
                "note": "..."}],               # signal "context" only: the read-through's reason
     "readthrough": {"request": {"provider", "model", "usage"} | null,   # null: not asked
                     "rejected": null,         # why the whole answer was rejected (English), else null
                     "ignored": [{"item": {...}, "problems": ["..."]}]}} # named words not found on their line

Words that are not listed are clearly recognised and must not be changed by later steps (ADR 0001).
"""
from __future__ import annotations

import time
from collections import Counter

from .. import hunspell, readthrough
from ..language import PageRequests, request_fields
from ..layout import LAYOUT_STEP, read_layout_page
from ..pages import PREPARED_STEP, read_pages
from ..problems import DOUBTFUL, KINDS, THRESHOLDS, UNREADABLE, detect
from ..reading_order import ORDER_FILE, ORDER_STEP
from ..step import ReportSection, Step, StepContext, StepResult
from ..wording import on_pages
from ..words import OCR_STEP, page_file, read_page

PROBLEMS_FILE = "problems.json"


def _project_known(words) -> set[str]:
    return {word.casefold() for word in words}


class ProblemsStep(Step):
    id = "problems"

    def run(self, ctx: StepContext) -> None:
        pages = read_pages(ctx.result(PREPARED_STEP))
        ocr = ctx.result(OCR_STEP)
        settings = ctx.settings
        thresholds = {key: float(settings.get(key, default)) for key, default in THRESHOLDS.items()}
        language = settings["language"]
        project_words = settings.get("project_dictionary") or []
        project = _project_known(project_words)
        dictionary = None
        try:
            speller = hunspell.speller(language)
        except hunspell.DictionaryError as exc:
            speller = None
            ctx.warn(f"{exc} Kontrola slovníkem byla vynechána.")
        if speller is not None:
            def known(word: str) -> bool:
                return word.casefold() in project or speller.lookup(word)

            path = hunspell.DICTIONARIES[language][0]
            dictionary = {"language": language,
                          "hunspell": f"{path.rsplit('/', 1)[-1]} (LibreOffice {hunspell.COMMIT[:12]})",
                          "project_words": len(project_words)}
        else:
            known = None
        layout_result = ctx.result(LAYOUT_STEP) if ctx.has_result(LAYOUT_STEP) else None
        order = ctx.result(ORDER_STEP).read_json(ORDER_FILE) if ctx.has_result(ORDER_STEP) else {"pages": []}
        order_pages = {item["index"]: item for item in order["pages"]}
        ai = PageRequests()
        summaries, rejected = [], []
        total = Counter()
        for page in pages:
            ctx.checkpoint()
            ctx.progress(page["index"], len(pages), f"Stránka {page['index'] + 1} z {len(pages)}")
            started = time.monotonic()
            recognised = read_page(ocr, page["index"]) or {"index": page["index"], "lines": []}
            spots = detect(recognised, language=language,
                           unreadable_below=thresholds["problem_unreadable_confidence"],
                           doubtful_below=thresholds["problem_doubtful_confidence"], known=known)
            layout = read_layout_page(layout_result, page["index"]) if layout_result else None
            articles = (order_pages.get(page["index"]) or {}).get("articles")
            spots, readthrough = self._read_through(ctx, ai, recognised, layout, articles, spots)
            if readthrough["rejected"]:
                rejected.append(page["index"] + 1)
            ctx.output.write_json(page_file(page["index"]), {"index": page["index"], "spots": spots,
                                                             "readthrough": readthrough})
            counts = Counter(spot["kind"] for spot in spots)
            total.update(counts)
            summary = {"index": page["index"], "file": page_file(page["index"]),
                       UNREADABLE: counts[UNREADABLE], DOUBTFUL: counts[DOUBTFUL]}
            summaries.append(summary)
            ctx.record("problems_page", **summary,
                       signals=dict(Counter(signal for spot in spots for signal in spot["signals"])),
                       **request_fields(readthrough["request"]), seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        ctx.output.write_json(PROBLEMS_FILE, {"thresholds": thresholds, "dictionary": dictionary,
                                              "pages": summaries, "total": {kind: total[kind] for kind in KINDS}})
        if rejected:
            ctx.warn(f"AI vrátila neplatnou kontrolu smyslu textu {on_pages(rejected)} (odpověď v chybném tvaru); "
                     "odpověď byla odmítnuta a tamní problémová místa určily jen místní signály.")

    @staticmethod
    def _read_through(ctx: StepContext, ai: PageRequests, recognised: dict, layout: dict | None,
                      articles: list[dict] | None, spots: list[dict]) -> tuple[list[dict], dict]:
        """The page's spots with the words the AI read-through named, and what the read-through recorded."""
        entry = {"request": None, "rejected": None, "ignored": []}
        shown, numbered = readthrough.page_text(recognised, layout, articles)
        if not any(word["text"] for line in numbered for word in line):
            return spots, entry
        data = {"language": ctx.settings.get("language"), "articles": shown}
        answer, entry["rejected"] = ai.ask(lambda: ctx.language.ask(
            readthrough.INSTRUCTIONS, data, schema=readthrough.SCHEMA, purpose=readthrough.PURPOSE))
        if answer is None:
            return spots, entry
        entry["request"] = answer.request
        try:
            named, entry["ignored"] = readthrough.nominations(answer.data, numbered)
        except ValueError as exc:
            entry["rejected"] = str(exc)
            return spots, entry
        return readthrough.add_spots(spots, named, recognised), entry

    def overlay(self, result: StepResult, page: int) -> dict | None:
        """The problem spots of one page with their kind, signals, text and confidence."""
        name = page_file(page)
        if not result.exists(name):
            return None
        spots = result.read_json(name)["spots"]
        return {"spots": spots, "counts": {kind: sum(1 for spot in spots if spot["kind"] == kind) for kind in KINDS}}

    def report(self, result: StepResult) -> ReportSection:
        """Problem spot counts by kind per page and in total, with the thresholds and dictionary used."""
        problems = result.read_json(PROBLEMS_FILE)
        pages = [{key: page[key] for key in ("index", UNREADABLE, DOUBTFUL)} for page in problems["pages"]]
        total = problems["total"]
        dictionary = problems["dictionary"]
        thresholds = problems["thresholds"]
        summary = [f"{total[UNREADABLE]} unreadable words and {total[DOUBTFUL]} doubtful readings; confidence "
                   f"below {thresholds['problem_unreadable_confidence']} is unreadable, below "
                   f"{thresholds['problem_doubtful_confidence']} doubtful; dictionary "
                   + (f"{dictionary['hunspell']} plus {dictionary['project_words']} project words."
                      if dictionary else "check skipped."), "",
                   "| Page | Unreadable words | Doubtful readings |", "|---|---|---|"]
        summary += [f"| {page['index'] + 1} | {page[UNREADABLE]} | {page[DOUBTFUL]} |" for page in pages
                    if page[UNREADABLE] or page[DOUBTFUL]][:REPORTED_PAGES]
        return ReportSection("Problem detection", {"thresholds": thresholds, "dictionary": dictionary,
                                                   "total": total, "pages": pages}, summary)


# The summary lists at most this many pages with problem spots; the report JSON has them all.
REPORTED_PAGES = 40

STEP = ProblemsStep()
