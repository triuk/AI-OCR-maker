"""Export: writes the OCR PDF from the source PDF with the recognised words as an invisible text layer.

Its result holds the output files under `outputs/` (named after the source PDF) and `textlayer.json`, the
plan of what was written: per page, the lines in written order with each word's id, text and box. Export
verification and the step preview read that plan.

Export verification (`ocrmaker.verify`) reads the written OCR PDF back and compares it with the plan. When it
finds anything, the OCR PDF is assembled once more with the alternative text strategy
(`textlayer.STRATEGIES`) and the attempt with fewer findings is kept. Remaining findings end the step as done
with a warning; the files are written either way. `verification.json` holds
{"strategy" (the one kept), "attempts": [{"strategy", "findings" (count)}], "findings": [...]}.
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import textlayer, verify
from ..pages import PREPARED_STEP, read_pages
from ..step import ReportSection, Step, StepContext, StepResult
from ..words import OCR_STEP, read_page

OUTPUTS = "outputs"
PLAN_FILE = "textlayer.json"
VERIFICATION_FILE = "verification.json"
# Findings kept in one diagnostic record; the result keeps them all.
RECORDED_FINDINGS = 200


def output_names(source_name: str) -> dict[str, str]:
    """Output file names derived from the source PDF's file name."""
    stem = Path(source_name).stem or "dokument"
    return {"pdf": f"{stem}-ocr.pdf"}


