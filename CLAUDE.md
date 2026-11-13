## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues of `triuk/AI-OCR-maker` (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Development

- Setup (no sudo, idempotent, caches shared between checkouts): `./setup.sh`. Start: `./start.sh` (`--server`,
  `--data-dir DIR`, `--port N`).
- Tests: `.venv/bin/python -m pytest -q`; the full suite takes about 17 minutes, so run it in the background. Slow
  smoke test on a real scan: `OCRMAKER_SMOKE_PDF=/path/scan.pdf .venv/bin/python -m pytest -q -m slow -s`.
- Dependencies are pinned in `uv.lock`, Python in `.python-version`; after editing `pyproject.toml` run
  `~/.local/share/ai-ocr-maker/tools/uv-<version>/uv lock`, then `./setup.sh`.
- Benchmark and optimisation rounds: `benchmark/README.md`, `benchmark/JOURNAL.md`, skill `/optimize`.

## Map

Each module's docstring is the authority for its format and rules; this list says where to look.

- Workflow: the eleven steps in `src/ocrmaker/steps/__init__.py`; a step is `src/ocrmaker/steps/<id>_step.py`
  with a module-level `STEP` (`src/ocrmaker/step.py`), its preview `src/ocrmaker/ui/previews/<id>.js` drawing
  through `ocrmaker.pageViewer` (`ui/viewer.js`). Runs, rounds and the review service: `workflow.py`.
- Pages, geometry, page transform: `pages.py` (the Import result is their authority). Steps read the PDF through
  `ctx.source_pdf` (the clean copy once an old text layer was removed, `existing_text.py`).
- Layout regions (ADR 0006, step `regions`, label Rozvržení): `regions.py`; a run stops after it while the
  project's `"layout"` is "waiting" (setting `layout_auto_approve` skips the stop). The person's edits:
  `layout_review.py`, editor `ui/previews/regions.js`.
- Recognised words: `words.py`, read per region by `steps/ocr_step.py` (empty word boxes `inkgaps.py`). Blocks
  inside regions: `layout.py`. Articles and cross-page links: `reading_order.py`.
- Problem spots: `steps/problems_step.py`, `problems.py`, AI read-through `readthrough.py`, dictionaries
  `hunspell.py`. AI reconstruction: `reconstruction.py`; Web lookup: `lookup.py`; both applied by
  `transcription.py`. Manual corrections: `corrections.py`, review service `review.py`.
- Export: OCR PDF `textlayer.py`, verified by `verify.py`; article Markdown `articletext.py`. Every word's text
  comes from `ExportStep.exported_words`, the one place where reconstructions and corrections apply.
- AI requests: `ctx.language.ask(...)` (`language.py`; CLIs on PATH or `OCRMAKER_CLAUDE` / `OCRMAKER_CODEX`).
  Run reports: `report.py` (a step adds metrics through `Step.report`).

## Tests

- Seam 1 tests script the AI with `tests/fakes.py::ScriptedProvider`; the `language_provider` fixture answers
  every purpose with a default (`fakes.default_provider`: one layout region per page). `tests/conftest.py`
  turns `layout_auto_approve` on for the suite; layout-round tests turn it off.
- Helpers: `fakes.run_and_export`, `fakes.answer_spots`, `fakes.BrokenExport`, printed pages `tests/layout_pdfs.py`
  and `tests/pdfs.py`, printed ground truth `test_reconstruction.py::PrintedOcr`.
