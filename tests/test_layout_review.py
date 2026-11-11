"""Seams 1 and 2 with real Tesseract: layout rounds (ADR 0006). A run stops at Layout until the person approves the
layout regions; between rounds the person edits them, and a round sends the AI only the pages with an unlocked
region or a layout note, keeping the locked regions exactly."""
from __future__ import annotations

import pytest

from fakes import ScriptedProvider
from layout_pdfs import CROSSING, printed_pdf
from ocrmaker import regions
from ocrmaker.errors import ConfirmationRequired
from ocrmaker.steps import implementations
from ocrmaker.steps.regions_step import REGIONS_FILE
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow
from test_regions import READING, grid

LOCAL = ("import", "prepare", "regions", "ocr")


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    return printed_pdf(tmp_path_factory.mktemp("rounds") / "zpravodaj-2000-05.pdf", [CROSSING, CROSSING])


def started(tmp_path, pdf, *answers, steps=LOCAL, settings=None):
    """A project of the two crossing pages run with layout regions and scripted AI layout answers."""
    provider = ScriptedProvider().on(regions.PURPOSE, *(answers or (READING,)))
    shipped = implementations()
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=provider,
                        steps={step: shipped[step] for step in steps})
    project = workflow.create(pdf)
    workflow.update_settings(project["id"], {"layout_method": "regions", **(settings or {})})
    return workflow, provider, workflow.run(project["id"])


def states(project) -> dict:
    return {step["id"]: step["state"] for step in project["steps"]}


def boxes(parts) -> list:
    return [region["box"] for part in parts for region in part["regions"]]


def test_a_run_stops_at_layout_until_the_person_approves_it(tmp_path, pdf):
    workflow, provider, project = started(tmp_path, pdf)

    assert states(project)["regions"] == "done" and states(project)["ocr"] == "pending"
    assert project["layout"] == "waiting"
    view = workflow.layout(project["id"])
    assert view["state"] == "waiting" and view["process"] == []
    assert [len(page["parts"]) for page in view["pages"]] == [2, 2]
    first = view["pages"][0]
    assert [part["kind"] for part in first["parts"]] == ["article", "article"]
    assert all(region["locked"] for part in first["parts"] for region in part["regions"])
    assert not first["edited"] and first["note"] == "" and first["round"] == 1
    asked = len(provider.requests)

    # running again neither asks the AI again nor goes past Layout
    project = workflow.run(project["id"])
    assert states(project)["ocr"] == "pending" and len(provider.requests) == asked

    project = workflow.approve_layout(project["id"])
    assert project["layout"] == "approved" and states(project)["ocr"] == "done"
    assert len(provider.requests) == asked


def test_the_setting_approves_the_layout_at_once(tmp_path, pdf):
    workflow, _, project = started(tmp_path, pdf, settings={"layout_auto_approve": True})

    assert project["layout"] == "approved" and states(project)["ocr"] == "done"


def test_approval_takes_the_layout_as_the_person_drew_it(tmp_path, pdf):
    workflow, provider, project = started(tmp_path, pdf)
    pid = project["id"]
    parts = workflow.layout(pid)["pages"][0]["parts"]
    # the person widens the last region by hand and says the second part is an advertisement
    widened = [value + delta for value, delta in zip(parts[1]["regions"][1]["box"], (-10, -10, 10, 10))]
    parts[1]["regions"][1]["box"] = widened
    parts[1]["kind"] = "ad"

    page = workflow.edit_layout(pid, 0, parts)
    assert page["edited"] and page["parts"][1]["kind"] == "ad" and not page["process"]
    asked = len(provider.requests)
    project = workflow.approve_layout(pid)

    assert states(project)["ocr"] == "done" and len(provider.requests) == asked
    first, second = workflow.result(pid, "regions").read_json(REGIONS_FILE)["pages"]
    assert first["source"] == "manual" and second["source"] == "ai"
    assert [region["kind"] for region in first["regions"]] == ["article", "article", "ad", "ad"]
    # kept as drawn, not snapped back to the print (the grid is a thousandth of the page on each axis)
    width, height = 420, 595
    assert first["regions"][3]["bbox"] == pytest.approx(
        [v * size / 1000 for v, size in zip(widened, (width, height) * 2)], abs=0.01)
    assert [region["kind"] for region in first["proposal"]] == ["article"] * 4
    assert workflow.layout(pid)["pages"][0]["edited"]


def kept(*ids) -> list[dict]:
    return [{"keep": id, "box": [0, 0, 0, 0], "orientation": "0"} for id in ids]


