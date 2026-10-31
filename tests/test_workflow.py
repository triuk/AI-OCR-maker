"""Seam 1: running the workflow, step states, results, diagnostics, the project lock, stop and rerun."""
from __future__ import annotations

import threading
import time

import pytest

from fakes import BlockingStep, RecordingStep, ScriptedProvider
from ocrmaker.errors import ConfirmationRequired, Conflict
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow


@pytest.fixture
def make_workflow(library):
    def make(*steps, language=None):
        return Workflow(ProjectStore(library), steps={step.id: step for step in steps}, language=language)
    return make


def states(project) -> dict:
    return {step["id"]: step["state"] for step in project["steps"]}


def background(fn, *args):
    thread = threading.Thread(target=fn, args=args, daemon=True)
    thread.start()
    return thread


def test_a_run_executes_implemented_steps_in_order_and_skips_missing_ones(make_workflow, source_pdf):
    workflow = make_workflow(RecordingStep("import"), RecordingStep("ocr", reads=("import",)))
    project = workflow.create(source_pdf)

    finished = workflow.run(project["id"])

    assert states(finished) == {"import": "done", "prepare": "pending", "ocr": "done", "layout": "pending",
                                "order": "pending", "problems": "pending", "reconstruct": "pending",
                                "lookup": "pending", "review": "pending", "export": "pending"}
    ocr = workflow.result(project["id"], "ocr").read_json("result.json")
    assert ocr["seen"] == {"import": {"step": "import", "run": 1, "seen": {}, "language": "ces"}}


def test_a_run_stops_at_manual_review_and_export_continues_from_there(make_workflow, source_pdf):
    review, export = RecordingStep("review"), RecordingStep("export", reads=("import",))
    workflow = make_workflow(RecordingStep("import"), review, export)
    project = workflow.create(source_pdf)

    after_run = workflow.run(project["id"])
    after_export = workflow.export(project["id"])

    assert (states(after_run)["review"], states(after_run)["export"]) == ("pending", "pending")
    assert (states(after_export)["review"], states(after_export)["export"]) == ("done", "done")


def test_without_manual_review_a_run_goes_through_to_export(make_workflow, source_pdf):
    workflow = make_workflow(RecordingStep("import"), RecordingStep("export"))
    project = workflow.create(source_pdf)

    assert states(workflow.run(project["id"]))["export"] == "done"


def test_export_before_the_automatic_steps_finished_is_refused(make_workflow, source_pdf):
    workflow = make_workflow(RecordingStep("import"), RecordingStep("review"), RecordingStep("export"))
    project = workflow.create(source_pdf)

    with pytest.raises(ValueError, match="Nejprve"):
        workflow.export(project["id"])


def test_a_run_with_no_implemented_step_is_refused_in_czech(make_workflow, source_pdf):
    workflow = make_workflow()
    project = workflow.create(source_pdf)

    with pytest.raises(ValueError, match="krok"):
        workflow.run(project["id"])


def test_a_failed_step_shows_its_message_keeps_earlier_results_and_stops_the_run(make_workflow, source_pdf):
    later = RecordingStep("layout")
    workflow = make_workflow(RecordingStep("import"), RecordingStep("ocr", failure="Tesseract selhal."), later)
    project = workflow.create(source_pdf)

    finished = workflow.run(project["id"])

    ocr = next(step for step in finished["steps"] if step["id"] == "ocr")
    assert (ocr["state"], ocr["message"]) == ("failed", "Tesseract selhal.")
    assert workflow.result(project["id"], "import").read_json("result.json")["step"] == "import"
    assert workflow.result(project["id"], "ocr") is None
    assert later.calls == 0


def test_an_unexpected_error_fails_the_step_with_a_czech_message(make_workflow, source_pdf):
    workflow = make_workflow(RecordingStep("import", crash=True))
    project = workflow.create(source_pdf)

    step = workflow.run(project["id"])["steps"][0]

    assert step["state"] == "failed"
    assert step["message"].startswith("Krok skončil neočekávanou chybou")


def test_a_failed_step_runs_again_on_the_next_run(make_workflow, source_pdf):
    flaky = RecordingStep("import", failure="Dočasná chyba.")
    workflow = make_workflow(flaky)
    project = workflow.create(source_pdf)
    workflow.run(project["id"])

    flaky.failure = None
    step = workflow.run(project["id"])["steps"][0]

    assert (step["state"], step["message"]) == ("done", None)


def test_a_warning_marks_the_step_done_with_a_warning(make_workflow, source_pdf):
    workflow = make_workflow(RecordingStep("import", warning="Stránka 2 je prázdná."))
    project = workflow.create(source_pdf)

    step = workflow.run(project["id"])["steps"][0]

    assert (step["state"], step["message"]) == ("warning", "Stránka 2 je prázdná.")


