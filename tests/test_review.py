"""Seam 1 and 2: manual review, the queue of illegible markers and manual corrections applied at export (ADR 0004)."""
from __future__ import annotations

import pymupdf
import pytest

from fakes import answer_spots
from ocrmaker.steps import implementations
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow
from pdfs import printed_text_pdf
from test_reconstruction import PRINTED, TEXT, PrintedOcr, spot_id, word_index

MARKER = "[NEČITELNÉ]"
# Three damaged words: "opravu" is reconstructed confidently, "čtvrtek" and the empty box of "kronikářem"
# stay illegible markers with the AI's doubtful candidates.
DAMAGED = {word_index("opravu"): ("opr", 20.0), word_index("čtvrtek"): ("čtv", 20.0),
           word_index("kronikářem"): ("", 0.0)}


def doubtful(request):
    return answer_spots({
        spot_id(request, "opr"): {"text": "opravu", "confidence": 95},
        spot_id(request, "čtv"): {"text": "čtvrtek", "confidence": 40},
        spot_id(request, ""): {"text": "kronikářem", "confidence": 30},
    })(request)


@pytest.fixture
def reviewed(tmp_path, language_provider):
    """A project run up to manual review, with two illegible markers left."""
    language_provider.on("reconstruction", doubtful)
    workflow = Workflow(ProjectStore(tmp_path / "library"), steps={**implementations(), "ocr": PrintedOcr(DAMAGED)},
                        language=language_provider)
    project = workflow.create(printed_text_pdf(tmp_path / "zpravodaj.pdf", TEXT))
    return workflow, workflow.run(project["id"])


def states(project) -> dict:
    return {step["id"]: step["state"] for step in project["steps"]}


def printed(index: int, count: int, before: bool) -> str:
    words = [text for text, _ in PRINTED]
    return " ".join(words[index - count:index] if before else words[index + 1:index + 1 + count])


def test_the_queue_lists_every_illegible_marker_with_its_crop_context_hint_and_candidate(reviewed):
    workflow, project = reviewed
    assert (states(project)["review"], states(project)["export"]) == ("pending", "pending")

    items = workflow.review_queue(project["id"])["items"]

    assert [(item["hint"], item["candidate"], item["text"]) for item in items] == [
        ("čtv", "čtvrtek", MARKER), ("", "kronikářem", MARKER)]
    first = items[0]
    assert first["context"]["before"].endswith(printed(word_index("čtvrtek"), 3, before=True))
    assert first["context"]["after"].startswith(printed(word_index("čtvrtek"), 3, before=False))
    crop = pymupdf.Pixmap(workflow.review_crop(project["id"], first["page"], first["id"]))
    page = pymupdf.Pixmap(workflow.page_image(project["id"], 0))
    assert 0 < crop.width <= page.width and 0 < crop.height < page.height / 4
    assert red_pixels(crop) > 0  # the marker's box is outlined


def red_pixels(pixmap: pymupdf.Pixmap) -> int:
    samples, n = pixmap.samples, pixmap.n
    return sum(1 for i in range(0, len(samples), n) if samples[i] > 180 and samples[i + 1] < 90)


def exported_text(workflow, project) -> tuple[list[str], str]:
    """The OCR PDF's words of page 1 and the article text Markdown of the last export."""
    names = {output["name"].rsplit(".", 1)[1]: output["name"] for output in workflow.outputs(project["id"])}
    with pymupdf.open(workflow.output(project["id"], names["pdf"])) as document:
        words = [word[4] for word in document[0].get_text("words", sort=False)]
    return words, workflow.output(project["id"], names["md"]).read_text(encoding="utf-8")


def step_runs(workflow, project) -> dict:
    return {step["id"]: (step["state"], step["run_id"]) for step in workflow.get(project["id"])["steps"]}


