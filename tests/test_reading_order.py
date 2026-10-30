"""Seam 1 with real Tesseract: Layout builds blocks and Reading order puts them into articles for the OCR PDF."""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

import pymupdf
import pytest

from fakes import ScriptedProvider
from layout_pdfs import CROSSING, CROSSING_COLUMNS, CROSSING_READING, RULES, TABLE_ROWS, printed_pdf
from ocrmaker.language import LanguageError
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow


def article(*numbers, kind="article", continues=None) -> dict:
    return {"kind": kind, "blocks": list(numbers), "continues": continues}


def numbers_by_key(request, items) -> dict[str, int]:
    """The block number of every printed item, found by the item's first word at the start of a block."""
    found = {}
    for item in items:
        matches = [block["number"] for block in request.data["blocks"]
                   if block["starts"].split()[:1] == item.words[:1]]
        assert len(matches) == 1, f"no single block starts with {item.words[0]!r}: {request.data['blocks']}"
        found[item.key] = matches[0]
    return found


def extracted_words(workflow, project, page: int = 0) -> list[str]:
    [output] = workflow.outputs(project["id"])
    with pymupdf.open(workflow.output(project["id"], output["name"])) as document:
        return [word[4] for word in document[page].get_text("words", sort=False)]


def words_of(items, keys) -> list[str]:
    by_key = {item.key: item for item in items}
    return [word for key in keys for word in by_key[key].words]


def ocr_words(workflow, project, page: int) -> list[str]:
    return [word["text"] for word in workflow.overlay(project["id"], "ocr", page)["words"]]


def states(project) -> dict:
    return {step["id"]: step for step in project["steps"]}


def similarity(words, expected) -> float:
    return SequenceMatcher(None, words, expected).ratio()


@pytest.fixture(scope="module")
def crossing(tmp_path_factory):
    """One recognised two-column page with two crossing articles; tests rerun Reading order with own answers."""
    directory = tmp_path_factory.mktemp("crossing")
    provider = ScriptedProvider()
    provider.on("reading_order", lambda request: {"articles": [
        article(*(numbers_by_key(request, CROSSING)[key] for key in CROSSING_READING))]})
    workflow = Workflow(ProjectStore(directory / "library"), language=provider)
    project = workflow.run(workflow.create(printed_pdf(directory / "zpravodaj-2000-05.pdf", [CROSSING]))["id"])
    return workflow, provider, project


def rerun_order(crossing, *answers):
    workflow, provider, project = crossing
    provider.requests.clear()
    provider.on("reading_order", *answers)
    return workflow.rerun(project["id"], "order")


def test_the_ocr_pdf_text_follows_the_articles_in_the_ai_reading_order(crossing):
    workflow, provider, _ = crossing

    def by_article(request):
        number = numbers_by_key(request, CROSSING)
        return {"articles": [article(number["A-head"], number["A1"], number["A2"]),
                             article(number["B-head"], number["B1"], number["B2"])]}
    project = rerun_order(crossing, by_article)

    assert states(project)["order"]["state"] == "done"
    assert states(project)["export"]["state"] == "done"
    words = extracted_words(workflow, project)
    assert similarity(words, words_of(CROSSING, CROSSING_READING)) > 0.95
    assert similarity(words, words_of(CROSSING, CROSSING_COLUMNS)) < 0.9
    order = workflow.result(project["id"], "order").read_json("order.json")
    assert [item["kind"] for item in order["pages"][0]["articles"]] == ["article", "article"]
    assert order["pages"][0]["source"] == "ai"


def test_layout_puts_every_word_into_exactly_one_block_of_the_right_kind(crossing):
    workflow, _, project = crossing
    layout = workflow.result(project["id"], "layout").read_json("pages/page-0001.json")
    ocr = workflow.overlay(project["id"], "ocr", 0)

    in_blocks = [word for block in layout["blocks"] for line in block["lines"] for word in line]
    assert sorted(in_blocks) == sorted(word["id"] for word in ocr["words"])
    assert len(layout["blocks"]) == len(CROSSING)
    texts = {word["id"]: word["text"] for word in ocr["words"]}
    kinds = {texts[block["lines"][0][0]]: block["kind"] for block in layout["blocks"]}
    assert kinds == {"Oprava": "heading", "Obecní": "body", "Nové": "heading", "Knihovna": "body",
                     "Stavební": "body", "Čtenáři": "body"}
    assert len(layout["columns"]) == 2