def test_done_steps_are_not_run_again(make_workflow, source_pdf):
    first = RecordingStep("import")
    workflow = make_workflow(first)
    project = workflow.create(source_pdf)

    workflow.run(project["id"])
    workflow.run(project["id"])

    assert first.calls == 1


def test_diagnostics_record_each_steps_start_end_duration_and_outcome(make_workflow, source_pdf):
    workflow = make_workflow(RecordingStep("import"), RecordingStep("ocr", failure="Chyba."))
    project = workflow.create(source_pdf)
    workflow.run(project["id"])

    records = [r for r in workflow.diagnostics(project["id"]) if r["kind"] == "step"]

    assert [(r["step"], r["outcome"]) for r in records] == [("import", "done"), ("ocr", "failed")]
    for record in records:
        assert record["started"] <= record["finished"]
        assert record["duration"] >= 0
        assert record["run_id"]
    assert records[1]["message"] == "Chyba."


def test_step_states_and_results_persist_across_a_restart(make_workflow, library, source_pdf):
    workflow = make_workflow(RecordingStep("import", overlay={"boxes": [[0, 0, 10, 10]]}))
    project = workflow.create(source_pdf)
    workflow.run(project["id"])

    restarted = make_workflow(RecordingStep("import"))

    assert states(restarted.get(project["id"]))["import"] == "done"
    assert restarted.overlay(project["id"], "import", 0) == {"boxes": [[0, 0, 10, 10]]}


def test_a_step_preview_overlay_exists_only_for_finished_steps_and_stored_pages(make_workflow, source_pdf):
    workflow = make_workflow(RecordingStep("import", overlay={"pages": 2}), RecordingStep("ocr"))
    project = workflow.create(source_pdf)
    workflow.run(project["id"])

    assert workflow.overlay(project["id"], "import", 0) == {"pages": 2}
    assert workflow.overlay(project["id"], "import", 1) is None
    assert workflow.overlay(project["id"], "ocr", 0) is None
    assert workflow.overlay(project["id"], "layout", 0) is None


def test_the_project_lock_prevents_a_second_concurrent_run(make_workflow, library, source_pdf):
    blocking = BlockingStep("import")
    workflow = make_workflow(blocking)
    project = workflow.create(source_pdf)
    first = background(workflow.run, project["id"])
    assert blocking.entered.wait(5)

    with pytest.raises(Conflict):
        workflow.run(project["id"])
    # A second application instance over the same library is held off too.
    with pytest.raises(Conflict):
        make_workflow(BlockingStep("import")).run(project["id"])
    with pytest.raises(Conflict):
        workflow.delete(project["id"])
    assert workflow.get(project["id"])["running"] is True

    blocking.release.set()
    first.join(5)
    assert states(workflow.get(project["id"]))["import"] == "done"
    assert workflow.get(project["id"])["running"] is False


def test_runs_of_different_projects_do_not_block_each_other(make_workflow, source_pdf):
    blocking = BlockingStep("import")
    workflow = make_workflow(blocking)
    busy = workflow.create(source_pdf)
    other = workflow.create(source_pdf)
    thread = background(workflow.run, busy["id"])
    assert blocking.entered.wait(5)

    workflow.delete(other["id"])

    blocking.release.set()
    thread.join(5)


def test_a_started_run_continues_in_the_background(make_workflow, source_pdf):
    blocking = BlockingStep("import")
    workflow = make_workflow(blocking)
    project = workflow.create(source_pdf)

    started = workflow.start(project["id"])

    assert started["running"] is True
    assert blocking.entered.wait(5)
    assert states(workflow.get(project["id"]))["import"] == "running"
    with pytest.raises(Conflict):
        workflow.start(project["id"])
    blocking.release.set()
    workflow.wait(project["id"], timeout=5)
    assert states(workflow.get(project["id"]))["import"] == "done"


def test_stopping_a_run_returns_the_interrupted_step_to_pending(make_workflow, source_pdf):
    blocking = BlockingStep("ocr")
    workflow = make_workflow(RecordingStep("import"), blocking)
    project = workflow.create(source_pdf)
    workflow.start(project["id"])
    assert blocking.entered.wait(5)

    workflow.stop(project["id"])
    workflow.wait(project["id"], timeout=5)

    stopped = workflow.get(project["id"])
    assert (states(stopped)["import"], states(stopped)["ocr"]) == ("done", "pending")
    assert workflow.result(project["id"], "ocr") is None
    assert [r["outcome"] for r in workflow.diagnostics(project["id"]) if r["kind"] == "step"] == [
        "done", "interrupted"]