def test_a_queue_correction_is_saved_at_once_runs_no_step_and_survives_a_restart(reviewed, tmp_path,
                                                                                   language_provider):
    workflow, project = reviewed
    before = step_runs(workflow, project)
    item = workflow.review_queue(project["id"])["items"][0]

    saved = workflow.correct(project["id"], item["page"], item["id"], "čtvrtek", source="queue")

    assert saved["text"] == "čtvrtek" and saved["previous"] == MARKER
    assert step_runs(workflow, project) == before
    assert not any(record["kind"] == "step" for record in workflow.diagnostics(project["id"])
                   if record["run_id"] not in {run_id for _, run_id in before.values()})
    restarted = Workflow(ProjectStore(tmp_path / "library"), language=language_provider)
    [again] = [entry for entry in restarted.review_queue(project["id"])["items"] if entry["id"] == item["id"]]
    assert again["text"] == "čtvrtek" and again["correction"]["id"] == saved["id"]
    assert restarted.review_queue(project["id"])["counts"] == {"markers": 2, "corrected": 1, "remaining": 1,
                                                               "corrections": 1}


def test_corrections_appear_in_the_next_export_of_both_outputs(reviewed):
    workflow, project = reviewed
    item = workflow.review_queue(project["id"])["items"][0]
    workflow.correct(project["id"], item["page"], item["id"], "čtvrtek", source="queue")
    # Any word can be corrected, also one the automation took as right: "Knihovna" is a clean reading.
    clean = next(word for line in workflow.review_page(project["id"], 0)["articles"][0]["blocks"][0]["lines"]
                 for word in line if word["text"] == "Knihovna")
    assert clean["origin"] == "ocr"
    workflow.correct(project["id"], 0, clean["target"], "Městská knihovna")

    exported = workflow.export(project["id"])

    assert states(exported)["export"] == "done"
    words, markdown = exported_text(workflow, project)
    expected = [text for text, _ in PRINTED]
    expected[word_index("Knihovna")] = "Městská knihovna"
    expected[word_index("kronikářem")] = MARKER
    assert words == " ".join(expected).split()
    assert "Městská knihovna rozšiřuje" in markdown and "Ve čtvrtek proběhne" in markdown
    assert f"s místním {MARKER} o historii" in markdown


def test_export_with_remaining_markers_succeeds_and_the_report_lists_markers_and_corrections(reviewed):
    workflow, project = reviewed
    item = workflow.review_queue(project["id"])["items"][0]
    workflow.correct(project["id"], item["page"], item["id"], "čtvrtek", source="queue")

    exported = workflow.export(project["id"])

    assert (states(exported)["review"], states(exported)["export"]) == ("done", "done")
    report = workflow.report(project["id"])
    section = report["sections"]["review"]
    assert section["counts"] == {"markers": 2, "corrected": 1, "remaining": 1, "corrections": 1}
    [correction] = section["corrections"]
    assert (correction["page"], correction["previous"], correction["text"]) == (0, MARKER, "čtvrtek")
    [marker] = section["markers"]
    assert (marker["page"], marker["hint"], marker["candidate"]) == (0, "", "kronikářem")
    summary = workflow.report_summary(project["id"])
    assert "## Manual review" in summary
    assert "'[NEČITELNÉ]' -> 'čtvrtek'" in summary and "candidate 'kronikářem'" in summary


def test_correcting_a_word_again_replaces_its_correction_and_restoring_the_text_removes_it(reviewed):
    workflow, project = reviewed
    item = workflow.review_queue(project["id"])["items"][0]
    first = workflow.correct(project["id"], 0, item["id"], "čtvrteek")
    second = workflow.correct(project["id"], 0, item["id"], "  čtvrtek ")

    assert second["id"] == first["id"] and second["text"] == "čtvrtek"
    assert [entry["text"] for entry in workflow.corrections(project["id"])] == ["čtvrtek"]
    assert workflow.rerun_impact(project["id"], "reconstruct")["corrections"] == 1

    assert workflow.correct(project["id"], 0, item["id"], MARKER) is None
    assert workflow.corrections(project["id"]) == []
    workflow.correct(project["id"], 0, item["id"], "čtvrtek")
    [stored] = workflow.corrections(project["id"])
    workflow.revert_correction(project["id"], stored["id"])
    assert workflow.review_queue(project["id"])["items"][0]["text"] == MARKER


