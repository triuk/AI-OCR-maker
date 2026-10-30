"""Problem detection: flags unreadable words and doubtful readings with local signals only (ocrmaker.problems).

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
                "line": "p1-l1"}]}             # the id of the word's line

Words that are not listed are clearly recognised and must not be changed by later steps (ADR 0001).
"""
from __future__ import annotations

import time
from collections import Counter

from .. import hunspell
from ..pages import PREPARED_STEP, read_pages
from ..problems import DOUBTFUL, KINDS, THRESHOLDS, UNREADABLE, detect
from ..step import Step, StepContext, StepResult
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
        summaries = []
        total = Counter()
        for page in pages:
            ctx.checkpoint()
            ctx.progress(page["index"], len(pages), f"Stránka {page['index'] + 1} z {len(pages)}")
            started = time.monotonic()
            recognised = read_page(ocr, page["index"]) or {"index": page["index"], "lines": []}
            spots = detect(recognised, language=language,
                           unreadable_below=thresholds["problem_unreadable_confidence"],
                           doubtful_below=thresholds["problem_doubtful_confidence"], known=known)
            ctx.output.write_json(page_file(page["index"]), {"index": page["index"], "spots": spots})
            counts = Counter(spot["kind"] for spot in spots)
            total.update(counts)
            summary = {"index": page["index"], "file": page_file(page["index"]),
                       UNREADABLE: counts[UNREADABLE], DOUBTFUL: counts[DOUBTFUL]}
            summaries.append(summary)
            ctx.record("problems_page", **summary,
                       signals=dict(Counter(signal for spot in spots for signal in spot["signals"])),
                       seconds=round(time.monotonic() - started, 3))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        ctx.output.write_json(PROBLEMS_FILE, {"thresholds": thresholds, "dictionary": dictionary,
                                              "pages": summaries, "total": {kind: total[kind] for kind in KINDS}})

    def overlay(self, result: StepResult, page: int) -> dict | None:
        """The problem spots of one page with their kind, signals, text and confidence."""
        name = page_file(page)
        if not result.exists(name):
            return None
        spots = result.read_json(name)["spots"]
        return {"spots": spots, "counts": {kind: sum(1 for spot in spots if spot["kind"] == kind) for kind in KINDS}}


STEP = ProblemsStep()
