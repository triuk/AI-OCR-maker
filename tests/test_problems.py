"""Seam 1 and 2: empty word boxes for ink Local OCR missed, and Problem detection with local signals only."""
from __future__ import annotations

import pytest

from fakes import run_and_export
from ocrmaker.step import Step
from ocrmaker.steps import implementations
from ocrmaker.store import ProjectStore
from ocrmaker.words import page_file
from ocrmaker.workflow import Workflow
from pdfs import BINDING_TEXT, PLANTED, binding_pdf, printed_words, scan_pdf

BAND = 90  # points of blurred binding at the left page edge


@pytest.fixture(scope="module")
def binding(tmp_path_factory):
    """One run over a page whose binding band is blurred, shared by the tests that only read its results."""
    directory = tmp_path_factory.mktemp("binding")
    workflow = Workflow(ProjectStore(directory / "library"))
    source = binding_pdf(directory / "zpravodaj-2000-05.pdf", band=BAND)
    project = run_and_export(workflow, workflow.create(source)["id"])
    return workflow, project


def overlap(a, b) -> float:
    """The part of box a's width that box b covers, if their vertical middles overlap."""
    middle = (a[1] + a[3]) / 2
    if not b[1] <= middle <= b[3]:
        return 0.0
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) / (a[2] - a[0])


def covered(box, boxes) -> float:
    """How much of a box's width the given boxes cover together."""
    spans = sorted((max(box[0], b[0]), min(box[2], b[2])) for b in boxes if overlap(box, b) > 0)
    total, reach = 0.0, box[0]
    for start, end in spans:
        start = max(start, reach)
        if end > start:
            total += end - start
            reach = end
    return total / (box[2] - box[0])


def test_every_printed_word_in_the_blurred_binding_band_has_a_word_box(binding):
    workflow, project = binding
    words = workflow.overlay(project["id"], "ocr", 0)["words"]
    in_band = [(text, box) for text, box in printed_words() if box[0] < BAND]
    assert len(in_band) >= 10

    for text, box in in_band:
        assert covered(box, [word["bbox"] for word in words]) > 0.75, text
    empty = [word for word in words if word["text"] == ""]
    assert empty, "Tesseract missed no word in the band, so the fixture tests nothing"
    # Missed words get empty word boxes; clean print outside the band never does.
    assert all(word["bbox"][0] < BAND for word in empty)
    ocr_section = workflow.report(project["id"])["sections"]["ocr"]
    assert ocr_section["pages"][0]["empty_words"] == len(empty)
    missed = [text for text, box in in_band
              if not any(overlap(box, word["bbox"]) > 0.3 for word in words if word["text"])]
    assert missed
    for text, box in in_band:
        if text in missed:
            assert any(overlap(box, word["bbox"]) > 0.3 for word in empty), text


def test_planted_words_lie_outside_the_band():
    planted = [box for text, box in printed_words() if text.strip(".,") in PLANTED]
    assert len(planted) == 2 and all(box[0] > BAND + 20 for box in planted)
    assert "Rych4ald" in BINDING_TEXT


def spots(workflow, project, page: int = 0) -> list[dict]:
    return workflow.overlay(project["id"], "problems", page)["spots"]


def test_problem_detection_runs_after_local_ocr_and_before_export(binding):
    _, project = binding
    states = {step["id"]: step["state"] for step in project["steps"]}
    assert states["problems"] == "done" and states["export"] == "done"


def test_every_empty_word_box_is_an_unreadable_word(binding):
    workflow, project = binding
    empty = [word["id"] for word in workflow.overlay(project["id"], "ocr", 0)["words"] if not word["text"]]
    kinds = {spot["id"]: spot["kind"] for spot in spots(workflow, project)}

    assert empty and all(kinds.get(word_id) == "unreadable" for word_id in empty)


def test_planted_doubtful_readings_and_non_dictionary_words_are_flagged(binding):
    workflow, project = binding
    found = spots(workflow, project)

    for text, box in printed_words():
        if text.strip(".,") in PLANTED:
            [spot] = [spot for spot in found if overlap(box, spot["bbox"]) > 0.5]
            assert spot["kind"] == "doubtful", text
    by_text = {spot["text"]: spot for spot in found}
    assert "digits_in_word" in by_text["Rych4ald"]["signals"]
    assert "not_in_dictionary" in by_text["Zbrdlíkov"]["signals"]


def test_clean_dictionary_words_read_with_high_confidence_are_not_flagged(binding):
    workflow, project = binding
    planted = [box for text, box in printed_words() if text.strip(".,") in PLANTED]
    words = workflow.overlay(project["id"], "ocr", 0)["words"]
    clean = [word for word in words if word["bbox"][0] > BAND and word["confidence"] >= 90]
    assert len(clean) > 50

    flagged = {spot["id"] for spot in spots(workflow, project)}
    for word in clean:
        if not any(overlap(box, word["bbox"]) > 0.5 for box in planted):
            assert word["id"] not in flagged, word["text"]


