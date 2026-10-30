"""Optional slow smoke test: the whole workflow on a real scan, outside the default suite.

Run it with the path of a scanned PDF (no real scans are committed), for example:

    OCRMAKER_SMOKE_PDF=~/Dokumenty/Rychvald-zpravodaje-OCR/zpravodaj-2000-05-c03.pdf \\
        .venv/bin/python -m pytest -q -m slow -s

It runs with the shipped steps and no AI, prints the run report's human summary, and keeps the project under
pytest's temporary directory for a closer look.
"""
from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest

from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow

SMOKE_PDF = os.environ.get("OCRMAKER_SMOKE_PDF")

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not SMOKE_PDF, reason="set OCRMAKER_SMOKE_PDF to the path of a real scan")]


def test_a_real_scan_goes_through_the_workflow_into_a_verified_ocr_pdf_and_a_run_report(tmp_path):
    source = Path(SMOKE_PDF).expanduser()
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=None)

    project = workflow.run(workflow.create(source)["id"])

    states = {step["id"]: step["state"] for step in project["steps"]}
    messages = {step["id"]: step["message"] for step in project["steps"] if step["message"]}
    assert states["ocr"] in ("done", "warning"), messages
    assert states["export"] in ("done", "warning"), messages
    report = workflow.report(project["id"])
    summary = workflow.report_summary(project["id"])
    print(f"\n{summary}\nProject directory: {workflow.store.directory(project['id'])}")
    pages = report["sections"]["ocr"]["pages"]
    assert len(pages) == project["source"]["pages"]
    assert report["sections"]["ocr"]["words"] > 0
    [output] = workflow.outputs(project["id"])
    with pymupdf.open(workflow.output(project["id"], output["name"])) as document:
        assert sum(len(page.get_text("words")) for page in document) > 0