def test_a_correction_of_a_split_word_covers_the_whole_group(tmp_path, language_provider):
    language_provider.on("reconstruction", lambda request: answer_spots({
        tuple(spot["id"] for spot in request.data["spots"]): {"words": ["opravu", "školy"], "confidence": 95}
    })(request))
    joined = Workflow(ProjectStore(tmp_path / "library"),
                      steps={**implementations(), "ocr": PrintedOcr(joined=[word_index("opravu")])},
                      language=language_provider)
    project = joined.create(printed_text_pdf(tmp_path / "zpravodaj.pdf", TEXT))
    project = joined.run(project["id"])
    words = [word for article in joined.review_page(project["id"], 0)["articles"] for block in article["blocks"]
             for line in block["lines"] for word in line]
    split = [word for word in words if word["text"] in ("opravu", "školy")]
    assert [word["origin"] for word in split] == ["ai", "ai"] and split[0]["target"] == split[1]["target"]

    saved = joined.correct(project["id"], 0, split[1]["id"], "opravou školy")

    assert saved["ids"] == [split[0]["target"]] and saved["previous"] == "opravu školy"
    exported_words, _ = exported_text(joined, joined.export(project["id"]))
    assert "opravou" in exported_words and "opravu" not in exported_words


@pytest.fixture
def api(reviewed):
    from fastapi.testclient import TestClient

    from ocrmaker.api import create_api
    workflow, project = reviewed
    with TestClient(create_api(workflow, token="test-token"), headers={"X-Ocrmaker-Token": "test-token"}) as client:
        yield client, f"/api/projects/{project['id']}"


def test_the_api_serves_the_queue_with_crops_and_saves_corrections(api):
    client, base = api
    queue = client.get(f"{base}/review/queue").json()
    assert queue["counts"]["remaining"] == 2
    item = queue["items"][0]
    crop = client.get(f"{base}/review/pages/{item['page']}/crop/{item['id']}")
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/png"

    saved = client.put(f"{base}/corrections", json={"page": 0, "word": item["id"], "text": "čtvrtek",
                                                     "source": "queue"})

    assert saved.status_code == 200 and saved.json()["correction"]["text"] == "čtvrtek"
    assert client.get(f"{base}/review/queue").json()["counts"]["remaining"] == 1
    words = [word for article in client.get(f"{base}/review/pages/0").json()["articles"]
             for block in article["blocks"] for line in block["lines"] for word in line]
    assert {word["text"]: word["origin"] for word in words}["čtvrtek"] == "manual"
    [stored] = client.get(f"{base}/corrections").json()["corrections"]
    assert client.delete(f"{base}/corrections/{stored['id']}").status_code == 200
    assert client.get(f"{base}/corrections").json()["corrections"] == []


def test_the_api_explains_in_czech_why_review_or_a_correction_is_refused(api, reviewed, tmp_path):
    client, base = api
    unknown = client.put(f"{base}/corrections", json={"page": 0, "word": "p1-w9999", "text": "x"})
    assert unknown.status_code == 404 and unknown.json()["detail"] == "Slovo na stránce neexistuje."
    assert client.get(f"{base}/review/pages/7").status_code == 404
    assert client.put(f"{base}/corrections", json={"page": 0, "word": "p1-w1"}).status_code == 422

    workflow, _ = reviewed
    fresh = workflow.create(printed_text_pdf(tmp_path / "jiny.pdf", TEXT))
    refused = client.get(f"/api/projects/{fresh['id']}/review/queue")
    assert refused.status_code == 422 and refused.json()["detail"].startswith(
        "Ruční kontrola bude k dispozici po dokončení automatického zpracování (chybí: Import")
