"""Seam 1: rerunning an earlier step (a clean slate, ADR 0004) and stopping a running workflow."""
from __future__ import annotations

import pytest

from fakes import BlockingStep, RecordingStep
from ocrmaker.errors import ConfirmationRequired, Conflict
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow

# Manual corrections of every kind count alike; the rerun discards each of them.
CORRECTIONS = [
    {"id": "c1", "kind": "text", "page": 0, "ids": ["p1-w3"], "text": "Rychvald", "previous": "Rychva1d"},
    {"id": "c2", "kind": "note", "page": 0, "box": [10, 10, 80, 40], "text": "Razítko obecního úřadu"},
    {"id": "c3", "kind": "article_order", "page": None, "articles": ["a2", "a1"]},
]


@pytest.fixture
def make_workflow(library):
    def make(*steps):
        return Workflow(ProjectStore(library), steps={step.id: step for step in steps})
    return make


def states(project) -> dict:
    return {step["id"]: step["state"] for step in project["steps"]}


def run_of(workflow, project_id, step_id):
    """Which run of the step produced its committed result, or None when it has none."""
    result = workflow.result(project_id, step_id)
    return result.read_json("result.json")["run"] if result else None


def seen_run(workflow, project_id, step_id, read_id):
    """Which run of an earlier step the step's committed result was built from."""
    return workflow.result(project_id, step_id).read_json("result.json")["seen"][read_id]["run"]


def three_steps(make_workflow, source_pdf, *extra):
    steps = (RecordingStep("import"), RecordingStep("ocr", reads=("import",)),
             RecordingStep("layout", reads=("ocr",)), *extra)
    workflow = make_workflow(*steps)
    project = workflow.create(source_pdf)
    workflow.run(project["id"])
    return workflow, project["id"], steps


def test_rerunning_a_middle_step_keeps_earlier_results_and_replaces_the_later_ones(make_workflow, source_pdf):
    workflow, pid, _ = three_steps(make_workflow, source_pdf)

    rerun = workflow.rerun(pid, "ocr")

    assert states(rerun)["layout"] == "done"
    assert (run_of(workflow, pid, "import"), run_of(workflow, pid, "ocr"), run_of(workflow, pid, "layout")) == (1, 2, 2)
    assert seen_run(workflow, pid, "layout", "ocr") == 2


def test_a_rerun_that_fails_leaves_no_old_results_after_the_failed_step(make_workflow, source_pdf):
    workflow, pid, (_, ocr, _) = three_steps(make_workflow, source_pdf)
    ocr.failure = "Rozpoznávání selhalo."

    rerun = workflow.rerun(pid, "ocr")

    assert (states(rerun)["import"], states(rerun)["ocr"], states(rerun)["layout"]) == ("done", "failed", "pending")
    assert run_of(workflow, pid, "import") == 1
    assert (workflow.result(pid, "ocr"), workflow.result(pid, "layout")) == (None, None)


def test_rerunning_an_earlier_step_discards_manual_corrections_of_every_kind_once_confirmed(make_workflow, library,
                                                                                           source_pdf):
    workflow, pid, _ = three_steps(make_workflow, source_pdf, RecordingStep("review"))
    ProjectStore(library).write_corrections(pid, CORRECTIONS)

    assert workflow.rerun_impact(pid, "layout") == {"corrections": 3, "steps": [
        "layout", "order", "problems", "reconstruct", "lookup", "review", "export"]}
    workflow.rerun(pid, "layout", discard_corrections=3)

    assert workflow.corrections(pid) == []
    assert workflow.rerun_impact(pid, "layout")["corrections"] == 0
    assert run_of(workflow, pid, "layout") == 2


@pytest.mark.parametrize("confirmed", [None, 2, 4])
def test_a_rerun_without_the_right_confirmation_changes_nothing(make_workflow, library, source_pdf, confirmed):
    workflow, pid, _ = three_steps(make_workflow, source_pdf, RecordingStep("review"))
    ProjectStore(library).write_corrections(pid, CORRECTIONS)

    with pytest.raises(ConfirmationRequired) as refused:
        workflow.rerun(pid, "ocr", discard_corrections=confirmed)

    assert refused.value.corrections == 3
    assert str(refused.value) == "Opakování kroku zahodí 3 ruční opravy."
    assert len(workflow.corrections(pid)) == 3
    assert states(workflow.get(pid))["ocr"] == "done"
    assert (run_of(workflow, pid, "ocr"), run_of(workflow, pid, "layout")) == (1, 1)


