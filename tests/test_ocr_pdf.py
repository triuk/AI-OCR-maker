"""Seam 1 with real Tesseract: Run goes Import → Image preparation → Local OCR → Export into an OCR PDF."""
from __future__ import annotations

import json
import math
from difflib import SequenceMatcher

import numpy
import pymupdf
import pytest

from fakes import TransformingPrepare, default_provider, run_and_export
from layout_pdfs import LEFT, RIGHT, Print, printed_pdf
from ocrmaker import inkgaps
from ocrmaker.pages import IDENTITY
from ocrmaker.steps import implementations
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow
from pdfs import columns_pdf, ground_truth


@pytest.fixture(scope="module")
def columns(tmp_path_factory):
    """One run over a two-column page of known Czech text, shared by the tests that only read its results."""
    directory = tmp_path_factory.mktemp("columns")
    workflow = Workflow(ProjectStore(directory / "library"))
    source = columns_pdf(directory / "zpravodaj-2000-05.pdf")
    project = run_and_export(workflow, workflow.create(source)["id"])
    return workflow, project, source


def ocr_pdf(workflow, project) -> pymupdf.Document:
    [output] = [item for item in workflow.outputs(project["id"]) if item["name"].endswith(".pdf")]
    return pymupdf.open(workflow.output(project["id"], output["name"]))


def extracted_words(document: pymupdf.Document, page: int = 0) -> list[tuple[str, pymupdf.Rect]]:
    return [(word[4], pymupdf.Rect(word[:4])) for word in document[page].get_text("words", sort=False)]


def stored_words(workflow, project, page: int = 0) -> list[dict]:
    overlay = workflow.overlay(project["id"], "ocr", page)
    return overlay["words"]


def states(project) -> dict:
    return {step["id"]: step["state"] for step in project["steps"]}


def test_run_goes_through_import_preparation_local_ocr_and_export(columns):
    workflow, project, _ = columns

    # Without an AI provider the reading order is geometric, with a warning; the clean page has no problem
    # spots, so AI reconstruction and Web lookup have nothing to ask.
    assert states(project) == {"import": "done", "prepare": "done", "ocr": "done", "layout": "done",
                               "order": "warning", "problems": "done", "reconstruct": "done",
                               "lookup": "done", "review": "done", "export": "done"}
    outputs = workflow.outputs(project["id"])
    assert [output["name"] for output in outputs] == ["zpravodaj-2000-05-ocr.md", "zpravodaj-2000-05-ocr.pdf"]
    with pymupdf.open(workflow.output(project["id"], "zpravodaj-2000-05-ocr.pdf")) as document:
        assert document.page_count == 1


def test_the_ocr_pdf_text_matches_the_printed_czech_text_word_for_word(columns):
    workflow, project, _ = columns

    with ocr_pdf(workflow, project) as document:
        words = [text for text, _ in extracted_words(document)]

    assert SequenceMatcher(None, words, ground_truth()).ratio() > 0.95


def test_every_extracted_word_lies_in_its_word_box_and_copies_exactly_as_stored(columns):
    workflow, project, _ = columns
    stored = stored_words(workflow, project)

    with ocr_pdf(workflow, project) as document:
        extracted = extracted_words(document)

    assert [text for text, _ in extracted] == [word["text"] for word in stored]
    assert any(set(word["text"]) & set("ěščřžýáíéůú") for word in stored)
    for (text, box), word in zip(extracted, stored):
        assert tuple(box) == pytest.approx(word["bbox"], abs=0.5), text


def scan_images(document: pymupdf.Document) -> list[tuple]:
    """Every page's image objects as stored: size, colour space, filter and raw (still encoded) stream bytes."""
    keys = ("Width", "Height", "ColorSpace", "BitsPerComponent", "Filter", "DecodeParms")
    return [tuple(document.xref_get_key(xref, key)[1] for key in keys) + (document.xref_stream_raw(xref),)
            for page in document for xref, *_ in page.get_images()]


def test_the_scan_image_bytes_in_the_ocr_pdf_are_those_of_the_source_pdf(columns):
    workflow, project, source = columns

    with ocr_pdf(workflow, project) as document, pymupdf.open(source) as original:
        assert scan_images(document) == scan_images(original)
        assert len(scan_images(document)) == 1
        assert document[0].get_image_info()[0]["transform"] == original[0].get_image_info()[0]["transform"]
        # The text layer is invisible: the page looks exactly as the source page does.
        assert document[0].get_pixmap(dpi=72).samples == original[0].get_pixmap(dpi=72).samples


def rotated(point, centre, degrees):
    """A point turned clockwise as displayed (y downwards) about a centre."""
    angle = math.radians(degrees)
    dx, dy = point[0] - centre[0], point[1] - centre[1]
    return (centre[0] + dx * math.cos(angle) - dy * math.sin(angle),
            centre[1] + dx * math.sin(angle) + dy * math.cos(angle))