def test_a_round_redoes_only_the_pages_the_person_unlocked_and_keeps_their_locked_regions(tmp_path, pdf):
    workflow, provider, project = started(tmp_path, pdf)
    pid = project["id"]
    before = workflow.result(pid, "regions").read_json(REGIONS_FILE)["pages"]
    parts = workflow.layout(pid)["pages"][0]["parts"]
    parts[1]["regions"][1]["locked"] = False
    workflow.edit_layout(pid, 0, parts)
    workflow.layout_note(pid, 0, "2b je inzerát")
    assert workflow.layout(pid)["process"] == [0]
    provider.on(regions.REDO_PURPOSE, {"parts": [
        {"kind": "article", "title": "Oprava školy", "regions": kept("1a", "1b")},
        {"kind": "article", "title": "Nové knihy", "regions": kept("2a")},
        {"kind": "ad", "title": "Čtenáři", "regions": [{"keep": "", "box": grid("B2"), "orientation": "0"}]}]})

    project = workflow.process_layout(pid)

    [request] = provider.requests_for(regions.REDO_PURPOSE)
    assert request.data["note"] == "2b je inzerát" and len(request.images) == 1
    current = [region for part in request.data["current_layout"]["parts"] for region in part["regions"]]
    assert [(region["id"], region["locked"]) for region in current] == [
        ("1a", True), ("1b", True), ("2a", True), ("2b", False)]
    after = workflow.result(pid, "regions").read_json(REGIONS_FILE)
    first, second = after["pages"]
    assert after["round"] == 2 and first["round"] == 2 and second == before[1]
    # the locked regions exactly as they were, the new one snapped to the print
    assert [region["bbox"] for region in first["regions"][:3]] == [region["bbox"] for region in before[0]["regions"][:3]]
    assert [(region["article"], region["kind"]) for region in first["regions"]] == [
        (1, "article"), (1, "article"), (2, "article"), (3, "ad")]
    assert first["regions"][3]["bbox"][0] > before[0]["regions"][3]["bbox"][0] - 1
    assert project["layout"] == "waiting" and states(project)["ocr"] == "pending"
    # the round took the edit and its note: the page is the AI's again, every region locked
    page = workflow.layout(pid)["pages"][0]
    assert page["round"] == 2 and not page["edited"] and page["note"] == "" and page["sent_note"] == "2b je inzerát"
    assert all(region["locked"] for part in page["parts"] for region in part["regions"])
    with pytest.raises(ValueError, match="Není co zpracovat"):
        workflow.process_layout(pid)


def test_an_answer_that_breaks_a_lock_is_refused_and_the_page_waits_for_the_next_round(tmp_path, pdf):
    workflow, provider, project = started(tmp_path, pdf)
    pid = project["id"]
    before = workflow.result(pid, "regions").read_json(REGIONS_FILE)["pages"]
    # a note and nothing unlocked: the AI may only add regions, but it drops the locked 2b, twice
    workflow.layout_note(pid, 0, "Chybí podpis pod článkem 2.")
    broken = {"parts": [{"kind": "article", "title": "Oprava školy", "regions": kept("1a", "1b")},
                        {"kind": "article", "title": "Nové knihy", "regions": kept("2a")}]}
    provider.on(regions.REDO_PURPOSE, broken)

    project = workflow.process_layout(pid)

    first, fix = provider.requests_for(regions.REDO_PURPOSE)
    assert fix.data["task"] == "Correct your previous answer."
    assert any("2b" in problem and "missing" in problem for problem in fix.data["problems"])
    after = workflow.result(pid, "regions").read_json(REGIONS_FILE)
    assert after["pages"] == before and after["kept"][0]["index"] == 0
    assert states(project)["regions"] == "warning" and "strana zůstala" in next(
        step["message"] for step in project["steps"] if step["id"] == "regions")
    view = workflow.layout(pid)
    assert view["process"] == [0] and view["pages"][0]["note"] == "Chybí podpis pod článkem 2."

    # the next round sends the page again; an answer that keeps every lock is taken, the regions as they were
    provider.on(regions.REDO_PURPOSE, {"parts": [
        {"kind": "article", "title": "Oprava školy", "regions": kept("1a", "1b")},
        {"kind": "article", "title": "Nové knihy", "regions": kept("2a", "2b")}]})
    workflow.process_layout(pid)

    assert len(provider.requests_for(regions.REDO_PURPOSE)) == 3
    again = workflow.result(pid, "regions").read_json(REGIONS_FILE)
    # the refused round counted too: this is the third
    assert again["kept"] == [] and again["round"] == again["pages"][0]["round"] == 3
    assert again["pages"][1] == before[1]
    assert [region["bbox"] for region in again["pages"][0]["regions"]] == \
        [region["bbox"] for region in before[0]["regions"]]
    assert workflow.layout(pid)["process"] == []


