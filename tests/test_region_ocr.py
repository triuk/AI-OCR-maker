"""Seam 1 with real Tesseract: Local OCR, Layout (blocks) and Reading order per layout region (ADR 0006, #18)."""
from __future__ import annotations

import pymupdf
import pytest

from fakes import ScriptedProvider, run_and_export
from layout_pdfs import CROSSING, CROSSING_COLUMNS, CROSSING_READING, Print, printed_pdf
from ocrmaker import regions
from ocrmaker.steps import implementations
from ocrmaker.store import ProjectStore
from ocrmaker.words import page_file
from ocrmaker.workflow import Workflow
from test_reading_order import extracted_words, similarity, words_of
from test_regions import READING, grid, part

LOCAL = ("import", "prepare", "regions", "ocr", "layout", "order")


def run_local(tmp_path, pdf, *answers, steps=LOCAL):
    """The local steps up to Reading order of a regions project, with scripted layout answers."""
    provider = ScriptedProvider().on(regions.PURPOSE, *answers)
    shipped = implementations()
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=provider,
                        steps={step: shipped[step] for step in steps})
    project = workflow.create(pdf)
    workflow.update_settings(project["id"], {"layout_method": "regions"})
    return workflow, workflow.run(project["id"])


def page_regions(workflow, project, page: int = 0) -> list[dict]:
    return workflow.result(project["id"], "regions").read_json("regions.json")["pages"][page]["regions"]


def recognised(workflow, project, page: int = 0) -> dict:
    return workflow.result(project["id"], "ocr").read_json(page_file(page))


def inside(box, region, slack=1.0) -> bool:
    return (box[0] >= region[0] - slack and box[1] >= region[1] - slack
            and box[2] <= region[2] + slack and box[3] <= region[3] + slack)


@pytest.fixture(scope="module")
def crossing_pdf(tmp_path_factory):
    return printed_pdf(tmp_path_factory.mktemp("region-ocr") / "zpravodaj-2000-05.pdf", [CROSSING])


def test_local_ocr_reads_each_region_on_its_own_inside_its_box(tmp_path, crossing_pdf):
    workflow, project = run_local(tmp_path, crossing_pdf, READING, steps=("import", "prepare", "regions", "ocr"))

    regions_by_id = {region["id"]: region for region in page_regions(workflow, project)}
    page = recognised(workflow, project)
    assert {line["region"] for line in page["lines"]} == set(regions_by_id)
    for line in page["lines"]:
        for word in line["words"]:
            assert inside(word["bbox"], regions_by_id[line["region"]]["bbox"]), word
    ids = [word["id"] for line in page["lines"] for word in line["words"]]
    assert len(ids) == len(set(ids))
    texts = " ".join(word["text"] for line in page["lines"] for word in line["words"]).split()
    for item in CROSSING:
        assert item.words[0] in texts


def test_blocks_are_built_inside_each_region_in_the_regions_order(tmp_path, crossing_pdf):
    workflow, project = run_local(tmp_path, crossing_pdf, READING, steps=("import", "prepare", "regions", "ocr",
                                                                          "layout"))

    found = page_regions(workflow, project)
    page = recognised(workflow, project)
    layout = workflow.result(project["id"], "layout").read_json(page_file(0))
    region_of = {word["id"]: line["region"] for line in page["lines"] for word in line["words"]}
    in_blocks = [word for block in layout["blocks"] for line in block["lines"] for word in line]
    assert sorted(in_blocks) == sorted(region_of)
    for block in layout["blocks"]:
        assert {region_of[word] for line in block["lines"] for word in line} == {block["region"]}
    # blocks follow the regions' reading order, numbered on the page; each region is a column of its own
    assert [block["region"] for block in layout["blocks"]] == ["p1-r1", "p1-r1", "p1-r2", "p1-r3", "p1-r3", "p1-r4"]
    assert [block["number"] for block in layout["blocks"]] == [1, 2, 3, 4, 5, 6]
    assert layout["columns"] == [region["bbox"] for region in found]
    assert [block["column"] for block in layout["blocks"]] == [0, 0, 1, 2, 2, 3]
    texts = {word["id"]: word["text"] for line in page["lines"] for word in line["words"]}
    kinds = {texts[block["lines"][0][0]]: block["kind"] for block in layout["blocks"]}
    assert kinds == {"Oprava": "heading", "Obecní": "body", "Nové": "heading", "Knihovna": "body",
                     "Stavební": "body", "Čtenáři": "body"}


