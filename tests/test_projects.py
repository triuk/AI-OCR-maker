"""Seam 1: creating, listing, persisting and deleting projects through the project workflow interface."""
from __future__ import annotations

import pytest

from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow


def test_a_project_created_from_a_source_pdf_is_listed(workflow, source_pdf):
    project = workflow.create(source_pdf)

    listed = workflow.list()
    assert [p["id"] for p in listed] == [project["id"]]
    assert listed[0]["name"] == "zpravodaj-2000-05"
    assert listed[0]["source"]["file_name"] == "zpravodaj-2000-05.pdf"
    assert listed[0]["source"]["pages"] == 2


def test_projects_persist_across_a_restart(workflow, library, source_pdf):
    project = workflow.create(source_pdf, name="Zpravodaj 5/2000")

    restarted = Workflow(ProjectStore(library))

    assert [(p["id"], p["name"]) for p in restarted.list()] == [(project["id"], "Zpravodaj 5/2000")]
    assert restarted.get(project["id"])["source"]["pages"] == 2


def test_the_source_pdf_is_never_modified(workflow, source_pdf):
    before = (source_pdf.read_bytes(), source_pdf.stat().st_mtime_ns)

    project = workflow.create(source_pdf)
    workflow.delete(project["id"])

    assert (source_pdf.read_bytes(), source_pdf.stat().st_mtime_ns) == before


def test_a_deleted_project_is_gone_also_after_a_restart(workflow, library, source_pdf):
    kept = workflow.create(source_pdf, name="Ponechaný")
    deleted = workflow.create(source_pdf, name="Smazaný")

    workflow.delete(deleted["id"])

    assert [p["id"] for p in workflow.list()] == [kept["id"]]
    with pytest.raises(KeyError):
        workflow.get(deleted["id"])
    assert [p["id"] for p in Workflow(ProjectStore(library)).list()] == [kept["id"]]


def test_projects_are_listed_newest_first(workflow, source_pdf):
    first = workflow.create(source_pdf, name="První")
    second = workflow.create(source_pdf, name="Druhý")

    assert [p["id"] for p in workflow.list()] == [second["id"], first["id"]]


def test_a_new_project_shows_all_ten_steps_pending_in_workflow_order(workflow, source_pdf):
    project = workflow.create(source_pdf)

    assert [(s["label"], s["state"]) for s in project["steps"]] == [
        ("Import", "pending"), ("Příprava obrazu", "pending"), ("Lokální OCR", "pending"),
        ("Rozvržení", "pending"), ("Pořadí čtení", "pending"), ("Detekce problémů", "pending"),
        ("AI rekonstrukce", "pending"), ("Dohledání na webu", "pending"), ("Ruční kontrola", "pending"),
        ("Export", "pending")]


def test_a_file_that_is_not_a_pdf_is_refused_and_creates_nothing(workflow, tmp_path):
    fake = tmp_path / "scan.pdf"
    fake.write_bytes(b"not a pdf at all")

    with pytest.raises(ValueError, match="PDF"):
        workflow.create(fake)
    assert workflow.list() == []


def test_a_password_protected_pdf_is_refused(workflow, source_pdf, tmp_path):
    import pymupdf
    locked = tmp_path / "locked.pdf"
    with pymupdf.open(source_pdf) as document:
        document.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="tajne", owner_pw="tajne")

    with pytest.raises(ValueError, match="heslem"):
        workflow.create(locked)


def test_document_language_defaults_to_czech_and_can_be_changed(workflow, library, source_pdf):
    project = workflow.create(source_pdf)
    assert project["settings"]["language"] == "ces"

    workflow.update_settings(project["id"], {"language": "slk"})

    assert Workflow(ProjectStore(library)).get(project["id"])["settings"]["language"] == "slk"


def test_an_unknown_document_language_is_refused(workflow, source_pdf):
    project = workflow.create(source_pdf)

    with pytest.raises(ValueError, match="jazyk"):
        workflow.update_settings(project["id"], {"language": "xx"})
    assert workflow.get(project["id"])["settings"]["language"] == "ces"


def test_an_unknown_project_id_is_reported_as_missing(workflow):
    with pytest.raises(KeyError):
        workflow.get("../../etc")
