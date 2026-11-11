"""Seam 1 and 2: the extras of manual review: propagation suggestions, region notes and the
optional spot check of accepted reconstructions (all manual corrections, ADR 0004)."""
from __future__ import annotations

import pytest

from fakes import answer_spots
from ocrmaker.steps import implementations
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow
from pdfs import CZECH_COLUMNS, printed_text_pdf, printed_words
from test_reconstruction import PrintedOcr, spot_id
from test_review import MARKER, exported_text, reviewed  # noqa: F401 (a fixture)

# A name printed three times: once read in part ("Zbrd"), once as an empty word box in the same phrase
# ("kronikář _ a"), once read in part from its other end ("brdlík"). "čtvrtek" is an unrelated marker.
NAMES = (CZECH_COLUMNS[0] + " Besedu vede kronikář Zbrdlík a starosta obce. Na fotografiích je kronikář Zbrdlík"
         " a jeho rodina. Pan Zbrdlík přinese také staré mapy. " + CZECH_COLUMNS[1])
NAMES_PRINTED = printed_words(NAMES)


def occurrences(text: str) -> list[int]:
    return [index for index, (printed, _) in enumerate(NAMES_PRINTED) if printed == text]


FIRST, SAME_PHRASE, OTHER_END = occurrences("Zbrdlík")
[THURSDAY] = occurrences("čtvrtek")
NAMES_DAMAGED = {FIRST: ("Zbrd", 20.0), SAME_PHRASE: ("", 0.0), OTHER_END: ("brdlík", 20.0), THURSDAY: ("čtv", 20.0)}


def unsure(request):
    """The AI guesses every damaged spot with too little confidence: all four stay illegible markers."""
    return answer_spots({spot_id(request, reading): {"text": text, "confidence": 30}
                         for reading, text in (("Zbrd", "Zbrdlíková"), ("", "kronikář"), ("brdlík", "Brdlík"),
                                               ("čtv", "čtvrtek"))})(request)


@pytest.fixture
def names(tmp_path, language_provider):
    language_provider.on("reconstruction", unsure)
    workflow = Workflow(ProjectStore(tmp_path / "library"),
                        steps={**implementations(), "ocr": PrintedOcr(NAMES_DAMAGED, printed=NAMES_PRINTED)},
                        language=language_provider)
    project = workflow.run(workflow.create(printed_text_pdf(tmp_path / "zpravodaj.pdf", NAMES))["id"])
    return workflow, project


def by_hint(workflow, project) -> dict[str, dict]:
    return {item["hint"]: item for item in workflow.review_queue(project["id"])["items"]}


def test_resolving_a_marker_suggests_its_text_for_markers_with_a_matching_partial_reading_or_context(names):
    workflow, project = names
    queue = by_hint(workflow, project)
    assert set(queue) == {"Zbrd", "", "brdlík", "čtv"}
    assert all(item["suggestion"] is None for item in queue.values())

    saved = workflow.correct(project["id"], 0, queue["Zbrd"]["id"], "Zbrdlík", source="queue")

    queue = by_hint(workflow, project)
    assert queue["brdlík"]["suggestion"] == {"text": "Zbrdlík", "correction": saved["id"], "match": "reading"}
    assert queue[""]["suggestion"] == {"text": "Zbrdlík", "correction": saved["id"], "match": "context"}
    assert queue["čtv"]["suggestion"] is None and queue["Zbrd"]["suggestion"] is None
    assert [item["hint"] for item in workflow.suggestions(project["id"], saved["id"])] == ["", "brdlík"]


def test_accepting_a_suggestion_is_one_action_and_reaches_the_export(names):
    workflow, project = names
    queue = by_hint(workflow, project)
    workflow.correct(project["id"], 0, queue["Zbrd"]["id"], "Zbrdlík", source="queue")

    accepted = workflow.accept_suggestion(project["id"], 0, queue["brdlík"]["id"])

    assert (accepted["text"], accepted["source"], accepted["previous"]) == ("Zbrdlík", "suggestion", MARKER)
    queue = by_hint(workflow, project)
    assert queue["brdlík"]["correction"]["id"] == accepted["id"] and queue["brdlík"]["suggestion"] is None
    with pytest.raises(KeyError):
        workflow.accept_suggestion(project["id"], 0, queue["čtv"]["id"])
    words, markdown = exported_text(workflow, workflow.export(project["id"]))
    assert words.count("Zbrdlík") == 2 and words.count(MARKER) == 2
    assert "Pan Zbrdlík přinese" in markdown