def test_rerunning_manual_review_keeps_the_manual_corrections(make_workflow, library, source_pdf):
    workflow, pid, _ = three_steps(make_workflow, source_pdf, RecordingStep("review"), RecordingStep("export"))
    workflow.export(pid)
    ProjectStore(library).write_corrections(pid, CORRECTIONS)

    assert workflow.rerun_impact(pid, "review")["corrections"] == 0
    workflow.rerun(pid, "review")

    assert len(workflow.corrections(pid)) == 3
    assert (run_of(workflow, pid, "layout"), run_of(workflow, pid, "export")) == (1, 2)


def test_a_rerun_is_refused_while_the_project_runs(make_workflow, source_pdf):
    blocking = BlockingStep("ocr")
    workflow = make_workflow(RecordingStep("import"), blocking)
    project = workflow.create(source_pdf)
    workflow.start(project["id"])
    assert blocking.entered.wait(5)

    with pytest.raises(Conflict):
        workflow.rerun(project["id"], "import")

    blocking.release.set()
    workflow.wait(project["id"], timeout=5)


def test_a_busy_project_refuses_a_rerun_before_asking_to_confirm_discarding(make_workflow, library, source_pdf):
    # The count is checked under the run lock, so a confirmation never covers corrections it was not shown.
    blocking = BlockingStep("ocr")
    workflow = make_workflow(RecordingStep("import"), blocking)
    project = workflow.create(source_pdf)
    ProjectStore(library).write_corrections(project["id"], CORRECTIONS)
    workflow.start(project["id"])
    assert blocking.entered.wait(5)

    with pytest.raises(Conflict) as refused:
        workflow.rerun(project["id"], "import")

    assert not isinstance(refused.value, ConfirmationRequired)
    assert str(refused.value) == "Projekt se právě zpracovává. Nejprve zpracování zastavte."
    blocking.release.set()
    workflow.wait(project["id"], timeout=5)
    assert len(workflow.corrections(project["id"])) == 3


def test_stopping_a_rerun_keeps_the_results_finished_so_far_and_the_interrupted_step_pending(make_workflow,
                                                                                            source_pdf):
    imported, ocr, blocking = RecordingStep("import"), RecordingStep("ocr", reads=("import",)), BlockingStep("layout")
    workflow = make_workflow(imported, ocr, blocking, RecordingStep("order", reads=("layout",)))
    project = workflow.create(source_pdf)
    pid = project["id"]
    blocking.release.set()
    workflow.run(pid)
    blocking.release.clear()
    blocking.entered.clear()

    workflow.start(pid, "rerun", step_id="ocr")
    assert blocking.entered.wait(5)
    assert workflow.stop(pid) is True
    workflow.wait(pid, timeout=5)

    stopped = workflow.get(pid)
    assert stopped["running"] is False
    assert [states(stopped)[s] for s in ("import", "ocr", "layout", "order")] == ["done", "done", "pending", "pending"]
    assert (run_of(workflow, pid, "import"), run_of(workflow, pid, "ocr")) == (1, 2)
    assert (workflow.result(pid, "layout"), workflow.result(pid, "order")) == (None, None)


def test_a_run_after_a_stop_continues_from_the_interrupted_step(make_workflow, source_pdf):
    imported, blocking = RecordingStep("import"), BlockingStep("ocr")
    workflow = make_workflow(imported, blocking, RecordingStep("layout"))
    project = workflow.create(source_pdf)
    workflow.start(project["id"])
    assert blocking.entered.wait(5)
    workflow.stop(project["id"])
    workflow.wait(project["id"], timeout=5)

    blocking.release.set()
    resumed = workflow.run(project["id"])

    assert [states(resumed)[s] for s in ("import", "ocr", "layout")] == ["done", "done", "done"]
    assert imported.calls == 1


def test_stopping_when_nothing_runs_changes_nothing(make_workflow, source_pdf):
    workflow, pid, _ = three_steps(make_workflow, source_pdf)

    assert workflow.stop(pid) is False
    assert [states(workflow.get(pid))[s] for s in ("import", "ocr", "layout")] == ["done", "done", "done"]
