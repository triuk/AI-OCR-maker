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
- AI requests go through `ctx.language.ask(...)` (`src/ocrmaker/language.py`); the CLIs are found on PATH or through
  `OCRMAKER_CLAUDE` / `OCRMAKER_CODEX`. Seam 1 tests script the AI with `tests/fakes.py::ScriptedProvider`
  (the `language_provider` fixture).
