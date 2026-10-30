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
