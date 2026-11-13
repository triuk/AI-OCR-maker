"""Seam 1: every workflow run leaves a run report, as JSON plus a short human summary."""
from __future__ import annotations

import pytest

from fakes import RecordingStep, ScriptedProvider, run_and_export
from ocrmaker.step import Step
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow
from pdfs import columns_pdf, ground_truth


@pytest.fixture
def step_implementations():
    return {"import": RecordingStep("import"), "prepare": RecordingStep("prepare", warning="Pozor na okraje.")}


@pytest.fixture
def project(workflow, source_pdf):
    return workflow.create(source_pdf, name="Zpravodaj 5/2000")


def entry(report, step_id) -> dict:
    return next(step for step in report["steps"] if step["id"] == step_id)


def test_a_run_writes_a_report_with_each_steps_outcome_and_timing(workflow, project):
    workflow.run(project["id"])

    report = workflow.report(project["id"])

    assert report["project"]["name"] == "Zpravodaj 5/2000"
    assert report["run"]["operation"] == "run"
    assert [step["step"] for step in report["run"]["steps"]] == ["import", "prepare"]
    assert entry(report, "import")["state"] == "done"
    assert entry(report, "prepare")["state"] == "warning"
    assert entry(report, "prepare")["message"] == "Pozor na okraje."
    assert entry(report, "ocr")["state"] == "pending"
    assert all(entry(report, step)["duration"] >= 0 and entry(report, step)["run_id"] == report["run"]["id"]
               for step in ("import", "prepare"))
    assert report["run"]["duration"] >= entry(report, "import")["duration"]


def test_the_human_summary_names_the_project_and_lists_the_steps(workflow, project):
    workflow.run(project["id"])

    summary = workflow.report_summary(project["id"])

    assert summary.startswith("# Run report: Zpravodaj 5/2000")
    assert "| prepare | warning |" in summary
    assert "Pozor na okraje." in summary


def test_every_run_leaves_its_own_report_even_a_failed_one(workflow, project, library):
    workflow.run(project["id"])
    first = workflow.report(project["id"])["run"]["id"]
    failing = Workflow(ProjectStore(library), steps={"import": RecordingStep("import", failure="Nečitelné PDF.")})

    failing.rerun(project["id"], "import")

    report = failing.report(project["id"])
    assert report["run"]["id"] != first
    assert report["run"]["operation"] == "rerun"
    assert entry(report, "import")["state"] == "failed"
    assert entry(report, "import")["message"] == "Nečitelné PDF."
    assert workflow.report(project["id"], first)["run"]["id"] == first
    assert [run["id"] for run in workflow.reports(project["id"])] == [first, report["run"]["id"]]


@pytest.fixture(scope="module")
def recognised(tmp_path_factory):
    """A real run (Tesseract) over a page of known Czech text, then an export: the export's report."""
    directory = tmp_path_factory.mktemp("report")
    workflow = Workflow(ProjectStore(directory / "library"))
    source = columns_pdf(directory / "zpravodaj-2000-05.pdf")
    project = run_and_export(workflow, workflow.create(source)["id"])
    return workflow, project


def test_the_report_has_the_word_count_and_mean_confidence_of_every_page(recognised):
    workflow, project = recognised

    ocr = workflow.report(project["id"])["sections"]["ocr"]

    [page] = ocr["pages"]
    assert (page["index"], page["words"]) == (0, len(ground_truth()))
    assert 80 < page["mean_confidence"] <= 100
    assert (ocr["words"], ocr["mean_confidence"]) == (page["words"], page["mean_confidence"])
    summary = workflow.report_summary(project["id"])
    assert "## Local OCR" in summary
    assert f"| 1 | {len(ground_truth())} | {page['mean_confidence']} |" in summary


def test_the_report_lists_the_export_outputs_and_its_verification(recognised):
    workflow, project = recognised

    export = workflow.report(project["id"])["sections"]["export"]

    assert export["outputs"] == ["zpravodaj-2000-05-ocr.md", "zpravodaj-2000-05-ocr.pdf"]
    assert (export["strategy"], export["attempts"], export["findings"]) == (
        "glyphless", [{"strategy": "glyphless", "findings": 0}], [])
    assert "Verification found nothing" in workflow.report_summary(project["id"])


class AskingStep(Step):
    """Asks the AI twice for the reading order and stores the answers."""

    id = "order"

    def run(self, ctx):
        answers = [ctx.language.ask("Order the blocks.", {"page": page}, purpose="example").data
                   for page in (0, 1)]
        ctx.output.write_json("result.json", {"answers": answers})


def run_asking(library, source_pdf, provider) -> tuple[Workflow, dict]:
    provider.on("example", {"order": [1, 2]})
    workflow = Workflow(ProjectStore(library), steps={"order": AskingStep()}, language=provider)
    return workflow, workflow.run(workflow.create(source_pdf)["id"])


def test_the_report_lists_the_ai_requests_with_provider_model_and_usage(library, source_pdf):
    workflow, project = run_asking(library, source_pdf, ScriptedProvider())

    ai = workflow.report(project["id"])["ai"]

    assert [(r["step"], r["purpose"], r["provider"], r["model"], r["outcome"]) for r in ai["requests"]] == [
        ("order", "example", "claude", "scripted-model", "ok")] * 2
    assert ai["requests"][0]["usage"]["input"] == 1200
    totals = ai["totals"]
    assert (totals["requests"], totals["failed"], totals["unknown_usage"]) == (2, 0, 0)
    assert (totals["usage"]["input"], totals["usage"]["cached_input"], totals["usage"]["output"]) == (2400, 2000, 160)
    assert totals["usage"]["reasoning"] is None
    assert totals["models"] == [{"provider": "claude", "model": "scripted-model", "requests": 2}]
    assert "2 requests (0 failed)" in workflow.report_summary(project["id"])


def test_unknown_ai_usage_stays_unknown_in_the_report(library, source_pdf):
    workflow, project = run_asking(library, source_pdf, ScriptedProvider(usage=None))

    totals = workflow.report(project["id"])["ai"]["totals"]

    assert totals["unknown_usage"] == 2
    assert totals["usage"]["input"] is None
    assert "Usage unknown for 2 requests" in workflow.report_summary(project["id"])