def client_for(workflow, project):
    from fastapi.testclient import TestClient

    from ocrmaker.api import create_api
    client = TestClient(create_api(workflow, token="test-token"), headers={"X-Ocrmaker-Token": "test-token"})
    return client, f"/api/projects/{project['id']}"


def test_the_api_returns_the_suggestions_a_correction_makes_and_accepts_one_with_one_request(names):
    workflow, project = names
    client, base = client_for(workflow, project)
    queue = by_hint(workflow, project)

    saved = client.put(f"{base}/corrections", json={"page": 0, "word": queue["Zbrd"]["id"], "text": "Zbrdlík",
                                                     "source": "queue"}).json()

    assert [item["hint"] for item in saved["suggestions"]] == ["", "brdlík"]
    accepted = client.post(f"{base}/review/suggestions", json={"page": 0, "word": queue[""]["id"]})
    assert accepted.status_code == 200 and accepted.json()["correction"]["source"] == "suggestion"
    assert [item["hint"] for item in client.get(f"{base}/review/suggestions").json()["suggestions"]] == ["brdlík"]
    refused = client.post(f"{base}/review/suggestions", json={"page": 0, "word": queue["čtv"]["id"]})
    assert refused.status_code == 404 and refused.json()["detail"] == "Pro toto místo není žádný návrh."


def test_accepted_reconstructions_follow_the_markers_in_the_queue_only_when_asked_for(reviewed):
    workflow, project = reviewed
    assert [item["kind"] for item in workflow.review_queue(project["id"])["items"]] == ["marker", "marker"]

    queue = workflow.review_queue(project["id"], reconstructions=True)

    kinds = [item["kind"] for item in queue["items"]]
    assert kinds[:2] == ["marker", "marker"] and set(kinds[2:]) == {"reconstruction"}
    [opravu] = [item for item in queue["items"] if item["hint"] == "opr"]
    assert (opravu["text"], opravu["status"], opravu["candidate"]) == ("opravu", "accepted", "opravu")
    assert queue["counts"]["reconstructions"] == len(kinds) - 2 and queue["counts"]["markers"] == 2

    workflow.correct(project["id"], 0, opravu["id"], "opravou", source="queue")
    [checked] = [item for item in workflow.review_queue(project["id"], reconstructions=True)["items"]
                 if item["hint"] == "opr"]
    assert checked["text"] == "opravou" and checked["correction"]["previous"] == "opravu"
    assert workflow.review_queue(project["id"])["counts"]["corrected"] == 0
    client, base = client_for(workflow, project)
    served = client.get(f"{base}/review/queue", params={"reconstructions": "true"}).json()
    assert [item["kind"] for item in served["items"]] == kinds


def test_a_region_note_persists_shows_in_the_review_page_and_is_listed_in_the_report(reviewed, tmp_path,
                                                                                       language_provider):
    workflow, project = reviewed
    note = workflow.add_note(project["id"], 0, [40, 60, 200, 120], "  Zkontrolovat jméno v tiráži. ")

    assert (note["kind"], note["page"], note["bbox"], note["text"]) == ("note", 0, [40, 60, 200, 120],
                                                                         "Zkontrolovat jméno v tiráži.")
    restarted = Workflow(ProjectStore(tmp_path / "library"), language=language_provider)
    assert restarted.review_page(project["id"], 0)["notes"] == [note]
    assert restarted.review_queue(project["id"])["counts"]["corrections"] == 0  # a note changes no word
    edited = restarted.edit_note(project["id"], note["id"], "Jméno ověřit v kronice.")
    assert edited["text"] == "Jméno ověřit v kronice." and edited["created"] == note["created"]
    assert workflow.rerun_impact(project["id"], "reconstruct")["corrections"] == 1

    workflow.export(project["id"])

    assert workflow.report(project["id"])["sections"]["review"]["notes"] == [edited]
    assert "page 1, region [40, 60, 200, 120]: 'Jméno ověřit v kronice.'" in workflow.report_summary(project["id"])
    workflow.revert_correction(project["id"], note["id"])
    assert workflow.review_page(project["id"], 0)["notes"] == []


