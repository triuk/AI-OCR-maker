"""Seam 1 and 2: empty word boxes for ink Local OCR missed, and Problem detection with local signals and the AI
read-through of the page text."""
from __future__ import annotations

import numpy
import pytest

from fakes import default_provider, run_and_export
from ocrmaker import edgeband
from ocrmaker.pages import IDENTITY
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


@pytest.mark.xfail(strict=True, reason=(
    "a layout region read on its own covers blurred words at the binding less than the whole page reading did "
    "(benchmark/JOURNAL.md, round layout-regions: words at the page edges and in the binding's shadow)"))
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


def scripted(tmp_path, pages, language=None, mark=None, **settings):
    """A workflow whose Local OCR reads the scripted pages, and its project after a run.

    mark is a rectangle (points) printed solid black into every scanned page, see pdfs.scan_pdf.
    """
    workflow = Workflow(ProjectStore(tmp_path / "library"), steps={**implementations(), "ocr": ScriptedOcr(pages)},
                        language=language)
    project = workflow.create(scan_pdf(tmp_path / "zpravodaj.pdf", pages=len(pages), mark=mark),
                              settings=settings or None)
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


def test_a_line_end_word_that_runs_on_into_the_next_line_without_a_hyphen_is_doubtful(tmp_path):
    lines = [[("Obecní", 96.0), ("úřad", 96.0), ("při", 96.0)],
             [("pravuje", 95.0), ("rozpočet", 96.0), ("a", 96.0), ("sporto'", 91.0)],
             [("vání", 95.0), ("schválilo.", 96.0), ("Žáci", 95.0), ("se", 96.0), ("do", 96.0)],
             [("opravených", 95.0), ("tříd", 96.0), ("vrátí", 96.0), ("do", 96.0)],
             [("Rychvaldu", 95.0), ("vání.", 96.0)]]
    workflow, project = scripted(tmp_path, [lines], project_dictionary=["Rychvaldu"])
    spots = flagged(workflow, project)

    assert spots["při"]["kind"] == "doubtful" and spots["při"]["signals"] == ["joins_next_line"]
    assert "joins_next_line" in spots["sporto'"]["signals"]
    assert {text for text, spot in spots.items() if "joins_next_line" in spot["signals"]} == {"při", "sporto'"}


SHADED_EDGE = [[("Obecní", 90.0), ("zastupitelstvo", 90.0), ("schválilo", 90.0)],
               [("rozpočet", 97.0), ("obce", 90.0), ("na", 90.0), ("rok", 90.0)],
               [("Zastupitelé", 80.0), ("odsouhlasili", 92.0), ("výdaje.", 95.0)]]


def test_words_beside_a_shadowed_page_edge_are_doubtful_unless_tesseract_is_nearly_certain(tmp_path):
    # Scripted lines start 50 pt from the left edge, 12 pt high; the shadow reaches 44 pt into the page.
    workflow, project = scripted(tmp_path, [SHADED_EDGE], mark=(0, 0, 44, 842))
    spots = flagged(workflow, project)

    assert {text: spot["signals"] for text, spot in spots.items()} == {
        "Obecní": ["damaged_edge"], "Zastupitelé": ["damaged_edge"]}
    assert spots["Obecní"]["kind"] == "doubtful"


def test_the_same_words_away_from_a_damaged_edge_are_not_flagged(tmp_path):
    workflow, project = scripted(tmp_path, [SHADED_EDGE])

    assert flagged(workflow, project) == {}


def test_a_page_whose_print_runs_to_the_scans_edge_has_a_damaged_band_along_it():
    # 10 px lines on a clean 600 px wide page; every line starts 5 px from the left edge.
    lines = [{"id": f"l{row}", "bbox": [5, 20 * row, 300, 20 * row + 10],
              "words": [{"id": f"l{row}-w{number}", "text": "slovo", "bbox": [5 + 60 * number, 20 * row,
                                                                              55 + 60 * number, 20 * row + 10]}
                        for number in range(5)]} for row in range(10)]
    band = edgeband.words_in_bands({"lines": lines}, numpy.full((220, 600), 255, numpy.uint8), IDENTITY)

    # The band reaches 1.5 line heights beyond the 5 px margin: only each line's first word starts in it.
    assert band == {f"l{row}-w0" for row in range(10)}


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


