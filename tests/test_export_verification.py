"""Seam 1 with real Tesseract: Export checks the OCR PDF it wrote, retries once and reports what remains."""
from __future__ import annotations

import pymupdf
import pytest

from fakes import BrokenExport, TransformingPrepare
from ocrmaker.steps import implementations
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow
from pdfs import columns_pdf, ground_truth


@pytest.fixture(scope="module")
def recognised(tmp_path_factory):
    """A project run once through Local OCR and Export over a two-column page of known Czech text.

    Tests export it again with their own Export step implementation; each export replaces the last one.
    """
    directory = tmp_path_factory.mktemp("verification")
    workflow = Workflow(ProjectStore(directory / "library"))
    project = workflow.run(workflow.create(columns_pdf(directory / "zpravodaj-2000-05.pdf"))["id"])
    return directory / "library", project["id"]


def export_with(recognised, export_step) -> tuple[Workflow, dict]:
    library, project_id = recognised
    workflow = Workflow(ProjectStore(library), steps={**implementations(), "export": export_step})
    return workflow, workflow.rerun(project_id, "export")


def step(project, step_id) -> dict:
    return next(entry for entry in project["steps"] if entry["id"] == step_id)


def first_word(workflow, project) -> dict:
    return workflow.overlay(project["id"], "ocr", 0)["words"][0]


def written_words(workflow, project) -> list[str]:
    [output] = workflow.outputs(project["id"])
    with pymupdf.open(workflow.output(project["id"], output["name"])) as document:
        return [word[4] for word in document[0].get_text("words", sort=False)]


def verification_records(workflow, project) -> list[dict]:
    run_id = step(project, "export")["run_id"]
    return [record for record in workflow.diagnostics(project["id"], run_id) if record["kind"] == "export_verification"]


def test_a_word_placed_outside_its_box_is_found_and_ends_export_with_a_warning_but_the_pdf_is_written(recognised):
    workflow, project = export_with(recognised, BrokenExport("outside_box"))

    export = step(project, "export")
    assert export["state"] == "warning"
    assert "textové vrstvy" in export["message"]
    assert len(written_words(workflow, project)) == len(ground_truth())
    findings = verification_records(workflow, project)[-1]["findings"]
    assert [(finding["word"], finding["kind"]) for finding in findings] == [(first_word(workflow, project)["id"],
                                                                             "outside_box")]


def second_word(workflow, project) -> dict:
    return workflow.overlay(project["id"], "ocr", 0)["words"][1]


def test_words_written_out_of_the_planned_order_are_found(recognised):
    workflow, project = export_with(recognised, BrokenExport("order"))

    assert step(project, "export")["state"] == "warning"
    findings = verification_records(workflow, project)[-1]["findings"]
    assert {finding["kind"] for finding in findings} == {"order"}
    assert {finding["word"] for finding in findings} <= {first_word(workflow, project)["id"],
                                                          second_word(workflow, project)["id"]}


def test_diacritics_that_do_not_copy_back_are_found_word_by_word(recognised):
    workflow, project = export_with(recognised, BrokenExport("diacritics"))

    assert step(project, "export")["state"] == "warning"
    findings = verification_records(workflow, project)[-1]["findings"]
    assert {finding["kind"] for finding in findings} == {"text"}
    first = first_word(workflow, project)
    assert first["text"] == "Obecní"
    assert {"word": first["id"], "text": "Obecní", "extracted": "Obecni"}.items() <= findings[0].items()
    assert len(findings) == sum(1 for word in ground_truth() if word.isascii() is False)


def test_a_failed_assembly_is_retried_once_with_the_alternative_strategy_and_its_findings_remain(recognised):
    workflow, project = export_with(recognised, BrokenExport("outside_box"))

    assert [record["strategy"] for record in verification_records(workflow, project)] == ["glyphless", "unicode"]


def test_a_missing_tounicode_map_is_found_and_the_alternative_strategy_copies_correctly_without_it(recognised):
    workflow, project = export_with(recognised, BrokenExport("to_unicode"))

    first, second = verification_records(workflow, project)
    # Without the map the per-document character ids come out as other characters or not at all.
    assert first["strategy"] == "glyphless"
    assert sum(first["counts"].get(kind, 0) for kind in ("missing", "text")) >= len(ground_truth()) / 2
    assert (second["strategy"], second["findings"]) == ("unicode", [])
    assert step(project, "export")["state"] == "done"
    stored = workflow.overlay(project["id"], "ocr", 0)["words"]
    assert written_words(workflow, project) == [word["text"] for word in stored]


@pytest.mark.parametrize("rotation, transform", [(0, None), (0, (2.0, [10, 20, 400, 580])), (90, None), (270, None)],
                         ids=["plain", "turned-and-cropped", "rotate-90", "rotate-270"])
def test_a_correct_assembly_verifies_without_findings_also_on_turned_and_rotated_pages(tmp_path, rotation, transform):
    steps = implementations()
    if transform:
        steps["prepare"] = TransformingPrepare(rotate=transform[0], crop=transform[1])
    workflow = Workflow(ProjectStore(tmp_path / "library"), steps=steps)
    project = workflow.run(workflow.create(columns_pdf(tmp_path / "scan.pdf", rotation=rotation))["id"])

    assert step(project, "export")["state"] == "done"
    [record] = verification_records(workflow, project)
    assert (record["strategy"], record["findings"]) == ("glyphless", [])


def test_the_export_step_preview_highlights_the_words_with_findings(recognised):
    workflow, project = export_with(recognised, BrokenExport("outside_box"))

    overlay = workflow.overlay(project["id"], "export", 0)

    flagged = [word for word in overlay["words"] if word.get("findings")]
    assert [word["id"] for word in flagged] == [first_word(workflow, project)["id"]]
    assert flagged[0]["findings"][0]["kind"] == "outside_box"
    assert flagged[0]["bbox"] == first_word(workflow, project)["bbox"]
    assert overlay["extra"] == []
    assert len(overlay["words"]) == len(ground_truth())


def test_remaining_findings_are_listed_in_the_run_report(recognised):
    workflow, project = export_with(recognised, BrokenExport("outside_box"))

    report = workflow.report(project["id"])

    export = report["sections"]["export"]
    word = first_word(workflow, project)
    assert [(finding["word"], finding["kind"], finding["text"]) for finding in export["findings"]] == [
        (word["id"], "outside_box", word["text"])]
    assert export["attempts"] == [{"strategy": "glyphless", "findings": 1}, {"strategy": "unicode", "findings": 1}]
    assert f"word {word['id']}: outside_box" in workflow.report_summary(project["id"])