def test_a_region_note_needs_text_and_a_region_on_the_page(reviewed):
    workflow, project = reviewed
    with pytest.raises(ValueError, match="Poznámka potřebuje text."):
        workflow.add_note(project["id"], 0, [40, 60, 200, 120], " ")
    with pytest.raises(ValueError, match="Oblast poznámky"):
        workflow.add_note(project["id"], 0, [200, 60, 40, 120], "Obráceně")
    with pytest.raises(ValueError, match="Oblast poznámky"):
        workflow.add_note(project["id"], 0, [40, 60, 2000, 120], "Mimo stránku")
    with pytest.raises(KeyError):
        workflow.add_note(project["id"], 5, [40, 60, 200, 120], "Jiná stránka")
    with pytest.raises(KeyError):
        workflow.edit_note(project["id"], "neexistuje", "Text")


def test_the_api_adds_edits_and_removes_region_notes(reviewed):
    workflow, project = reviewed
    client, base = client_for(workflow, project)

    added = client.post(f"{base}/review/notes", json={"page": 0, "bbox": [40, 60, 200, 120], "text": "Skvrna"})

    assert added.status_code == 200
    note = added.json()["note"]
    assert client.get(f"{base}/review/pages/0").json()["notes"] == [note]
    assert client.put(f"{base}/review/notes/{note['id']}", json={"text": "Skvrna od kávy"}).json()["note"][
        "text"] == "Skvrna od kávy"
    assert client.post(f"{base}/review/notes", json={"page": 0, "text": "Bez oblasti"}).status_code == 422
    assert client.delete(f"{base}/corrections/{note['id']}").status_code == 200
    assert client.get(f"{base}/review/pages/0").json()["notes"] == []


@pytest.fixture(scope="module")
def two_articles(tmp_path_factory):
    """One recognised two-column page (real Tesseract) whose AI reading order is article A, then article B."""
    from fakes import default_provider
    from layout_pdfs import CROSSING, printed_pdf
    from test_reading_order import article, numbers_by_key

    def by_article(request):
        number = numbers_by_key(request, CROSSING)
        return {"articles": [article(number["A-head"], number["A1"], number["A2"]),
                             article(number["B-head"], number["B1"], number["B2"])]}
    directory = tmp_path_factory.mktemp("articles")
    workflow = Workflow(ProjectStore(directory / "library"), language=default_provider().on("reading_order", by_article))
    project = workflow.run(workflow.create(printed_pdf(directory / "zpravodaj.pdf", [CROSSING]))["id"])
    return workflow, project


def headings(workflow, project) -> list[str]:
    return [line[0]["text"] for item in workflow.review_page(project["id"], 0)["articles"]
            for block in item["blocks"] if block["kind"] == "heading" for line in block["lines"][:1]]


def test_an_article_order_kept_by_an_older_project_is_ignored(two_articles):
    """Reading order is decided in Layout only (#21): manual review no longer reorders articles, and a manual
    article order an older project stored neither moves an article nor counts as a correction."""
    workflow, project = two_articles
    automatic = [item["id"] for item in workflow.review_page(project["id"], 0)["articles"]]
    assert len(automatic) == 2 and headings(workflow, project) == ["Oprava", "Nové"]
    workflow.store.write_corrections(project["id"], [
        {"id": "0123456789ab", "kind": "order", "page": 0, "articles": automatic[::-1], "previous": automatic,
         "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00"}])

    assert workflow.corrections(project["id"]) == []
    assert headings(workflow, project) == ["Oprava", "Nové"]
    assert workflow.rerun_impact(project["id"], "order")["corrections"] == 0
    workflow.export(project["id"])
    _, markdown = exported_text(workflow, project)
    assert markdown.index("## Oprava školy") < markdown.index("## Nové knihy")
    assert "0 manual corrections (0 word texts, 0 region notes)" in workflow.report_summary(project["id"])
    client, base = client_for(workflow, project)
    assert client.put(f"{base}/review/pages/0/order", json={"articles": automatic[::-1]}).status_code in (404, 405)