PROOFREAD = [[("Zasedání", 96.0), ("se", 96.0), ("konalo", 95.0), ("8do", 94.0), ("května.", 95.0)],
             [("Paní", 96.0), ("čárka", 95.0), ("Nováková", 95.0), ("je", 96.0), ("místo", 96.0), ("starosty.", 95.0)],
             [("Obec", 96.0), ("Rych4ald", 95.0), ("schválila", 95.0), ("rozpočet.", 96.0)]]


def lines_of(request) -> dict[str, int]:
    """The read-through request's numbered lines: text -> line number."""
    found = {}
    for article in request.data["articles"]:
        for line in article["text"].splitlines():
            number, text = line.split("| ", 1)
            found[text] = int(number)
    return found


def naming(*items):
    """A scripted read-through answer naming (word, reason) items on the line that holds the word."""
    def answer(request):
        lines = lines_of(request)
        return {"words": [{"line": next((number for text, number in lines.items() if word in text), 99),
                           "word": word, "reason": reason} for word, reason in items]}
    return answer


def test_the_ai_read_through_flags_words_that_do_not_fit_their_sentence_with_its_reason(tmp_path):
    provider = default_provider().on("readthrough", naming(
        ("čárka", "Jméno, nejspíš Šárka."), ("místo starosty", "Nejspíš místostarosty."),
        ("8do", "Chybí mezera: 8 do."), ("Rych4ald", "Rychvald."), ("Nováček", "Not in the text.")))
    workflow, project = scripted(tmp_path, [PROOFREAD], language=provider)
    spots = flagged(workflow, project)

    request = provider.requests_for("readthrough")[0]
    assert "Zasedání se konalo 8do května." in lines_of(request) and not request.images
    assert spots["čárka"]["kind"] == "doubtful" and spots["čárka"]["signals"] == ["context"]
    assert spots["čárka"]["note"] == "Jméno, nejspíš Šárka."
    assert spots["místo"]["note"] == spots["starosty."]["note"] == "Nejspíš místostarosty."
    assert spots["8do"]["signals"][-1] == "context" and spots["Rych4ald"]["signals"][-1] == "context"
    assert "Nováková" not in spots and "konalo" not in spots
    page = workflow.result(project["id"], "problems").read_json(page_file(0))
    assert [item["item"]["word"] for item in page["readthrough"]["ignored"]] == ["Nováček"]
    assert [spot["text"] for spot in page["spots"]] == ["8do", "čárka", "místo", "starosty.", "Rych4ald"]


def test_ai_reconstruction_reads_a_context_spot_in_the_print_with_the_read_through_reason(tmp_path):
    provider = default_provider().on("readthrough", naming(("čárka", "Jméno, nejspíš Šárka.")))
    workflow, project = scripted(tmp_path, [PROOFREAD], language=provider)

    request = provider.requests_for("reconstruction")[0]
    spot = next(spot for spot in request.data["spots"] if spot["reading"] == "čárka")
    assert spot["signals"] == ["context"] and spot["note"] == "Jméno, nejspíš Šárka."
    assert "signal context" in request.instructions and request.images


def test_an_invalid_read_through_answer_leaves_the_local_spots_with_a_warning(tmp_path):
    provider = default_provider().on("readthrough", {"names": []})
    workflow, project = scripted(tmp_path, [PROOFREAD], language=provider)

    assert set(flagged(workflow, project)) == {"Rych4ald"}
    problems = next(step for step in project["steps"] if step["id"] == "problems")
    assert problems["state"] == "warning" and "kontrolu smyslu textu" in problems["message"]
