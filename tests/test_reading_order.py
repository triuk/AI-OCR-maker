"""Seam 1 with real Tesseract: Layout builds blocks and Reading order puts them into articles for the OCR PDF."""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

import pymupdf
import pytest

from fakes import ScriptedProvider, answer_spots, run_and_export
from layout_pdfs import CROSSING, CROSSING_COLUMNS, CROSSING_READING, RULES, TABLE_ROWS, Print, printed_pdf
from ocrmaker.language import LanguageError
from ocrmaker.step import Step
from ocrmaker.steps import implementations
from ocrmaker.store import ProjectStore
from ocrmaker.words import page_file
from ocrmaker.workflow import Workflow
from pdfs import CZECH_COLUMNS, printed_text_pdf, printed_words


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
    [output] = [item for item in workflow.outputs(project["id"]) if item["name"].endswith(".pdf")]
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
    provider = ScriptedProvider().on("readthrough", {"words": []})
    provider.on("reading_order", lambda request: {"articles": [
        article(*(numbers_by_key(request, CROSSING)[key] for key in CROSSING_READING))]})
    workflow = Workflow(ProjectStore(directory / "library"), language=provider)
    project = run_and_export(workflow,
                             workflow.create(printed_pdf(directory / "zpravodaj-2000-05.pdf", [CROSSING]))["id"])
    return workflow, provider, project


