"""Seams 1 and 2: a source PDF that already has a text layer; an invisible one can be removed, a visible one not."""
from __future__ import annotations

import hashlib

import pymupdf
import pytest

from ocrmaker.words import page_words, read_page
from pdfs import scan_pdf, text_pdf


def step(project, step_id: str = "import") -> dict:
    return next(step for step in project["steps"] if step["id"] == step_id)


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invisible_layer_pdf(path, **options):
    return scan_pdf(path, pages=2, invisible_text="Rychva1dsky zpravodaj", **options)


def text_under_scan_pdf(path):
    return scan_pdf(path, text_under_scan="Rychva1dsky zpravodaj")


def text_in_form_pdf(path):
    """The invisible text layer drawn from a form XObject over the scan, as OCRmyPDF grafts it."""
    scan = pymupdf.open(scan_pdf(path.with_name("scan-only.pdf")))
    layer = pymupdf.open()
    layer.new_page(width=scan[0].rect.width, height=scan[0].rect.height).insert_text(
        (72, 100), "Rychva1dsky zpravodaj", fontsize=14, fontname="helv", render_mode=3)
    scan[0].show_pdf_page(scan[0].rect, layer, 0)
    scan.save(path)
    return path


HIDDEN_LAYERS = pytest.mark.parametrize("make", [invisible_layer_pdf, text_under_scan_pdf, text_in_form_pdf],
                                        ids=["render mode 3", "beneath the scan", "in a form xobject"])


@HIDDEN_LAYERS
def test_import_stopped_by_an_invisible_text_layer_offers_to_remove_it(workflow, tmp_path, make):
    project = workflow.run(workflow.create(make(tmp_path / "older-ocr.pdf"))["id"])

    stopped = step(project)
    assert stopped["state"] == "failed"
    assert "neviditelnou textovou vrstvu" in stopped["message"]
    assert stopped["action"] == "remove_text_layer"
    assert workflow.pages(project["id"]) == []
    assert all(s["state"] == "pending" for s in project["steps"] if s["id"] != "import")
    record = next(r for r in workflow.diagnostics(project["id"]) if r["kind"] == "import")
    assert (record["outcome"], record["text_layer"]) == ("text_layer", "invisible")
    assert record["text_layer_words"] > 0


def image_objects(path) -> list[tuple[int, str, bytes]]:
    """Every page's images as stored: xref, dictionary and raw (still encoded) stream bytes."""
    with pymupdf.open(path) as document:
        return [(xref, document.xref_object(xref), document.xref_stream_raw(xref))
                for page in document for xref, *_ in page.get_images(full=True)]


@HIDDEN_LAYERS
def test_removing_an_invisible_text_layer_continues_to_an_ocr_pdf_without_duplicated_text(workflow, tmp_path,
                                                                                         make):
    source = make(tmp_path / "older-ocr.pdf")
    before = digest(source)
    project = workflow.run(workflow.create(source)["id"])

    project = workflow.remove_text_layer(project["id"])

    assert [step(project, s)["state"] for s in ("import", "ocr", "export")] == ["done", "done", "done"]
    clean = workflow.base_pdf(project["id"])
    with pymupdf.open(clean) as document, pymupdf.open(source) as original:
        assert document.page_count and all(page.get_text() == "" for page in document)
        assert [page.get_pixmap().samples for page in document] == [page.get_pixmap().samples for page in original]
    assert image_objects(clean) == image_objects(source)
    [output] = [item for item in workflow.outputs(project["id"]) if item["name"].endswith(".pdf")]
    ocr = workflow.result(project["id"], "ocr")
    with pymupdf.open(workflow.output(project["id"], output["name"])) as document:
        for page in document:
            recognised = [word["text"] for word in page_words(read_page(ocr, page.number))]
            assert recognised
            assert [word[4] for word in page.get_text("words", sort=False)] == recognised
            assert "Rychva1dsky" not in page.get_text()
    assert image_objects(workflow.output(project["id"], output["name"])) == image_objects(source)
    assert digest(source) == before


def test_a_pdf_with_visible_text_is_not_processed_and_its_text_is_not_removed(workflow, tmp_path):
    source = text_pdf(tmp_path / "born-digital.pdf")
    before = digest(source)
    project = workflow.run(workflow.create(source)["id"])

    stopped = step(project)
    assert stopped["state"] == "failed"
    assert "viditelný text (strana 1)" in stopped["message"]
    assert stopped["action"] is None
    with pytest.raises(ValueError, match="Textovou vrstvu nelze odstranit: zdrojové PDF obsahuje viditelný text"):
        workflow.remove_text_layer(project["id"])
    assert step(workflow.get(project["id"]))["state"] == "failed"
    assert digest(source) == before


def upload_and_run(client, workflow, path) -> str:
    with path.open("rb") as source:
        project = client.post("/api/projects", files={"source": (path.name, source, "application/pdf")}).json()
    client.post(f"/api/projects/{project['id']}/run")
    workflow.wait(project["id"], timeout=30)
    return project["id"]


def test_the_removal_is_offered_and_chosen_through_the_api(client, workflow, tmp_path):
    project_id = upload_and_run(client, workflow, invisible_layer_pdf(tmp_path / "older-ocr.pdf"))
    assert step(client.get(f"/api/projects/{project_id}").json())["action"] == "remove_text_layer"

    accepted = client.post(f"/api/projects/{project_id}/text-layer/remove")
    workflow.wait(project_id, timeout=30)

    assert accepted.status_code == 202
    project = client.get(f"/api/projects/{project_id}").json()
    assert (step(project)["state"], step(project, "export")["state"]) == ("done", "done")
    assert project["working_copy"]["pages"] == [1, 2]


def test_the_api_refuses_to_remove_visible_text_in_czech(client, workflow, tmp_path):
    project_id = upload_and_run(client, workflow, text_pdf(tmp_path / "born-digital.pdf"))

    refused = client.post(f"/api/projects/{project_id}/text-layer/remove")

    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("Textovou vrstvu nelze odstranit: zdrojové PDF obsahuje viditelný text")