def test_a_page_transform_turns_and_crops_the_page_through_its_matrix_without_re_encoding_the_scan(tmp_path):
    steps = {**implementations(), "prepare": TransformingPrepare(rotate=2.0, crop=[10, 20, 400, 580])}
    workflow = Workflow(ProjectStore(tmp_path / "library"), steps=steps)
    source = columns_pdf(tmp_path / "skewed.pdf", size=(420, 595))
    project = run_and_export(workflow, workflow.create(source)["id"])
    centre = (210, 297.5)

    stored = stored_words(workflow, project)
    with ocr_pdf(workflow, project) as document, pymupdf.open(source) as original:
        assert scan_images(document) == scan_images(original)
        assert tuple(document[0].cropbox) == (10, 20, 400, 580)
        # The whole scan turned 2° clockwise about the page centre: its bounding box grows accordingly.
        # PyMuPDF reports positions relative to the CropBox, hence the crop's origin is added back.
        corners = [rotated(corner, centre, 2.0) for corner in ((0, 0), (420, 0), (0, 595), (420, 595))]
        expected = (min(x for x, _ in corners), min(y for _, y in corners),
                    max(x for x, _ in corners), max(y for _, y in corners))
        bbox = document[0].get_image_info()[0]["bbox"]
        assert (bbox[0] + 10, bbox[1] + 20, bbox[2] + 10, bbox[3] + 20) == pytest.approx(expected, abs=0.01)
        # The text layer turns with the scan: every word sits where its box lands on the turned image.
        extracted = extracted_words(document)
    assert [text for text, _ in extracted] == [word["text"] for word in stored]
    for (text, box), word in zip(extracted, stored):
        x0, y0, x1, y1 = word["bbox"]
        expected = rotated(((x0 + x1) / 2, (y0 + y1) / 2), centre, 2.0)
        assert (box.x0 + box.x1) / 2 + 10 == pytest.approx(expected[0], abs=0.5), text
        assert (box.y0 + box.y1) / 2 + 20 == pytest.approx(expected[1], abs=0.5), text


@pytest.mark.parametrize("rotation, direction", [(90, (0, -1)), (270, (0, 1))])
def test_a_page_displayed_with_rotate_gets_its_words_upright_in_their_boxes(tmp_path, rotation, direction):
    # Problem spots keep their own reading (the scripted AI confirms them), so the text layer is Local OCR's.
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=default_provider())
    source = columns_pdf(tmp_path / "sideways.pdf", rotation=rotation)
    project = run_and_export(workflow, workflow.create(source)["id"])

    stored = stored_words(workflow, project)
    with ocr_pdf(workflow, project) as document:
        page = document[0]
        assert page.rotation == rotation
        extracted = extracted_words(document)
        # Directions are in the unrotated page; turned by /Rotate they all point to the right.
        directions = {line["dir"] for block in page.get_text("dict")["blocks"] if block["type"] == 0
                      for line in block["lines"]}
    assert SequenceMatcher(None, [text for text, _ in extracted], ground_truth()).ratio() > 0.95
    assert [text for text, _ in extracted] == [word["text"] for word in stored]
    assert directions == {direction}
    for (text, box), word in zip(extracted, stored):
        assert tuple(box) == pytest.approx(word["bbox"], abs=0.5), text


def test_the_local_ocr_step_preview_gives_each_word_box_its_text_and_confidence(columns):
    workflow, project, _ = columns

    overlay = workflow.overlay(project["id"], "ocr", 0)

    first = overlay["words"][0]
    assert (first["text"], first["id"]) == ("Obecní", "p1-w1")
    assert first["bbox"] == pytest.approx([38.6, 39.8, 70.1, 47.5], abs=1.5)
    assert all(0 <= word["confidence"] <= 100 and word["text"].strip() == word["text"] != ""
               for word in overlay["words"])
    assert len({word["id"] for word in overlay["words"]}) == len(overlay["words"]) == len(ground_truth())
    assert workflow.overlay(project["id"], "ocr", 1) is None


def test_local_ocr_records_each_pages_word_count_and_mean_confidence(columns):
    workflow, project, _ = columns

    [record] = [r for r in workflow.diagnostics(project["id"]) if r["kind"] == "ocr_page"]

    assert (record["step"], record["index"], record["words"]) == ("ocr", 0, len(ground_truth()))
    assert 80 < record["mean_confidence"] <= 100


PARISH = ("Sbor dobrovolných hasičů oslavil sto let od svého založení. Na slavnosti vystoupila dechová hudba a děti "
          "předvedly ukázku hašení malého požáru. ") * 4


