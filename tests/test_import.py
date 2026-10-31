"""Seam 1: the Import step reads the source PDF, records page geometry and renders working rasters."""
from __future__ import annotations

import pytest

import pymupdf

from ocrmaker.pages import apply
from pdfs import drawing_pdf, embedded_images, scan_pdf


def import_step(project) -> dict:
    return next(step for step in project["steps"] if step["id"] == "import")


@pytest.fixture
def run(workflow):
    def run(pdf):
        project = workflow.create(pdf)
        return workflow.run(project["id"])
    return run


def test_a_run_imports_the_source_pdf_and_records_each_pages_geometry(workflow, run, tmp_path):
    project = run(scan_pdf(tmp_path / "scan.pdf", pages=2, size=(360, 504), dpi=200))

    assert import_step(project)["state"] == "done"
    pages = workflow.pages(project["id"])
    assert [page["index"] for page in pages] == [0, 1]
    assert (pages[0]["width"], pages[0]["height"], pages[0]["rotation"]) == (360, 504, 0)
    assert pages[0]["mediabox"] == pages[0]["cropbox"] == [0, 0, 360, 504]
    assert pages[0]["image"]["dpi"] == [200, 200]


@pytest.mark.parametrize("size, dpi", [((360, 504), 200), ((595.2, 841.92), 300)])
def test_working_rasters_reproduce_the_scan_image_pixels_at_its_native_resolution(workflow, run, tmp_path,
                                                                                 size, dpi):
    source = scan_pdf(tmp_path / "scan.pdf", size=size, dpi=dpi)
    project = run(source)

    scan = embedded_images(source)[0]
    raster = pymupdf.Pixmap(str(workflow.page_image(project["id"], 0)))
    page = workflow.pages(project["id"])[0]
    assert (raster.width, raster.height) == (scan.width, scan.height)
    assert raster.samples == scan.samples
    assert page["raster"]["native"] is True
    assert page["raster"]["dpi"] == pytest.approx(page["image"]["dpi"])


def dark(pixmap, x: float, y: float) -> bool:
    return pixmap.pixel(int(x), int(y))[0] < 64


def test_a_rotated_cropped_page_gets_an_upright_raster_mapped_back_to_source_page_coordinates(
        workflow, run, tmp_path):
    # The mark at (250, 380)-(290, 430) on the MediaBox lies at (230, 350)-(270, 400) in source page
    # coordinates, whose origin is the CropBox's top-left corner.
    source = scan_pdf(tmp_path / "rotated.pdf", text="", size=(360, 504), dpi=200, rotation=90,
                      cropbox=(20, 30, 340, 484), mark=(250, 380, 290, 430))
    project = run(source)

    page = workflow.pages(project["id"])[0]
    raster = pymupdf.Pixmap(str(workflow.page_image(project["id"], 0)))
    assert (page["width"], page["height"], page["rotation"]) == (320, 454, 90)
    assert page["mediabox"] == [0, 0, 360, 504]
    assert page["cropbox"] == [20, 20, 340, 474]  # PDF user space, y upwards
    # Displayed a quarter turn clockwise: the raster is landscape, and source (x, y) shows at (454 - y, x).
    scale = 200 / 72
    assert (raster.width, raster.height) == (round(454 * scale), round(320 * scale))
    assert dark(raster, (454 - 375) * scale, 250 * scale)
    assert not dark(raster, (454 - 375) * scale, 100 * scale)
    to_raster, to_source = page["raster"]["to_raster"], page["raster"]["to_source"]
    assert apply(to_raster, 250, 375) == pytest.approx(((454 - 375) * scale, 250 * scale))
    assert apply(to_source, *apply(to_raster, 250, 375)) == pytest.approx((250, 375))
    assert apply(page["pdf_to_source"], 270, 504 - 380) == pytest.approx((250, 350))


def test_import_writes_a_diagnostic_record_with_each_pages_geometry_and_resolution(workflow, run, tmp_path):
    project = run(scan_pdf(tmp_path / "scan.pdf", pages=2, size=(360, 504), dpi=200))

    records = workflow.diagnostics(project["id"])
    record = next(r for r in records if r["kind"] == "import")
    assert (record["step"], record["pages"], record["outcome"]) == ("import", 2, "imported")
    assert [page["raster_size"] for page in record["page_details"]] == [[1000, 1400], [1000, 1400]]
    assert record["page_details"][0]["image_dpi"] == [200, 200]
    assert record["page_details"][0]["native"] is True
    assert any(r["kind"] == "step" and r["step"] == "import" and r["outcome"] == "done" for r in records)


def test_the_import_step_preview_shows_page_bounds_and_resolution(workflow, run, tmp_path):
    project = run(scan_pdf(tmp_path / "scan.pdf", size=(360, 504), dpi=200, cropbox=(20, 30, 340, 484)))

    overlay = workflow.overlay(project["id"], "import", 0)

    assert overlay["bounds"] == [0, 0, 320, 454]
    assert overlay["mediabox"] == [-20, -30, 340, 474]
    assert overlay["image"]["bbox"] == [-20, -30, 340, 474]
    assert (overlay["image"]["width"], overlay["image"]["height"], overlay["image"]["dpi"]) == (1000, 1400, [200, 200])
    assert overlay["raster"] == {"width": round(320 * 200 / 72), "height": round(454 * 200 / 72),
                                 "dpi": [200, 200], "native": True}
    assert workflow.overlay(project["id"], "import", 1) is None


def test_a_page_without_a_scan_image_is_rendered_at_a_fallback_resolution_with_a_warning(workflow, run, tmp_path):
    project = run(drawing_pdf(tmp_path / "drawing.pdf"))

    step = import_step(project)
    page = workflow.pages(project["id"])[0]
    assert step["state"] == "warning"
    assert "náhradním rozlišení" in step["message"]
    assert page["image"] is None
    assert (page["raster"]["dpi"], page["raster"]["native"]) == ([300, 300], False)
    assert (page["raster"]["width"], page["raster"]["height"]) == (round(200 * 300 / 72), round(300 * 300 / 72))


def test_a_low_resolution_scan_is_imported_with_a_warning(workflow, run, tmp_path):
    project = run(scan_pdf(tmp_path / "low.pdf", dpi=100))

    step = import_step(project)
    assert step["state"] == "warning"
    assert "nízké rozlišení" in step["message"]
    assert workflow.pages(project["id"])[0]["raster"]["native"] is True