class ScriptedOcr(Step):
    """Local OCR that 'recognises' scripted lines: per page, lines of (text, confidence) words laid out in a row."""

    id = "ocr"

    def __init__(self, pages: list[list[list[tuple[str, float]]]]):
        self.pages = pages

    def run(self, ctx):
        for index, lines in enumerate(self.pages):
            number, count, stored = index + 1, 0, []
            for row, words in enumerate(lines):
                x, y, placed = 50.0, 100.0 + 20 * row, []
                for text, confidence in words:
                    count += 1
                    width = 6.0 * (len(text) or 5)
                    placed.append({"id": f"p{number}-w{count}", "text": text, "bbox": [x, y, x + width, y + 12],
                                   "confidence": confidence, "chars": []})
                    x += width + 4
                stored.append({"id": f"p{number}-l{row + 1}", "bbox": [50.0, y, x - 4, y + 12],
                               "baseline": None, "block": 0, "paragraph": 0, "words": placed})
            ctx.output.write_json(page_file(index), {"index": index, "lines": stored})
        ctx.output.write_json("ocr.json", {"engine": {"name": "scripted"}, "pages": []})


def scripted(tmp_path, pages, **settings):
    """A workflow whose Local OCR reads the scripted pages, and its project after a run."""
    workflow = Workflow(ProjectStore(tmp_path / "library"), steps={**implementations(), "ocr": ScriptedOcr(pages)})
    project = workflow.create(scan_pdf(tmp_path / "zpravodaj.pdf", pages=len(pages)), settings=settings or None)
    return workflow, workflow.run(project["id"])


def flagged(workflow, project, page: int = 0) -> dict[str, dict]:
    return {spot["text"]: spot for spot in spots(workflow, project, page)}


CLEAN = [("Obecní", 96.0), ("zastupitelstvo", 95.0), ("schválilo", 94.0), ("rozpočet.", 93.0)]


def test_a_word_in_the_project_dictionary_is_not_flagged_by_the_dictionary_check(tmp_path):
    workflow, project = scripted(tmp_path, [[CLEAN, [("Zbrdlíkov", 96.0), ("Rychvald", 96.0)]]])
    assert "not_in_dictionary" in flagged(workflow, project)["Zbrdlíkov"]["signals"]

    workflow.update_settings(project["id"], {"project_dictionary": ["zbrdlíkov"]})
    workflow.rerun(project["id"], "problems")

    assert "Zbrdlíkov" not in flagged(workflow, project)


def test_thresholds_are_project_settings_and_changing_them_changes_the_detected_spots(tmp_path):
    words = [("Obecní", 96.0), ("zastupitelstvo", 85.0), ("schválilo", 60.0), ("rozpočet", 30.0)]
    workflow, project = scripted(tmp_path, [[words]])
    assert {text: spot["kind"] for text, spot in flagged(workflow, project).items()} == {
        "schválilo": "doubtful", "rozpočet": "unreadable"}

    workflow.update_settings(project["id"], {"problem_unreadable_confidence": 75,
                                             "problem_doubtful_confidence": 90})
    workflow.rerun(project["id"], "problems")

    assert {text: spot["kind"] for text, spot in flagged(workflow, project).items()} == {
        "zastupitelstvo": "doubtful", "schválilo": "unreadable", "rozpočet": "unreadable"}


@pytest.mark.parametrize(("text", "signal"), [
    ("Rychva1d", "digits_in_word"), ("0becní", "digits_in_word"), ("l9", "digits_in_word"),
    ("rYchvald", "mixed_case"), ("ObecnÍ", "mixed_case"),
    ("pŕíští", "implausible_diacritics"), ("ůhel", "implausible_diacritics"), ("žěna", "implausible_diacritics"),
    ("|omněl", "odd_symbols"), ("ob§ec", "odd_symbols"),
    ("Rychvaldsko", "not_in_dictionary"),
])
def test_odd_character_mixes_and_non_dictionary_words_are_doubtful_readings(tmp_path, text, signal):
    workflow, project = scripted(tmp_path, [[CLEAN, [("Jde", 96.0), (text, 96.0), ("domů.", 96.0)]]])
    spot = flagged(workflow, project)[text]

    assert spot["kind"] == "doubtful" and signal in spot["signals"]