CORRECTIONS = [{"id": "c1", "kind": "note", "page": 0, "bbox": [40, 40, 90, 60], "text": "razítko", "created": "x"}]


def test_editing_after_approval_asks_to_discard_the_corrections_and_makes_the_later_steps_stale(tmp_path, pdf):
    workflow, provider, project = started(tmp_path, pdf, settings={"layout_auto_approve": True})
    pid = project["id"]
    ProjectStore(tmp_path / "library").write_corrections(pid, CORRECTIONS)
    assert workflow.layout(pid)["discards"] == 1
    parts = workflow.layout(pid)["pages"][1]["parts"]
    parts[0]["regions"][0]["locked"] = False

    with pytest.raises(ConfirmationRequired) as refused:
        workflow.edit_layout(pid, 1, parts)
    assert refused.value.corrections == 1 and workflow.get(pid)["layout"] == "approved"

    workflow.edit_layout(pid, 1, parts, discard_corrections=1)

    project = workflow.get(pid)
    assert project["layout"] == "waiting" and states(project)["ocr"] == "pending"
    assert states(project)["regions"] == "done" and workflow.corrections(pid) == []
    assert workflow.layout(pid)["process"] == [1] and workflow.layout(pid)["discards"] == 0


def test_running_layout_again_starts_from_the_ai_without_the_edits(tmp_path, pdf):
    workflow, provider, project = started(tmp_path, pdf)
    pid = project["id"]
    workflow.layout_note(pid, 1, "Záhlaví chybí.")

    project = workflow.rerun(pid, "regions")

    assert len(provider.requests_for(regions.PURPOSE)) == 4 and not provider.requests_for(regions.REDO_PURPOSE)
    assert project["layout"] == "waiting" and workflow.layout(pid)["pages"][1]["note"] == ""


@pytest.fixture
def step_implementations():
    shipped = implementations()
    return {step: shipped[step] for step in ("import", "prepare", "regions")}


@pytest.fixture
def language_provider():
    return ScriptedProvider().on(regions.PURPOSE, READING).on(regions.REDO_PURPOSE, {"parts": [
        {"kind": "article", "title": "Oprava školy", "regions": kept("1a", "1b")},
        {"kind": "article", "title": "Nové knihy", "regions": kept("2a", "2b")}]})


def test_the_layout_editor_api_saves_edits_and_runs_rounds_and_approval(client, workflow, pdf):
    with open(pdf, "rb") as source:
        pid = client.post("/api/projects", files={"source": ("zpravodaj.pdf", source, "application/pdf")}).json()["id"]
    client.put(f"/api/projects/{pid}/settings", json={"layout_method": "regions"})
    assert client.post(f"/api/projects/{pid}/run").status_code == 202
    workflow.wait(pid, timeout=120)
    view = client.get(f"/api/projects/{pid}/layout").json()
    assert view["state"] == "waiting" and len(view["pages"]) == 2
    parts = view["pages"][0]["parts"]
    locked = boxes(parts)
    parts[0]["regions"][1]["locked"] = False

    saved = client.put(f"/api/projects/{pid}/layout/pages/0", json={"parts": parts})
    assert saved.status_code == 200 and saved.json()["process"]
    assert client.put(f"/api/projects/{pid}/layout/pages/1/note", json={"text": "Bez záhlaví."}).status_code == 200
    assert client.put(f"/api/projects/{pid}/layout/pages/0", json={"parts": [{"kind": "poem"}]}).status_code == 422
    assert client.post(f"/api/projects/{pid}/layout/process").status_code == 202
    workflow.wait(pid, timeout=120)

    view = client.get(f"/api/projects/{pid}/layout").json()
    assert view["round"] == 2 and view["process"] == [] and boxes(view["pages"][0]["parts"]) == locked
    assert client.post(f"/api/projects/{pid}/layout/process").status_code == 422
    assert client.post(f"/api/projects/{pid}/layout/approve").status_code == 202
    workflow.wait(pid, timeout=120)
    assert client.get(f"/api/projects/{pid}").json()["layout"] == "approved"
    ProjectStore(workflow.store.root).write_corrections(pid, CORRECTIONS)
    refused = client.put(f"/api/projects/{pid}/layout/pages/1/note", json={"text": "Ještě jednou."})
    assert refused.status_code == 409 and refused.json()["corrections"] == 1
    confirmed = client.put(f"/api/projects/{pid}/layout/pages/1/note",
                           json={"text": "Ještě jednou.", "discard_corrections": 1})
    assert confirmed.status_code == 200 and client.get(f"/api/projects/{pid}").json()["layout"] == "waiting"