class ExportStep(Step):
    id = "export"

    def text_layer_plan(self, ctx: StepContext, pages: list[dict]) -> list[dict]:
        """Per page, the lines of words in the order they are written; for now Tesseract's (geometric) order."""
        ocr = ctx.result(OCR_STEP)
        plan = []
        for page in pages:
            recognised = read_page(ocr, page["index"]) or {"lines": []}
            plan.append({"index": page["index"], "lines": [
                [{key: word[key] for key in ("id", "text", "bbox")} for word in line["words"]]
                for line in recognised["lines"]]})
        return plan

    def assemble(self, source: Path, target: Path, pages: list[dict], strategy: str) -> None:
        """Write the OCR PDF with one text strategy (see textlayer.write_ocr_pdf for pages)."""
        textlayer.write_ocr_pdf(source, target, pages, strategy=strategy)

    def run(self, ctx: StepContext) -> None:
        started = time.monotonic()
        pages = read_pages(ctx.result(PREPARED_STEP))
        plan = self.text_layer_plan(ctx, pages)
        ctx.checkpoint()
        name = output_names(ctx.source_name)["pdf"]
        layer = [{"page": page, "lines": item["lines"]} for page, item in zip(pages, plan)]
        verification = self._write_verified(ctx, ctx.output.path(f"{OUTPUTS}/{name}"), layer)
        ctx.output.write_json(PLAN_FILE, {"pages": plan})
        ctx.output.write_json(VERIFICATION_FILE, verification)
        ctx.record("export", outputs=[name], pages=len(plan),
                   words=sum(len(line) for item in plan for line in item["lines"]),
                   strategy=verification["strategy"], findings=len(verification["findings"]),
                   seconds=round(time.monotonic() - started, 3))
        if verification["findings"]:
            ctx.warn(_warning(verification["findings"]))

    def _write_verified(self, ctx: StepContext, target: Path, layer: list[dict]) -> dict:
        """Assemble and verify the OCR PDF, retrying once with the alternative strategy; keep the better one."""
        attempts, best = [], None
        total = len(textlayer.STRATEGIES)
        for number, strategy in enumerate(textlayer.STRATEGIES):
            ctx.checkpoint()
            ctx.progress(number, total, "Zápis a kontrola OCR PDF" if not number
                         else "Druhý pokus: jiné kódování textu")
            clock = time.monotonic()
            attempt = target.with_name(f".{strategy}-{target.name}")
            self.assemble(ctx.source_pdf, attempt, layer, strategy)
            findings = verify.verify_ocr_pdf(attempt, layer)
            ctx.record("export_verification", strategy=strategy, findings=findings[:RECORDED_FINDINGS],
                       counts=_counts(findings), seconds=round(time.monotonic() - clock, 3))
            attempts.append({"strategy": strategy, "findings": len(findings)})
            if best is None or len(findings) < len(best[2]):
                if best:
                    best[1].unlink()
                best = (strategy, attempt, findings)
            else:
                attempt.unlink()
            if not findings:
                break
        ctx.progress(total, total, "Zápis a kontrola OCR PDF")
        strategy, attempt, findings = best
        attempt.replace(target)
        return {"strategy": strategy, "attempts": attempts, "findings": findings}

    def overlay(self, result: StepResult, page: int) -> dict | None:
        """The written text layer of one page: word boxes with their text, in written order.

        A word with verification findings carries them as "findings": [{"kind", "extracted"}]; text that
        came out of the page without being planned is listed under "extra" with its box.
        """
        pages = result.read_json(PLAN_FILE)["pages"]
        item = next((item for item in pages if item["index"] == page), None)
        if item is None:
            return None
        findings = [finding for finding in self._findings(result) if finding["page"] == page]
        by_word: dict[str, list[dict]] = {}
        for finding in findings:
            if finding["word"] is not None:
                by_word.setdefault(finding["word"], []).append(
                    {"kind": finding["kind"], "extracted": finding["extracted"]})
        words = [word | ({"findings": by_word[word["id"]]} if word["id"] in by_word else {})
                 for line in item["lines"] for word in line]
        extra = [{"text": finding["extracted"], "bbox": finding["bbox"]}
                 for finding in findings if finding["word"] is None]
        return {"words": words, "extra": extra}

    @staticmethod
    def _findings(result: StepResult) -> list[dict]:
        return result.read_json(VERIFICATION_FILE)["findings"] if result.exists(VERIFICATION_FILE) else []

    def report(self, result: StepResult) -> ReportSection:
        """The files written and what export verification found: the attempts and every remaining finding."""
        outputs = sorted(path.name for path in result.path(OUTPUTS).iterdir() if path.is_file()) \
            if result.path(OUTPUTS).is_dir() else []
        verification = result.read_json(VERIFICATION_FILE)
        findings = verification["findings"]
        counts = _counts(findings)
        summary = [f"Wrote {', '.join(outputs) or 'nothing'}."]
        attempts = ", ".join(f"{attempt['strategy']} {attempt['findings']}" for attempt in verification["attempts"])
        if not findings:
            summary.append(f"Verification found nothing (text strategy {verification['strategy']}; "
                           f"findings per attempt: {attempts}).")
        else:
            summary += [f"Verification left {len(findings)} findings ("
                        + ", ".join(f"{kind} {count}" for kind, count in counts.items())
                        + f"), text strategy {verification['strategy']}; findings per attempt: {attempts}.", ""]
            summary += [f"- page {finding['page'] + 1}, word {finding['word'] or '-'}: {finding['kind']}, "
                        f"planned {finding['text']!r}, extracted {finding['extracted']!r}"
                        for finding in findings[:REPORTED_FINDINGS]]
            if len(findings) > REPORTED_FINDINGS:
                summary.append(f"- and {len(findings) - REPORTED_FINDINGS} more (see the report JSON)")
        return ReportSection("Export", {"outputs": outputs, "strategy": verification["strategy"],
                                        "attempts": verification["attempts"], "counts": counts,
                                        "findings": findings}, summary)


# Findings listed one by one in the human summary; the report JSON has them all.
REPORTED_FINDINGS = 20


def _counts(findings: list[dict]) -> dict[str, int]:
    return {kind: count for kind in verify.KINDS if (count := sum(f["kind"] == kind for f in findings))}


# Czech names of the finding kinds in the step's warning.
KIND_LABELS = {"order": "pořadí", "outside_box": "mimo rámeček slova", "text": "nepřesný text",
               "missing": "chybějící slovo", "extra": "text navíc"}


def _warning(findings: list[dict]) -> str:
    count = len(findings)
    noun = "nález" if count == 1 else "nálezy" if 2 <= count <= 4 else "nálezů"
    kinds = ", ".join(f"{KIND_LABELS[kind]} {number}" for kind, number in _counts(findings).items())
    return (f"Kontrola textové vrstvy OCR PDF: {count} {noun} ({kinds}). Soubory jsou přesto zapsány; "
            "dotčená slova zvýrazňuje náhled exportu a vypisuje je zpráva o běhu.")


STEP = ExportStep()