def test_export_writes_the_articles_in_the_regions_reading_order(tmp_path, crossing_pdf):
    provider = ScriptedProvider().on(regions.PURPOSE, READING).on("readthrough", {"words": []})
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=provider)
    project = workflow.create(crossing_pdf)
    workflow.update_settings(project["id"], {"layout_method": "regions"})
    project = run_and_export(workflow, project["id"])

    assert {step["id"]: step["state"] for step in project["steps"]}["export"] == "done"
    # the articles, their kinds and their order come from the regions; nothing asks the AI to regroup the blocks
    assert provider.requests_for("reading_order") == []
    layout = workflow.result(project["id"], "layout").read_json(page_file(0))
    order = workflow.result(project["id"], "order").read_json("order.json")
    [entry] = order["pages"]
    assert entry["source"] == "regions"
    regions_of = {block["id"]: block["region"] for block in layout["blocks"]}
    assert [[regions_of[block] for block in article["blocks"]] for article in entry["articles"]] == [
        ["p1-r1", "p1-r1", "p1-r2"], ["p1-r3", "p1-r3", "p1-r4"]]
    assert [article["kind"] for article in entry["articles"]] == ["article", "article"]
    words = extracted_words(workflow, project)
    assert similarity(words, words_of(CROSSING, CROSSING_READING)) > 0.95
    assert similarity(words, words_of(CROSSING, CROSSING_COLUMNS)) < 0.9
    [markdown] = [item["name"] for item in workflow.outputs(project["id"]) if item["name"].endswith(".md")]
    text = workflow.output(project["id"], markdown).read_text()
    assert text.index("Stavební") < text.index("Nové knihy")


def test_ink_outside_every_region_is_not_recognised(tmp_path, crossing_pdf):
    only_a = {"parts": READING["parts"][:1]}
    workflow, project = run_local(tmp_path, crossing_pdf, only_a, steps=("import", "prepare", "regions", "ocr"))

    texts = [word["text"] for line in recognised(workflow, project)["lines"] for word in line["words"]]
    assert texts[:2] == ["Oprava", "školy"]
    for key in ("B-head", "B1", "B2"):
        assert not set(item.words[0] for item in CROSSING if item.key == key) & set(texts)


SIDE = [*CROSSING[:2], Print("side", (392, 120, 410, 470), "Zpravodaj městského obvodu Hrabová", size=13, turn=90)]


def test_a_turned_region_is_read_upright_in_source_page_coordinates(tmp_path):
    pdf = printed_pdf(tmp_path / "zpravodaj-2000-06.pdf", [SIDE])
    side = [round(392 * 1000 / 420) - 5, round(120 * 1000 / 595) - 5, round(410 * 1000 / 420) + 5,
            round(470 * 1000 / 595) + 5]
    answer = {"parts": [part("Oprava školy", grid("A-head", "A1")),
                        {"kind": "header", "title": "Zpravodaj", "regions": [{"box": side, "orientation": "90"}]}]}
    workflow, project = run_local(tmp_path, pdf, answer)

    turned = [line for line in recognised(workflow, project)["lines"] if line["region"] == "p1-r2"]
    words = [word for line in turned for word in line["words"]]
    assert [word["text"] for word in words] == SIDE[2].words
    for word in words:
        x0, y0, x1, y1 = word["bbox"]
        assert inside(word["bbox"], (392, 120, 410, 470), slack=3) and y1 - y0 > x1 - x0
    # read bottom to top, as the letters run
    assert [word["bbox"][1] for word in words] == sorted((word["bbox"][1] for word in words), reverse=True)
    layout = workflow.result(project["id"], "layout").read_json(page_file(0))
    assert [block["kind"] for block in layout["blocks"] if block["region"] == "p1-r2"] == ["header"]
