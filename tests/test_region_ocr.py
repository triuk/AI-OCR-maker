"""Seam 1 with real Tesseract: Local OCR, Layout (blocks) and Reading order per layout region (ADR 0006, #18)."""
from __future__ import annotations

import pymupdf
import pytest

from fakes import ScriptedProvider, answer_spots, run_and_export
from layout_pdfs import CROSSING, CROSSING_COLUMNS, CROSSING_READING, LEFT, RIGHT, RULES, Print, printed_pdf
from ocrmaker import reading_order as rules
from ocrmaker import regions
from ocrmaker.steps import implementations, ocr_step
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
    workflow.update_settings(project["id"], {"layout_method": "regions", "layout_auto_approve": True})
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


IMPRINT = Print("imprint", (LEFT[0], 540, RIGHT[1], 562), "Vydává město Rychvald. Šéfredaktor: Tadeusz Siwek. "
                "Adresa redakce: Městský úřad Rychvald, Orlovská 678. Tisk: KarTis.", 8, inverse=True)


def test_an_imprint_on_a_black_bar_is_read_once_in_its_region(tmp_path):
    # Each region is read on its own and the whole page once more (the words only the page reading finds are
    # taken over); where both readings find a word it must not come out twice.
    pdf = printed_pdf(tmp_path / "zpravodaj-2000-05.pdf", [CROSSING + [IMPRINT]])
    x0, y0, x1, y1 = IMPRINT.rect
    bar = [round((x0 - 6) * 1000 / 420), round((y0 - 6) * 1000 / 595), round((x1 + 6) * 1000 / 420),
           round((y1 + 6) * 1000 / 595)]
    answer = {"parts": READING["parts"] + [part("Vydává město Rychvald", bar, kind="footer")]}
    workflow, project = run_local(tmp_path, pdf, answer, steps=("import", "prepare", "regions", "ocr"))

    found = {region["id"]: region for region in page_regions(workflow, project)}
    footer = list(found.values())[-1]
    assert footer["kind"] == "footer"
    page = recognised(workflow, project)
    read = [word["text"] for line in page["lines"] if line["region"] == footer["id"] for word in line["words"]]
    assert read == IMPRINT.text.split()
    texts = [word["text"] for line in page["lines"] for word in line["words"] if word["text"]]
    assert len(texts) == sum(len(item.text.split()) for item in CROSSING + [IMPRINT])
    for line in page["lines"]:
        for word in line["words"]:
            assert inside(word["bbox"], found[line["region"]]["bbox"]), word


def word(text, x0, y0, x1, y1) -> dict:
    return {"id": "", "text": text, "bbox": [x0, y0, x1, y1], "confidence": 90.0, "chars": []}