def test_numbers_units_codes_quotes_and_line_end_hyphenation_are_not_flagged(tmp_path):
    lines = [[("Dne", 96.0), ("20.", 95.0), ("5.", 95.0), ("2000", 96.0), ("přivezli", 95.0), ("5kg", 94.0),
              ("papíru", 95.0), ("A4", 94.0), ("a", 96.0), ("„Starosta“", 93.0), ("zastupi-", 95.0)],
             [("telstvo", 95.0), ("česko-slovenský", 94.0), ("poděkoval.", 96.0), ("–", 92.0)],
             [("Ing.", 95.0), ("Novák,", 96.0), ("č.", 95.0), ("p.", 94.0), ("15,", 95.0), ("MěÚ", 95.0), ("DPS", 93.0),
              ("a.s.,", 94.0), ("apod.", 95.0)]]
    workflow, project = scripted(tmp_path, [lines])

    assert flagged(workflow, project) == {}


def test_a_hyphenated_word_whose_continuation_is_unreadable_is_judged_only_by_the_continuation(tmp_path):
    lines = [[("Obecní", 96.0), ("zastupitelstvo", 95.0), ("přihlá-", 95.0)],
             [("", 0.0), ("schválilo", 95.0), ("rozpočet.", 96.0)]]
    workflow, project = scripted(tmp_path, [lines])

    assert {text: spot["kind"] for text, spot in flagged(workflow, project).items()} == {"": "unreadable"}


TWO_PAGES = [[CLEAN, [("Rych4ald", 95.0), ("", 0.0), ("obec", 20.0)]],
             [[("Zbrdlíkov", 96.0), ("pŕíští", 95.0)], CLEAN]]


def test_the_report_counts_problem_spots_by_kind_per_page(tmp_path):
    workflow, project = scripted(tmp_path, TWO_PAGES)

    section = workflow.report(project["id"])["sections"]["problems"]
    assert section["pages"] == [{"index": 0, "unreadable": 2, "doubtful": 1},
                                {"index": 1, "unreadable": 0, "doubtful": 2}]
    assert section["total"] == {"unreadable": 2, "doubtful": 3}
    summary = workflow.report_summary(project["id"])
    assert "## Problem detection" in summary and "| 1 | 2 | 1 |" in summary and "| 2 | 0 | 2 |" in summary


def test_problem_spot_counts_are_kept_with_the_result_and_in_diagnostics(tmp_path):
    workflow, project = scripted(tmp_path, TWO_PAGES)

    summary = workflow.result(project["id"], "problems").read_json("problems.json")
    assert [(page["index"], page["unreadable"], page["doubtful"]) for page in summary["pages"]] == [
        (0, 2, 1), (1, 0, 2)]
    assert summary["total"] == {"unreadable": 2, "doubtful": 3}
    records = [record for record in workflow.diagnostics(project["id"]) if record["kind"] == "problems_page"]
    assert [(record["index"], record["unreadable"], record["doubtful"]) for record in records] == [
        (0, 2, 1), (1, 0, 2)]


def test_the_step_preview_distinguishes_unreadable_words_from_doubtful_readings(tmp_path):
    workflow, project = scripted(tmp_path, TWO_PAGES)

    overlay = workflow.overlay(project["id"], "problems", 0)
    assert overlay["counts"] == {"unreadable": 2, "doubtful": 1}
    assert [(spot["text"], spot["kind"]) for spot in overlay["spots"]] == [
        ("Rych4ald", "doubtful"), ("", "unreadable"), ("obec", "unreadable")]
    assert all(set(spot) >= {"id", "bbox", "confidence", "signals"} for spot in overlay["spots"])


def test_the_api_serves_the_problem_preview_and_takes_threshold_and_dictionary_settings(client, workflow,
                                                                                         source_pdf):
    project = workflow.create(source_pdf)
    settings = f"/api/projects/{project['id']}/settings"

    defaults = client.get(settings).json()
    assert defaults["problem_unreadable_confidence"] < defaults["problem_doubtful_confidence"]
    assert defaults["project_dictionary"] == []
    saved = client.put(settings, json={"problem_unreadable_confidence": 40, "problem_doubtful_confidence": 70,
                                       "project_dictionary": "Zbrdlíkov\n Rychvaldsko \n\nZbrdlíkov"}).json()
    assert saved["settings"]["project_dictionary"] == ["Zbrdlíkov", "Rychvaldsko"]
    assert saved["settings"]["problem_doubtful_confidence"] == 70
    for refused in ({"problem_doubtful_confidence": 120}, {"problem_unreadable_confidence": 80},
                    {"project_dictionary": ["dvě slova"]}):
        response = client.put(settings, json=refused)
        assert response.status_code == 422 and response.json()["detail"], refused
    listing = {step["id"]: step["preview"] for step in client.get("/api/projects").json()["steps"]}
    assert listing["problems"] is True
    assert "unreadable" in client.get("/previews/problems.js").text