@pytest.mark.parametrize("footer", ["5", "– 5 –", "strana 5"])
def test_an_isolated_centred_page_number_in_the_footer_is_not_lost(tmp_path, footer):
    # Tesseract's automatic page segmentation drops a lone centred page number below two columns of text.
    width = 12 + 6 * len(footer)
    items = [Print("left", (LEFT[0], 40, LEFT[1], 500), PARISH), Print("right", (RIGHT[0], 40, RIGHT[1], 500), PARISH),
             Print("footer", (210 - width / 2, 566, 210 + width / 2, 582), footer, size=10)]
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=None)
    project = run_and_export(workflow, workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [items]))["id"])

    footers = [word for word in stored_words(workflow, project) if word["bbox"][1] > 550]
    read = "".join(word["text"] for word in footers)
    assert [char for char in read if char.isalnum()] == [char for char in footer if char.isalnum()]  # dashes vary
    assert all(160 < word["bbox"][0] and word["bbox"][2] < 260 for word in footers)
    kinds = {block["kind"] for block in workflow.overlay(project["id"], "layout", 0)["blocks"]
             if block["bbox"][1] > 550}
    assert kinds == {"footer"}
    [markdown] = [output["name"] for output in workflow.outputs(project["id"]) if output["name"].endswith(".md")]
    assert "printed=5" in workflow.output(project["id"], markdown).read_text(encoding="utf-8")


NOTICE = "Oznamujeme občanům, že kontejnery budou přistaveny na stejných místech jako minule."


def test_text_printed_on_a_halftone_grey_background_is_read(tmp_path):
    # Tesseract's page segmentation takes a screened grey box for a picture and reads none of its text.
    items = [Print("left", (LEFT[0], 40, LEFT[1], 400), PARISH), Print("right", (RIGHT[0], 40, RIGHT[1], 400), PARISH),
             Print("notice-head", (LEFT[0], 440, RIGHT[1], 461), "Mimořádné přistavení kontejnerů", size=16,
                   shade=True),
             Print("notice", (LEFT[0], 471, RIGHT[1], 505), NOTICE, shade=True)]
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=None)
    project = run_and_export(workflow, workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [items]))["id"])

    read = [word["text"] for word in stored_words(workflow, project) if word["bbox"][1] > 430]
    expected = items[2].words + items[3].words
    assert SequenceMatcher(None, read, expected).ratio() > 0.9, read


def merged_word_scan(path, *pieces: tuple[str, int]) -> tuple[dict, object]:
    """A scan of one word as Tesseract merged it, and its page file (pixels = page coordinates): letters and digits
    print as bars 12 wide and 20 high, dots and commas as 4 by 4 squares on the baseline, 2 apart, each piece then
    followed by its gap. A dot's char box reaches into the next char, as Tesseract's LSTM boxes often do."""
    pixels = numpy.full((60, 400), 255, dtype=numpy.uint8)
    chars, x = [], 20
    for text, gap in pieces:
        for n, char in enumerate(text):
            width, top = (4, 36) if char in ".," else (12, 20)
            pixels[top:40, x:x + width] = 0
            reach = gap + 3 if char in ".," and n == len(text) - 1 and gap else 0
            chars.append({"text": char, "bbox": [x, top, x + width + reach, 40], "confidence": 99.0 - len(chars)})
            x += width + (gap if n == len(text) - 1 else 2)
    pymupdf.Pixmap(pymupdf.csGRAY, 400, 60, pixels.tobytes(), False).save(path)
    word = {"id": "p1-w1", "text": "".join(char["text"] for char in chars), "bbox": [20, 20, x, 40],
            "confidence": 50.0, "chars": chars}
    return {"index": 0, "lines": [{"id": "p1-l1", "bbox": [20, 20, x, 40], "words": [word]}]}, path


@pytest.mark.parametrize("pieces, expected", [
    ([("19.", 8), ("2.", 8), ("2000", 0)], ["19.", "2.", "2000"]),  # a date printed with narrow spaces
    ([("8", 7), ("do", 0)], ["8", "do"]),  # a number and the next word
    ([("16.", 3), ("15", 0)], ["16.15"]),  # a time: the dot's own gap is narrow
    ([("5", 2), ("kg", 0)], ["5kg"]),
    ([("na", 9), ("již", 0)], ["najiž"]),  # between letters a gap is left to Tesseract (letter-spaced print)
])
def test_a_word_tesseract_merged_over_a_narrow_space_is_cut_there(tmp_path, pieces, expected):
    page, raster = merged_word_scan(tmp_path / "page.png", *pieces)

    [line] = inkgaps.split_narrow_spaces(page, raster, IDENTITY, IDENTITY)["lines"]

    assert [word["text"] for word in line["words"]] == expected
    assert [word["id"] for word in line["words"]] == [f"p1-w{n}" for n in range(1, len(expected) + 1)]
    if len(expected) > 1:  # each part wraps its own ink (blurred by descreening) and has its chars' lowest confidence
        for word in line["words"]:
            last = word["chars"][-1]
            assert word["bbox"][0] == word["chars"][0]["bbox"][0] and word["bbox"][1::2] == [20, 40]
            assert word["bbox"][2] == pytest.approx(last["bbox"][0] + (4 if last["text"] in ".," else 12), abs=2)
            assert word["confidence"] == last["confidence"]
        assert json.dumps(line)