def line(region, *words) -> dict:
    boxes = [item["bbox"] for item in words]
    return {"id": "", "region": region, "baseline": None, "block": 0, "paragraph": 0, "words": list(words),
            "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                     max(b[3] for b in boxes)]}


def test_a_region_takes_over_the_words_only_the_whole_page_reading_found():
    # Alone on its canvas a region can read worse than on the page: on the 2000 newsletter Tesseract dropped the
    # years in the binding's shadow at a region's edge and an imprint's white letters on their black bar, though
    # the page reading has a word or an empty word box there.
    layout_regions = [{"id": "p1-r1", "bbox": [0, 0, 100, 100], "orientation": "0"},
                      {"id": "p1-r2", "bbox": [110, 0, 200, 100], "orientation": "0"},
                      {"id": "p1-r3", "bbox": [0, 110, 200, 200], "orientation": "90"}]
    read = [line("p1-r1", word("Bohumil", 10, 10, 40, 20), word("Blahoš", 44, 10, 70, 20)),
            line("p1-r3", word("Inzerce", 10, 120, 20, 190))]
    whole = {"index": 0, "lines": [
        # the page reading's line runs across both regions; its first two words the region has itself
        line(None, word("Bohumil", 10, 10, 40, 20), word("Blahos", 45, 10, 70, 20), word("1924", 80, 10, 96, 20),
             word("Sokol", 120, 10, 150, 20), word("", 155, 10, 170, 20)),
        line(None, word("Zemřeli", 10, 50, 50, 60)),
        line(None, word("%%", 60, 150, 90, 160)),      # in a turned region the page reading is no evidence
        line(None, word("smítko", 102, 205, 108, 210))]}  # in no region

    lines = ocr_step.with_page_words(read, whole, layout_regions)

    assert [(item["region"], [w["text"] for w in item["words"]]) for item in lines] == [
        ("p1-r1", ["Bohumil", "Blahoš", "1924"]),  # on the row of a line the region read: joins that line
        ("p1-r1", ["Zemřeli"]),
        ("p1-r2", ["Sokol", ""]),
        ("p1-r3", ["Inzerce"])]
    assert lines[0]["bbox"] == [10, 10, 96, 20] and lines[2]["bbox"] == [120, 10, 170, 20]


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
    workflow.update_settings(project["id"], {"layout_method": "regions", "layout_auto_approve": True})
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


def box(page, *keys, pad=4) -> list[int]:
    """The grid box (0..1000) around the printed items of a RULES page."""
    rects = [item.rect for item in RULES[page] if item.key in keys]
    x0, y0 = min(r[0] for r in rects) - pad, min(r[1] for r in rects) - pad
    x1, y1 = max(r[2] for r in rects) + pad, max(r[3] for r in rects) + pad
    return [round(x0 * 1000 / 420), round(y0 * 1000 / 595), round(x1 * 1000 / 420), round(y1 * 1000 / 595)]


TABLE = [item.key for item in RULES[1] if item.key.startswith("T-")]
RULES_REGIONS = [
    {"parts": [part("Rychvaldský zpravodaj", box(0, "header-1"), kind="header"),
               part("Hasiči slavili", box(0, "C-head", "C1"), box(0, "caption-1")),
               part("Zastupitelé projednali", box(0, "E1")),
               part("Prodám garáž", box(0, "ad-1"), kind="ad"),
               part("strana 1", box(0, "footer-1"), kind="footer")]},
    {"parts": [part("Rychvaldský zpravodaj", box(1, "header-2"), kind="header"),
               part("začnou ještě letos", box(1, "E2")),
               part("Turnaj v kopané", box(1, "F-head", "F1"), box(1, *TABLE)),
               part("strana 2", box(1, "footer-2"), kind="footer")]}]


def regions_in_turn(answers):
    """Layout regions answers page by page; a corrective request gets the current page's answer again."""
    pages = iter(answers)
    current = {}

    def answer(request):
        if isinstance(request.data, str):
            current["answer"] = next(pages)
        return current["answer"]
    return answer


def continued(request) -> dict:
    """The link of the page's article starting "začnou" to the previous page's article ending "oplocení"."""
    [article] = [item["id"] for item in request.data["articles"] if item["starts"].startswith("začnou")]
    [previous] = [item["id"] for item in request.data["previous_page"]["articles"]
                  if item["ends"].endswith("oplocení")]
    return {"links": [{"article": article, "continues": previous}]}


@pytest.fixture(scope="module")
def continuation(tmp_path_factory):
    """Two pages divided into layout regions; an article of page 1 goes on at the top of page 2."""
    directory = tmp_path_factory.mktemp("continuation")
    provider = ScriptedProvider().on(regions.PURPOSE, regions_in_turn(RULES_REGIONS))
    provider.on("readthrough", {"words": []}).on("reconstruction", answer_spots())
    provider.on(rules.CONTINUATION_PURPOSE, continued)
    workflow = Workflow(ProjectStore(directory / "library"), language=provider)
    project = workflow.create(printed_pdf(directory / "zpravodaj-2000-05.pdf", RULES))
    workflow.update_settings(project["id"], {"layout_method": "regions", "layout_auto_approve": True})
    return workflow, provider, run_and_export(workflow, project["id"])


def test_an_article_continued_on_the_next_page_is_linked_from_the_text_of_both_pages(continuation):
    workflow, provider, project = continuation
    assert {step["id"]: step["state"] for step in project["steps"]}["order"] == "done"

    # only the second page has a previous page to continue; the request shows the articles, never their blocks
    [request] = provider.requests_for(rules.CONTINUATION_PURPOSE)
    assert request.images == []
    assert [item["id"] for item in request.data["previous_page"]["articles"]] == ["p1-a2", "p1-a3"]
    assert [item["id"] for item in request.data["articles"]] == ["p2-a2", "p2-a3"]
    assert request.data["articles"][1]["starts"].startswith("Turnaj v kopané Fotbalisté")
    assert "blocks" not in str(request.data)
    order = workflow.result(project["id"], "order").read_json("order.json")
    assert order["links"] == [{"from": "p1-a3", "to": "p2-a2"}]
    assert [[(item["kind"], item["continues"]) for item in page["articles"]] for page in order["pages"]] == [
        [("header", None), ("article", None), ("article", None), ("ad", None), ("footer", None)],
        [("header", None), ("article", "p1-a3"), ("article", None), ("footer", None)]]
    assert [page["source"] for page in order["pages"]] == ["regions", "regions"]
    assert order["pages"][0]["request"] is None and order["pages"][1]["request"]["model"] == "scripted-model"

    # the text keeps the regions' order (a table without rules inside its region is read by columns, #23)
    second = " ".join(extracted_words(workflow, project, 1))
    starts = [second.index(" ".join(words_of(RULES[1], [key])[:3])) for key in ("header-2", "E2", "F-head", "F1")]
    assert starts == sorted(starts) and second.endswith("strana 2")


@pytest.mark.parametrize("answer", [
    {"links": [{"article": "p2-a1", "continues": "p1-a3"}]},    # a running header cannot continue an article
    {"links": [{"article": "p2-a2", "continues": "p1-a9"}]},    # no such article on the previous page
    {"links": [{"article": "p2-a2", "continues": "p1-a3"}, {"article": "p2-a3", "continues": "p1-a3"}]},
    {"links": [{"article": ["p2-a2"], "continues": "p1-a3"}]},  # not an id
    "not json"])
def test_an_invalid_continuation_answer_links_nothing_with_a_warning_and_keeps_the_articles(continuation, answer):
    workflow, provider, project = continuation
    before = workflow.result(project["id"], "order").read_json("order.json")
    provider.on(rules.CONTINUATION_PURPOSE, answer)
    try:
        project = workflow.rerun(project["id"], "order")
        check_unlinked(workflow, project, before)
    finally:
        provider.on(rules.CONTINUATION_PURPOSE, continued)
        workflow.rerun(project["id"], "order")


def check_unlinked(workflow, project, before):
    state = {step["id"]: step for step in project["steps"]}["order"]
    assert state["state"] == "warning" and "propojen" in state["message"]
    order = workflow.result(project["id"], "order").read_json("order.json")
    assert order["links"] == []
    assert order["pages"][1]["reason"]
    assert [[item["blocks"] for item in page["articles"]] for page in order["pages"]] == [
        [item["blocks"] for item in page["articles"]] for page in before["pages"]]