@pytest.mark.parametrize("answer", [
    "not json",
    {"articles": []},
    {"order": [1, 2, 3]},
    "missing",
    "twice",
    "unknown",
    "bad continuation",
], ids=["not-json", "empty", "no-articles", "missing-block", "block-twice", "unknown-block", "bad-continuation"])
def test_an_invalid_answer_falls_back_to_the_geometric_order_with_a_warning_and_loses_no_text(crossing, answer):
    workflow, _, _ = crossing

    def broken(request):
        numbers = [block["number"] for block in request.data["blocks"]]
        return {"missing": {"articles": [article(*numbers[1:])]},
                "twice": {"articles": [article(*numbers), article(numbers[0])]},
                "unknown": {"articles": [article(*numbers, 99)]},
                "bad continuation": {"articles": [article(*numbers, continues="p0-a1")]}}[answer]
    scripted = answer in ("missing", "twice", "unknown", "bad continuation")
    project = rerun_order(crossing, broken if scripted else answer)

    order_state = states(project)["order"]
    assert order_state["state"] == "warning"
    assert "geometrické" in order_state["message"]
    assert states(project)["export"]["state"] == "done"
    words = extracted_words(workflow, project)
    assert Counter(words) == Counter(ocr_words(workflow, project, 0))
    # The geometric order reads the columns left to right, each top to bottom.
    assert similarity(words, words_of(CROSSING, CROSSING_COLUMNS)) > 0.95
    order = workflow.result(project["id"], "order").read_json("order.json")
    assert order["pages"][0]["source"] == "geometric"
    assert order["pages"][0]["reason"]


def test_a_failed_ai_connection_fails_the_step_with_its_czech_message(crossing):
    project = rerun_order(crossing, LanguageError("Byl vyčerpán limit předplatného.", code="usage_limit"))

    assert states(project)["order"]["state"] == "failed"
    assert "limit předplatného" in states(project)["order"]["message"]
    assert states(project)["export"]["state"] == "pending"


def test_without_any_ai_provider_the_order_is_geometric_with_a_warning(tmp_path):
    workflow = Workflow(ProjectStore(tmp_path / "library"))
    project = workflow.run(workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [CROSSING]))["id"])

    assert states(project)["order"]["state"] == "warning"
    assert "AI není k dispozici" in states(project)["order"]["message"]
    assert similarity(extracted_words(workflow, project), words_of(CROSSING, CROSSING_COLUMNS)) > 0.95


def test_the_request_shows_the_numbered_page_the_block_kinds_and_the_rules(crossing):
    workflow, provider, project = crossing
    rerun_order(crossing, lambda request: {"articles": [
        article(*(block["number"] for block in request.data["blocks"]))]})
    [request] = provider.requests_for("reading_order")

    assert request.images and request.images[0].startswith(b"\x89PNG")
    with pymupdf.open(stream=request.images[0], filetype="png") as image:
        assert max(image[0].rect.width, image[0].rect.height) <= 1600
    assert [block["number"] for block in request.data["blocks"]] == list(range(1, len(CROSSING) + 1))
    assert {block["kind"] for block in request.data["blocks"]} == {"heading", "body"}
    assert request.data["page"] == 1 and request.data["previous_page"] is None
    for rule in ("exactly once", "continues", "header", "footer", "caption", "ad", "table"):
        assert rule in request.instructions.lower()
    assert request.schema["required"] == ["articles"]


def test_the_step_previews_show_blocks_by_kind_and_numbered_articles_with_arrows(crossing):
    workflow, _, _ = crossing

    def by_article(request):
        number = numbers_by_key(request, CROSSING)
        return {"articles": [article(number["A-head"], number["A1"], number["A2"]),
                             article(number["B-head"], number["B1"], number["B2"])]}
    project = rerun_order(crossing, by_article)

    layout = workflow.overlay(project["id"], "layout", 0)
    assert {block["kind"] for block in layout["blocks"]} == {"heading", "body"}
    assert all(len(block["bbox"]) == 4 and block["number"] and block["text"] for block in layout["blocks"])
    order = workflow.overlay(project["id"], "order", 0)
    assert [item["number"] for item in order["articles"]] == [1, 2]
    first = order["articles"][0]
    assert [block["number"] for block in first["blocks"]] == [1, 2, 5]  # heading, left column, right column
    assert all(len(block["bbox"]) == 4 for item in order["articles"] for block in item["blocks"])


