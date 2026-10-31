## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues of `triuk/AI-OCR-maker` (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Development

- Setup (no sudo, idempotent, shares uv, Python and package caches between checkouts): `./setup.sh`
- Tests: `.venv/bin/python -m pytest -q`
- Start: `./start.sh` (`--server` skips the browser, `--data-dir DIR` uses another library, `--port N`)
- Dependencies are pinned in `uv.lock` and Python in `.python-version`; after editing `pyproject.toml` run
  `~/.local/share/ai-ocr-maker/tools/uv-<version>/uv lock`, then `./setup.sh`.
- A workflow step is implemented by adding `src/ocrmaker/steps/<step id>_step.py` with a module-level `STEP`
  (see `src/ocrmaker/steps/__init__.py` and `src/ocrmaker/step.py`).
- Page geometry, source page coordinates and working rasters are defined in `src/ocrmaker/pages.py`; the Import
  result is their authority. A step preview is `src/ocrmaker/ui/previews/<step id>.js` (loaded on demand) and
  normally draws its overlay through `ocrmaker.pageViewer` from `src/ocrmaker/ui/viewer.js`.
- Recognised words (Local OCR's result: lines, word boxes, char boxes, confidence) are defined in
  `src/ocrmaker/words.py`; the prepared pages with their page transform in `src/ocrmaker/pages.py`. The OCR PDF
  is written by `src/ocrmaker/textlayer.py` from a per-page plan of lines of words, which the Export step builds.
- Blocks (Layout's result, word ids per line) are defined in `src/ocrmaker/layout.py`; articles in reading order,
  their kinds and cross-page links (Reading order's result) in `src/ocrmaker/reading_order.py`. Export writes the
  text layer article by article, block by block. Seam 1 tests print laid-out pages with `tests/layout_pdfs.py`.
- AI requests go through `ctx.language.ask(...)` (`src/ocrmaker/language.py`); the CLIs are found on PATH or through
  `OCRMAKER_CLAUDE` / `OCRMAKER_CODEX`. Seam 1 tests script the AI with `tests/fakes.py::ScriptedProvider`
  (the `language_provider` fixture).
- Every workflow operation writes a run report (`src/ocrmaker/report.py`): `<project>/reports/<run id>.json` plus a
  Markdown summary. A step adds its metrics by overriding `Step.report(result)` and returning a `ReportSection`
  (`src/ocrmaker/step.py`); see the `report.py` docstring for the format. AI requests are summarised from diagnostics.
- Export verifies the OCR PDF it wrote (`src/ocrmaker/verify.py`) and retries once with the alternative text
  strategy (`textlayer.STRATEGIES`); tests break the assembly with `tests/fakes.py::BrokenExport`.
- Optional slow smoke test on a real scan: `OCRMAKER_SMOKE_PDF=/path/scan.pdf .venv/bin/python -m pytest -q -m slow -s`.
- Steps read the PDF through `ctx.source_pdf`: the clean working copy once an earlier OCR's invisible text layer
  was removed (`src/ocrmaker/existing_text.py`, `Workflow.remove_text_layer`), otherwise the source PDF copy.
- Export also writes the article text Markdown (`<stem>-ocr.md`; format and hyphenation rules in
  `src/ocrmaker/articletext.py`). Both outputs take every word's text from `ExportStep.exported_words`, the one
  place where AI reconstructions and manual corrections must be applied.
- Problem spots (Problem detection's result, read by AI reconstruction) are defined in
  `src/ocrmaker/steps/problems_step.py`, their signals and kinds in `src/ocrmaker/problems.py`. Empty word boxes for
  ink Tesseract missed are added by `src/ocrmaker/inkgaps.py` inside Local OCR. The hunspell dictionaries are pinned
  in `src/ocrmaker/hunspell.py` (setup installs `ces`, others download on demand; `OCRMAKER_HUNSPELL` overrides the cache).
- AI reconstruction (`src/ocrmaker/steps/reconstruct_step.py`, request/answer/result format in
  `src/ocrmaker/reconstruction.py`) writes reconstruction records per page; `src/ocrmaker/transcription.py` applies
  them (and Web lookup's, in the same format) to the words `ExportStep.exported_words` returns. Seam 1 tests read
  printed ground truth through `tests/test_reconstruction.py::PrintedOcr` and script answers with `fakes.answer_spots`.
- Web lookup (`src/ocrmaker/steps/lookup_step.py`, queries/answer/result format in `src/ocrmaker/lookup.py`) searches
  only for AI reconstruction's unresolved records and writes records with `"source": "web"` (plus `queries`,
  `answer`, `sources`) only for the spots it settles; the setting `web_lookup` switches it off.
- Manual review (`src/ocrmaker/steps/review_step.py`) is the manual step: Run stops before it, Export runs it and
  then Export. The review service (`src/ocrmaker/review.py`: queue, page text, crops) backs `Workflow.review_queue`,
  `review_page`, `review_crop`, `correct` and `revert_correction`, plus `suggestions`/`accept_suggestion`
  (propagation), `add_note`/`edit_note` (region notes) and `reorder_articles`; manual corrections (format and kinds
  `text`, `note`, `order` in `src/ocrmaker/corrections.py`) are saved at once, run no step, and reach Export through
  the Review step's result. A manual article order is applied in `export_step.reading_articles`.
  Seam 1 tests that need the outputs run and then export (`tests/fakes.py::run_and_export`).