def rerun_order(crossing, *answers):
    workflow, provider, project = crossing
    provider.requests.clear()
    provider.on("reading_order", *answers)
    project = workflow.rerun(project["id"], "order")
    # The rerun goes on up to manual review; an export then writes the outputs, as the user would.
    if states(project)["order"]["state"] in ("done", "warning"):
        project = workflow.export(project["id"])
    return project


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
    project = run_and_export(workflow, workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [CROSSING]))["id"])

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

    The scripted AI deliberately answers the groups out of order; the rules put each group where it belongs.
    """
    directory = tmp_path_factory.mktemp("rules")
    provider = ScriptedProvider().on("readthrough", {"words": []})

    def answer(request):
        page = request.data["page"]
        # The table is one block, found by its first cell.
        number = numbers_by_key(request, [item for item in RULES[page - 1]
                                          if not item.key.startswith("T-") or item.key == "T-r1"])
        if page == 1:
            return {"articles": [article(number["footer-1"], kind="footer"),
                                 article(number["C-head"], number["C1"], number["caption-1"]),
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
    project = run_and_export(workflow, workflow.create(printed_pdf(directory / "zpravodaj-2000-05.pdf", RULES))["id"])
    return workflow, provider, project


def test_headers_footers_ads_and_continuations_are_placed_by_the_rules(rules_run):
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


# A form whose middle row is set in small print, which Layout takes for a caption by its size alone.
FORM = [Print("title", (40, 40, 380, 62), "Zveme Vás na běh", 16),
        Print("organiser", (40, 70, 380, 100), "Pořadatel: Sokolská župa Těšínská ve spolupráci s místní "
              "tělocvičnou jednotou a městem."),
        Print("place", (40, 104, 380, 116), "Místo konání: hřiště u základní školy na sídlišti", size=8),
        Print("money", (40, 120, 380, 170), "Zúčastnit se můžete i formou peněžního příspěvku, který zašlete "
              "na konto pro výzkum rakoviny u spořitelny.")]


def test_small_print_inside_an_article_stays_where_the_ai_reads_it(tmp_path):
    # Only the AI sees whether a block Layout calls a caption by its small size really captions a picture.
    provider = ScriptedProvider().on("readthrough", {"words": []}).on("reconstruction", answer_spots())
    provider.on("reading_order", lambda request: {"articles": [
        article(*(numbers_by_key(request, FORM)[item.key] for item in FORM))]})
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=provider)
    project = run_and_export(workflow, workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [FORM]))["id"])

    assert "caption" in [kind for kind, _ in block_texts(workflow, project)]
    assert similarity(extracted_words(workflow, project), words_of(FORM, [item.key for item in FORM])) > 0.95


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


def test_the_layout_preview_shows_the_blocks_reading_order_put_into_an_ad_as_ads(rules_run, client):
    workflow, _, project = rules_run

    blocks = {block["text"].split()[0]: block for block in workflow.overlay(project["id"], "layout", 0)["blocks"]}
    assert blocks["Prodám"]["kind"] == "ad" and blocks["Sbor"]["kind"] == "body"
    stored = workflow.result(project["id"], "layout").read_json("pages/page-0001.json")["blocks"]
    assert "ad" not in {block["kind"] for block in stored}  # Layout's own kinds stay as Layout decided them
    script = client.get("/previews/layout.js").text
    assert 'kind !== "ad"' not in script and '"inzerát"' in script


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


CONCERT = [Print("title", (40, 40, 380, 60), "Jarní koncert", 16),
           # Wraps so that its last line is the one word "hudba.", which Tesseract measures taller than body text.
           Print("concert", (40, 70, 240, 200), "Na letošním jarním koncertě základní umělecké školy vystoupili žáci "
                 "všech oborů a sál kulturního domu byl zcela zaplněn. Diváky nejvíce potěšila lidová a dechová "
                 "hudba."),
           Print("next", (40, 210, 240, 300), "Další odstavec začíná zde a pokračuje ještě jedním řádkem textu o "
                 "škole.")]


def block_texts(workflow, project, page: int = 0) -> list[tuple[str, str]]:
    """Each Layout block's kind and whole text, in Layout's order."""
    recognised = workflow.result(project["id"], "ocr").read_json(page_file(page))
    texts = {word["id"]: word["text"] for line in recognised["lines"] for word in line["words"]}
    layout = workflow.result(project["id"], "layout").read_json(page_file(page))
    return [(block["kind"], " ".join(texts[word] for line in block["lines"] for word in line))
            for block in layout["blocks"]]


def test_a_one_word_last_line_of_a_paragraph_stays_in_the_paragraph(tmp_path):
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=None)
    project = workflow.run(workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [CONCERT]))["id"])

    texts = block_texts(workflow, project)
    assert [kind for kind, _ in texts] == ["heading", "body", "body"]
    assert texts[1][1].endswith("dechová hudba.")


BUS = [Print("title", (40, 40, 300, 62), "Do nového domu již jezdí autobus", 18),
       Print("left", (40, 63, 150, 250), "Do nového domu s pečovatelskou službou na Podlesí je od dubna zajištěno "
             "spojení minibusy a to podle následujícího jízdního řádu."),
       # Right under the heading, the right column starts a little higher than the left one, as on a real scan.
       Print("right", (170, 60, 285, 250), "Linka odjíždí od domu ve dvanáct hodin ze zastávky na rozcestí a "
             "vrací se odpoledne ze zastávky u pošty.")]


def test_a_heading_over_two_columns_does_not_take_the_first_line_of_one_column(tmp_path):
    # The right column's first line, narrow and within the heading's width, looked like the heading's short
    # last line; that exception is meant for a last line Tesseract measures larger, never for smaller text.
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=None)
    project = workflow.run(workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [BUS]))["id"])

    texts = block_texts(workflow, project)
    assert texts[0] == ("heading", "Do nového domu již jezdí autobus")
    assert " ".join(text for _, text in texts).split() == words_of(BUS, ["title", "left", "right"])


# The headings of two articles side by side, a narrow gap apart, set in different sizes.
NEIGHBOURS = [Print("carnival", (40, 40, 172, 60), "Karnevalové show 2000", 13),
              Print("vaccination", (180, 38, 345, 60), "Povinné očkování psů", 18),
              Print("carnival text", (40, 66, 160, 200), "V mateřské škole se konal dětský karneval, na který "
                    "se převlékly do masek i paní učitelky."),
              Print("vaccination text", (180, 66, 345, 200), "Oznamujeme občanům, že v úterý proběhne hromadné "
                    "očkování psů starších tří měsíců proti vzteklině.")]


def test_two_headings_side_by_side_in_different_sizes_are_blocks_of_their_own(tmp_path):
    # Tesseract reads both headings as one line; the gap between them is narrow for their size, but the size
    # changes there, so they are two headings.
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=None)
    project = workflow.run(workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [NEIGHBOURS]))["id"])

    texts = [text for _, text in block_texts(workflow, project)]
    assert "Karnevalové show 2000" in texts and "Povinné očkování psů" in texts


# A timetable and a bullet list: each label stands a wide gap before its text, which wraps under itself.
TIMETABLE = [Print("time1", (40, 40, 75, 54), "11.48"),
             Print("entry1", (100, 40, 300, 82), "Odjezd od domu s pečovatelskou službou s možností využití "
                   "linky číslo sedm ze zastávky na rozcestí"),
             Print("time2", (40, 85, 75, 99), "15.52"),
             Print("entry2", (100, 85, 300, 127), "Odjezd z rozcestí k domu s pečovatelskou službou s možností "
                   "využití linky číslo sedm ze zastávky u pošty"),
             Print("time3", (40, 130, 75, 144), "16.03"),
             Print("entry3", (100, 130, 300, 172), "Odjezd od domu na rozcestí s možností využití linky číslo "
                   "sedm ze zastávky na rozcestí v Rychvaldě"),
             Print("bullet1", (40, 190, 50, 204), "-"),
             Print("item1", (75, 190, 300, 204), "na odboru výstavby městského úřadu"),
             Print("bullet2", (40, 208, 50, 222), "-"),
             Print("item2", (75, 208, 300, 222), "v pracovních dnech od osmi do šestnácti hodin")]


def test_a_time_or_bullet_a_wide_gap_before_its_text_is_read_with_that_text(tmp_path):
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=None)
    project = workflow.run(workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [TIMETABLE]))["id"])

    texts = [text for _, text in block_texts(workflow, project)]
    assert " ".join(texts).split() == words_of(TIMETABLE, [item.key for item in TIMETABLE])
    # Each row is a paragraph of its own: the label's line and the text's wrapped lines below it.
    assert [text.split()[0] for text in texts] == ["11.48", "15.52", "16.03", "-", "-"]


# A programme of dates and a form of labels: a key column of short entries, each a wide gap before its text.
PROGRAMME = [Print("date1", (40, 40, 100, 54), "2. a 3. 5."),
             Print("event1", (100, 40, 330, 68), "Výprodej záclon od devíti do sedmnácti hodin v předsálí "
                   "kulturního střediska"),
             Print("date2", (40, 72, 100, 86), "12. 5."),
             Print("event2", (100, 72, 330, 86), "Besídka základní školy ke svátku matek"),
             Print("date3", (40, 90, 100, 104), "22. a 23. 5."),
             Print("event3", (100, 90, 330, 118), "Výprodej textilu od devíti do sedmnácti hodin ve velkém "
                   "sále"),
             Print("key1", (40, 140, 110, 154), "Pořadatel:"),
             Print("value1", (120, 140, 330, 168), "Sokolská župa Těšínská ve spolupráci s místní "
                   "tělocvičnou jednotou"),
             Print("key2", (40, 172, 110, 186), "Termín:"),
             Print("value2", (120, 172, 330, 186), "Pátek devatenáctého května v šestnáct hodin"),
             Print("key3", (40, 190, 110, 204), "Místo konání:"),
             Print("value3", (120, 190, 330, 204), "Hřiště u základní školy na sídlišti")]


def test_a_column_of_dates_or_form_labels_is_read_row_by_row_with_the_text_beside_it(tmp_path):
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=None)
    project = workflow.run(workflow.create(printed_pdf(tmp_path / "zpravodaj.pdf", [PROGRAMME]))["id"])

    texts = [text for _, text in block_texts(workflow, project)]
    assert " ".join(texts).split() == words_of(PROGRAMME, [item.key for item in PROGRAMME])
    assert [text.split()[0] for text in texts] == ["2.", "12.", "22.", "Pořadatel:", "Termín:", "Místo"]


class StrayOcr(Step):
    """Local OCR that reads a printed paragraph exactly and adds the stray artefacts of a real scan below its
    last line: a speck read as "-" and a dot, each a tiny box of its own line."""

    id = "ocr"

    def __init__(self, printed):
        self.printed = printed

    def run(self, ctx):
        rows: dict[float, list] = {}
        for text, box in self.printed:
            rows.setdefault(round(box[1], 1), []).append((text, box))
        lines, count = [], 0

        def line(items):
            nonlocal count
            words = []
            for text, box in items:
                count += 1
                words.append({"id": f"p1-w{count}", "text": text, "bbox": [round(v, 2) for v in box],
                              "confidence": 96.0, "chars": []})
            return {"id": f"p1-l{len(lines) + 1}", "bbox": [words[0]["bbox"][0], min(w["bbox"][1] for w in words),
                                                           words[-1]["bbox"][2], max(w["bbox"][3] for w in words)],
                    "baseline": None, "block": 0, "paragraph": 0, "words": words}

        for items in rows.values():
            lines.append(line(items))
        last = list(rows.values())[-1][0][1]
        height = last[3] - last[1]
        lines.append(line([("-", [last[0] + 60, last[3] + 0.4 * height, last[0] + 63, last[3] + 0.5 * height])]))
        lines.append(line([(".", [last[0] + 90, last[3] + 0.3 * height, last[0] + 91.5, last[3] + 0.5 * height])]))
        ctx.output.write_json(page_file(0), {"index": 0, "lines": lines})
        ctx.output.write_json("ocr.json", {"engine": {"name": "stray"}, "pages": []})


def test_a_small_stray_artefact_below_a_paragraph_joins_the_paragraph(tmp_path):
    text = CZECH_COLUMNS[0]
    steps = {**implementations(), "ocr": StrayOcr(printed_words(text))}
    workflow = Workflow(ProjectStore(tmp_path / "library"), steps=steps, language=None)
    project = workflow.run(workflow.create(printed_text_pdf(tmp_path / "zpravodaj.pdf", text))["id"])

    [(kind, written)] = block_texts(workflow, project)
    assert kind == "body" and set(written.split()[-2:]) == {"-", "."}