@pytest.fixture(scope="module")
def rules_run(tmp_path_factory):
    """Two pages with headers, footers, a caption, an ad, a table and an article continued on page 2.

    The scripted AI deliberately answers out of order; the rules put each group where it belongs.
    """
    directory = tmp_path_factory.mktemp("rules")
    provider = ScriptedProvider()

    def answer(request):
        page = request.data["page"]
        # The table is one block, found by its first cell.
        number = numbers_by_key(request, [item for item in RULES[page - 1]
                                          if not item.key.startswith("T-") or item.key == "T-r1"])
        if page == 1:
            return {"articles": [article(number["footer-1"], kind="footer"),
                                 article(number["C-head"], number["caption-1"], number["C1"]),
                                 article(number["ad-1"], kind="ad"),
                                 article(number["E1"]),
                                 article(number["header-1"], kind="header")]}
        [continued] = [item["id"] for item in request.data["previous_page"]["articles"]
                       if item["ends"].endswith("oplocení")]
        return {"articles": [article(number["footer-2"], kind="footer"),
                             article(number["F-head"], number["F1"], number["T-r1"]),
                             article(number["E2"], continues=continued),
                             article(number["header-2"], kind="header")]}
    provider.on("reading_order", answer)
    workflow = Workflow(ProjectStore(directory / "library"), language=provider)
    project = workflow.run(workflow.create(printed_pdf(directory / "zpravodaj-2000-05.pdf", RULES))["id"])
    return workflow, provider, project


def test_headers_footers_captions_ads_and_continuations_are_placed_by_the_rules(rules_run):
    workflow, _, project = rules_run
    assert states(project)["order"]["state"] == "done"

    first = extracted_words(workflow, project, 0)
    expected = words_of(RULES[0], ["header-1", "C-head", "C1", "caption-1", "E1", "ad-1", "footer-1"])
    assert similarity(first, expected) > 0.95
    second = extracted_words(workflow, project, 1)
    table = [word for row in TABLE_ROWS for word in row]
    expected = words_of(RULES[1], ["header-2", "E2", "F-head", "F1"]) + table + words_of(RULES[1], ["footer-2"])
    assert similarity(second, expected) > 0.95

    order = workflow.result(project["id"], "order").read_json("order.json")
    kinds = [[item["kind"] for item in page["articles"]] for page in order["pages"]]
    assert kinds == [["header", "article", "article", "ad", "footer"], ["header", "article", "article", "footer"]]
    assert order["links"] == [{"from": "p1-a3", "to": "p2-a2"}]
    assert order["pages"][1]["articles"][1]["continues"] == "p1-a3"


def test_layout_recognises_headers_footers_headings_captions_and_tables(rules_run):
    workflow, _, project = rules_run

    for page in (0, 1):
        layout = workflow.overlay(project["id"], "layout", page)
        kinds = {block["text"].split()[0]: block["kind"] for block in layout["blocks"]}
        assert kinds["Rychvaldský"] == "header"
        assert kinds["strana"] == "footer"
        ocr = workflow.overlay(project["id"], "ocr", page)
        assert sum(block["words"] for block in layout["blocks"]) == len(ocr["words"])
    first = {block["text"].split()[0]: block["kind"] for block in workflow.overlay(project["id"], "layout", 0)["blocks"]}
    assert first["Hasiči"] == "heading" and first["Obr."] == "caption" and first["Sbor"] == "body"
    second = workflow.overlay(project["id"], "layout", 1)["blocks"]
    [table] = [block for block in second if block["kind"] == "table"]
    assert table["text"].split() == [word for row in TABLE_ROWS for word in row]


def test_the_next_pages_request_offers_the_previous_pages_articles_for_continuation(rules_run):
    _, provider, _ = rules_run
    first, second = provider.requests_for("reading_order")[-2:]

    assert first.data["previous_page"] is None
    previous = second.data["previous_page"]
    assert previous["page"] == 1
    assert [item["id"] for item in previous["articles"]] == ["p1-a1", "p1-a2", "p1-a3", "p1-a4", "p1-a5"]
    assert first.instructions == second.instructions


def test_the_diagnostics_record_the_reading_order_ai_request_of_every_page(rules_run):
    workflow, _, project = rules_run
    records = workflow.diagnostics(project["id"])

    requests = [record for record in records if record["kind"] == "ai_request" and record["purpose"] == "reading_order"]
    assert len(requests) == 2 and all(record["outcome"] == "ok" for record in requests)
    pages = [record for record in records if record["kind"] == "reading_order"]
    assert [(record["index"], record["source"]) for record in pages] == [(0, "ai"), (1, "ai")]
    assert all(record["provider"] == "claude" and record["model"] == "scripted-model" and record["usage"]
               for record in pages)
