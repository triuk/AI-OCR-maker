"""Export: writes the OCR PDF from the source PDF with the recognised words as an invisible text layer,
page by page in the reading order (ADR 0005).

Its result holds the output files under `outputs/` (named after the source PDF) and `textlayer.json`, the
plan of what was written: per page, the lines in written order with each word's id, text and box. Export
verification and the step preview read that plan.

Export verification (`ocrmaker.verify`) reads the written OCR PDF back and compares it with the plan. When it
finds anything, the OCR PDF is assembled once more with the alternative text strategy
(`textlayer.STRATEGIES`) and the attempt with fewer findings is kept. Remaining findings end the step as done
with a warning; the files are written either way. `verification.json` holds
{"strategy" (the one kept), "attempts": [{"strategy", "findings" (count)}], "findings": [...]}.

Export also writes the article text Markdown next to the OCR PDF (format in `ocrmaker.articletext`), and
`articletext.json` with its statistics: articles, ads, captions, printed page numbers, hyphen decisions and
the front matter.

Word text: both outputs take every word from `ExportStep.exported_words`, the one place where Export decides
what a word says. Reconstruction records (AI reconstruction, Web lookup; `ocrmaker.transcription`) and manual
corrections (`ocrmaker.corrections`, ADR 0004; handed over in manual review's result) are applied there.
Article order: both outputs take a page's articles from `reading_articles`, which applies a manual article
order.
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import articletext, corrections, hunspell, textlayer, transcription, verify
from ..layout import LAYOUT_STEP, read_layout_page
from ..pages import PREPARED_STEP, read_pages
from ..reading_order import ORDER_FILE, ORDER_STEP
from ..step import ReportSection, Step, StepContext, StepResult
from ..transcription import expand
from ..words import OCR_STEP, read_page

OUTPUTS = "outputs"
PLAN_FILE = "textlayer.json"
VERIFICATION_FILE = "verification.json"
ARTICLE_TEXT_FILE = "articletext.json"
# Findings kept in one diagnostic record; the result keeps them all.
RECORDED_FINDINGS = 200


def output_names(source_name: str) -> dict[str, str]:
    """Output file names derived from the source PDF's file name."""
    stem = Path(source_name).stem or "dokument"
    return {"pdf": f"{stem}-ocr.pdf", "markdown": f"{stem}-ocr.md"}




def manual_corrections(ctx) -> list[dict]:
    """The manual corrections manual review handed to this export (its result), or none without one."""
    if not ctx.has_result(corrections.REVIEW_STEP):
        return []
    result = ctx.result(corrections.REVIEW_STEP)
    return result.read_json(corrections.CORRECTIONS_FILE) if result.exists(corrections.CORRECTIONS_FILE) else []


def reading_order(ctx) -> tuple[dict[int, dict], list[dict]]:
    """Reading order's pages by index and its cross-page links; none without Layout and Reading order."""
    if not (ctx.has_result(LAYOUT_STEP) and ctx.has_result(ORDER_STEP)):
        return {}, []
    order = ctx.result(ORDER_STEP).read_json(ORDER_FILE)
    return {item["index"]: item for item in order.get("pages", [])}, order.get("links", [])


def reading_articles(ctx, recognised: dict, order_pages: dict[int, dict], manual: list[dict] = ()) -> list[dict]:
    """A page's articles in reading order, as OCR word ids: [{"id", "kind", "continues", "blocks": [{"id",
    "kind", "lines": [[word id, ...], ...]}]}].

    Without a reading order the page is one article of one block holding Tesseract's lines. Words no block
    holds (there should be none) are not lost: they end the page as one more article of one line. A manual
    article order among the manual corrections given (`corrections.ordered`) puts the articles in its order.
    """
    return corrections.ordered(_automatic_articles(ctx, recognised, order_pages), manual, recognised["index"])


def _automatic_articles(ctx, recognised: dict, order_pages: dict[int, dict]) -> list[dict]:
    index = recognised["index"]
    if index not in order_pages:
        return [{"id": f"p{index + 1}-text", "kind": "article", "continues": None, "blocks": [
            {"id": f"p{index + 1}-text", "kind": "body",
             "lines": [[word["id"] for word in line["words"]] for line in recognised["lines"]]}]}]
    layout = read_layout_page(ctx.result(LAYOUT_STEP), index) or {"blocks": []}
    blocks = {block["id"]: block for block in layout["blocks"]}
    articles = [{**article, "blocks": [{"id": block, "kind": blocks[block]["kind"], "lines": blocks[block]["lines"]}
                                       for block in article["blocks"]]} for article in order_pages[index]["articles"]]
    placed = {word for block in layout["blocks"] for line in block["lines"] for word in line}
    rest = [word["id"] for line in recognised["lines"] for word in line["words"] if word["id"] not in placed]
    if rest:
        articles.append({"id": f"p{index + 1}-rest", "kind": "article", "continues": None,
                         "blocks": [{"id": f"p{index + 1}-rest", "kind": "body", "lines": [rest]}]})
    return articles