def test_a_step_left_running_by_a_crash_is_pending_after_a_restart(make_workflow, library, source_pdf):
    workflow = make_workflow(RecordingStep("import"))
    project = workflow.create(source_pdf)

    def crashed(document):
        document["steps"]["import"]["state"] = "running"
    ProjectStore(library).mutate(project["id"], crashed)

    assert states(make_workflow(RecordingStep("import")).get(project["id"]))["import"] == "pending"


def test_a_rerun_invalidates_the_step_and_every_later_step_and_runs_them_again(make_workflow, source_pdf):
    imported, ocr = RecordingStep("import"), RecordingStep("ocr", reads=("import",))
    workflow = make_workflow(imported, ocr, RecordingStep("review"))
    project = workflow.create(source_pdf)
    workflow.run(project["id"])

    assert workflow.rerun_impact(project["id"], "import") == {"corrections": 0, "steps": [
        "import", "prepare", "ocr", "layout", "order", "problems", "reconstruct", "lookup", "review", "export"]}
    rerun = workflow.rerun(project["id"], "import")

    assert (imported.calls, ocr.calls) == (2, 2)
    assert workflow.result(project["id"], "ocr").read_json("result.json")["seen"]["import"]["run"] == 2
    assert states(rerun)["review"] == "pending"


def test_a_rerun_that_would_discard_manual_corrections_needs_their_count_confirmed(make_workflow, library,
                                                                                    source_pdf):
    workflow = make_workflow(RecordingStep("import"), RecordingStep("review"))
    project = workflow.create(source_pdf)
    workflow.run(project["id"])
    ProjectStore(library).write_corrections(project["id"], [{"word": "p1-w3", "text": "Rychvald"},
                                                            {"word": "p1-w9", "text": "obec"}])

    assert workflow.rerun_impact(project["id"], "import")["corrections"] == 2
    with pytest.raises(ConfirmationRequired) as refused:
        workflow.rerun(project["id"], "import")
    assert refused.value.corrections == 2

    workflow.rerun(project["id"], "import", discard_corrections=2)
    assert workflow.rerun_impact(project["id"], "import")["corrections"] == 0


def test_rerunning_export_keeps_manual_corrections(make_workflow, library, source_pdf):
    export = RecordingStep("export")
    workflow = make_workflow(RecordingStep("import"), RecordingStep("review"), export)
    project = workflow.create(source_pdf)
    workflow.run(project["id"])
    workflow.export(project["id"])
    ProjectStore(library).write_corrections(project["id"], [{"word": "p1-w3", "text": "Rychvald"}])

    assert workflow.rerun_impact(project["id"], "export") == {"corrections": 0, "steps": ["export"]}
    workflow.rerun(project["id"], "export")

    assert export.calls == 2
    assert len(ProjectStore(library).corrections(project["id"])) == 1


def test_steps_receive_the_project_settings_and_the_language_provider(make_workflow, source_pdf):
    seen = {}

    class Probe(RecordingStep):
        def run(self, ctx):
            seen.update(language=ctx.settings["language"], answer=ctx.language.ask("Say hi.", purpose="probe").text)
            super().run(ctx)

    provider = ScriptedProvider().on("probe", "hi")
    workflow = make_workflow(Probe("import"), language=provider)
    project = workflow.create(source_pdf, settings={"language": "eng"})
    workflow.run(project["id"])

    assert seen == {"language": "eng", "answer": "hi"}
    assert [request.purpose for request in provider.requests] == ["probe"]


class PagedStep(BlockingStep):
    """Works through four pages of 0.3 s each, reporting progress, and waits after the first one."""

    def run(self, ctx):
        ctx.progress(0, 4, "Stránka 1 z 4")
        time.sleep(0.3)
        ctx.progress(1, 4, "Stránka 2 z 4")
        super().run(ctx)


def test_a_running_step_shows_roughly_how_long_is_left(make_workflow, source_pdf, client):
    paged = PagedStep("import")
    workflow = make_workflow(paged)
    project = workflow.create(source_pdf)

    workflow.start(project["id"])
    assert paged.entered.wait(5)
    progress = next(step for step in workflow.get(project["id"])["steps"] if step["id"] == "import")["progress"]
    paged.release.set()
    workflow.wait(project["id"], timeout=5)

    assert (progress["done"], progress["total"]) == (1, 4)
    assert 0.8 <= progress["remaining"] <= 1.5  # three more pages at about 0.3 s each
    assert "zbývá" in client.get("/app.js").text