class ExportStep(Step):
    id = "export"

    def exported_words(self, ctx: StepContext, recognised: dict) -> dict[str, list[dict]]:
        """The words of one page as every output writes them: {OCR word id: [{"id", "text", "bbox"}, ...]}.

        This is the only place where Export takes a word's text, for the OCR PDF and the article text alike.
        Each OCR word id maps to the words written in its place, in recognition order: the word itself with
        Local OCR's text, or what the reconstruction records settled (`ocrmaker.transcription`): the
        reconstructed text, the illegible marker, no word for a box merged into its neighbour, several words
        for a split box. Manual corrections (`ocrmaker.corrections`, ADR 0004), as manual review handed them
        over in its result, are applied last. Expand a line of word ids with `transcription.expand(words, ids)`.
        """
        return corrections.apply(transcription.page_words(ctx, recognised), recognised, manual_corrections(ctx))

    def text_layer_plan(self, ctx: StepContext, pages: list[dict]) -> list[dict]:
        """Per page, the lines of words in the order they are written: the reading order (articles, their
        blocks, the blocks' lines), or Tesseract's recognition order when there is no reading order."""
        ocr = ctx.result(OCR_STEP)
        order_pages, _ = reading_order(ctx)
        plan = []
        for page in pages:
            recognised = read_page(ocr, page["index"]) or {"index": page["index"], "lines": []}
            words = self.exported_words(ctx, recognised)
            lines = [expand(words, line) for article in reading_articles(ctx, recognised, order_pages, manual_corrections(ctx))
                     for block in article["blocks"] for line in block["lines"]]
            if page["index"] in order_pages:
                lines = [line for line in lines if line]
            plan.append({"index": page["index"], "lines": lines})
        return plan

    def article_pages(self, ctx: StepContext, pages: list[dict]) -> tuple[list[dict], list[dict]]:
        """The pages' articles with their blocks' words (see articletext.render), and the cross-page links.

        Without a reading order every page is one article of one block holding Tesseract's lines.
        """
        ocr = ctx.result(OCR_STEP)
        order_pages, links = reading_order(ctx)
        result = []
        for page in pages:
            index = page["index"]
            recognised = read_page(ocr, index) or {"index": index, "lines": []}
            words = self.exported_words(ctx, recognised)
            articles = [{**article, "blocks": [{**block, "lines": [expand(words, line) for line in block["lines"]]}
                                               for block in article["blocks"]]}
                        for article in reading_articles(ctx, recognised, order_pages, manual_corrections(ctx))]
            # The words no block holds may all write nothing; then there is no article for them.
            articles = [article for article in articles if article["id"] != f"p{index + 1}-rest"
                        or any(line for block in article["blocks"] for line in block["lines"])]
            result.append({"index": index, "articles": articles})
        return result, links

    def dictionary(self, ctx: StepContext):
        """The dictionary deciding unclear line-end hyphens in the article text: known(word) -> bool.

        Hunspell for the document language plus the project dictionary, as in Problem detection. Without a
        hunspell dictionary only the project dictionary, the document's own words and the typographic rules
        decide (articletext.join_hyphenated).
        """
        project = {word.casefold() for word in ctx.settings.get("project_dictionary") or []}
        try:
            speller = hunspell.speller(ctx.settings["language"])
        except hunspell.DictionaryError as exc:  # a missing dictionary must not stop the export
            ctx.record("article_text_dictionary", error=str(exc))
            return lambda word: word.casefold() in project
        return lambda word: word.casefold() in project or bool(speller.lookup(word))

    def write_article_text(self, ctx: StepContext, pages: list[dict], name: str) -> dict:
        """Write the article text Markdown into the outputs; returns its statistics."""
        article_pages, links = self.article_pages(ctx, pages)
        text, stats = articletext.render(ctx.source_name, article_pages, links, self.dictionary(ctx))
        ctx.output.write_bytes(f"{OUTPUTS}/{name}", text.encode("utf-8"))
        stats = {"output": name, **stats}
        ctx.output.write_json(ARTICLE_TEXT_FILE, stats)
        return stats

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
        ctx.checkpoint()
        markdown = output_names(ctx.source_name)["markdown"]
        stats = self.write_article_text(ctx, pages, markdown)
        ctx.record("article_text", **stats)
        ctx.record("export", outputs=[name, markdown], pages=len(plan),
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
        data = {"outputs": outputs, "strategy": verification["strategy"], "attempts": verification["attempts"],
                "counts": counts, "findings": findings}
        if result.exists(ARTICLE_TEXT_FILE):
            data["article_text"] = stats = result.read_json(ARTICLE_TEXT_FILE)
            summary += ["", _article_text_summary(stats)]
        return ReportSection("Export", data, summary)


def _article_text_summary(stats: dict) -> str:
    def counted(count: int, noun: str) -> str:
        return f"{count} {noun}{'' if count == 1 else 's'}"
    printed = stats["printed_pages"]
    found = sum(number is not None for number in printed)
    return (f"Article text {stats.get('output', 'Markdown')}: {counted(stats['articles'], 'article')}, "
            f"{counted(stats['ads'], 'ad')}, {counted(stats['captions'], 'caption')}; printed page numbers found on "
            f"{found} of {len(printed)} pages; line-end hyphens: {stats['hyphens']['joined']} joined, "
            f"{stats['hyphens']['kept']} kept as genuine.")


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
