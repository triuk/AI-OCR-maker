"""Seam 1 with real Tesseract: Layout (blocks) builds the blocks inside each layout region in reading order."""
from __future__ import annotations

from difflib import SequenceMatcher

import pymupdf

from fakes import ScriptedProvider
from layout_pdfs import RULES, Print, Rule, printed_pdf
from ocrmaker.step import Step
from ocrmaker.steps import implementations
from ocrmaker.store import ProjectStore
from ocrmaker.words import page_file
from ocrmaker.workflow import Workflow
from pdfs import CZECH_COLUMNS, printed_text_pdf, printed_words


def extracted_words(workflow, project, page: int = 0) -> list[str]:
    [output] = [item for item in workflow.outputs(project["id"]) if item["name"].endswith(".pdf")]
    with pymupdf.open(workflow.output(project["id"], output["name"])) as document:
        return [word[4] for word in document[page].get_text("words", sort=False)]


def words_of(items, keys) -> list[str]:
    by_key = {item.key: item for item in items}
    return [word for key in keys for word in by_key[key].words]


def similarity(words, expected) -> float:
    return SequenceMatcher(None, words, expected).ratio()


CONCERT = [Print("title", (40, 40, 380, 60), "Jarní koncert", 16),
           # Wraps so that its last line is the one word "hudba.", which Tesseract measures taller than body text.
           Print("concert", (40, 70, 240, 200), "Na letošním jarním koncertě základní umělecké školy vystoupili žáci "
                 "všech oborů a sál kulturního domu byl zcela zaplněn. Diváky nejvíce potěšila lidová a dechová "
                 "hudba."),
           Print("next", (40, 210, 240, 300), "Další odstavec začíná zde a pokračuje ještě jedním řádkem textu o "
                 "škole.")]


LAYOUT_STEPS = ("import", "prepare", "regions", "ocr", "layout")


def laid_out(tmp_path, items, parts=None, kinds=None, steps=LAYOUT_STEPS):
    """A page printed from the items, divided into layout regions (ADR 0006) and run through `steps` (by default
    up to Layout). `parts` lists the regions' articles, each a list of regions, each a list of item keys, and
    `kinds` their kinds (article by default); by default one region holds every item."""
    pdf = printed_pdf(tmp_path / "zpravodaj.pdf", [items])
    by_key = {item.key: item.rect for item in items}

    def box(keys, pad=4):
        rects = [by_key[key] for key in keys]
        x0, y0 = min(r[0] for r in rects) - pad, min(r[1] for r in rects) - pad
        x1, y1 = max(r[2] for r in rects) + pad, max(r[3] for r in rects) + pad
        return [round(x0 * 1000 / 420), round(y0 * 1000 / 595), round(x1 * 1000 / 420), round(y1 * 1000 / 595)]
    parts = parts or [[list(by_key)]]
    answer = {"parts": [{"kind": kind, "title": "", "regions": [{"box": box(keys), "orientation": "0"}
                                                                for keys in regions]}
                        for regions, kind in zip(parts, kinds or ["article"] * len(parts))]}
    shipped = implementations()
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=ScriptedProvider().on("layout_regions", answer),
                        steps={step: shipped[step] for step in steps})
    project = workflow.create(pdf)
    return workflow, workflow.run(project["id"])


def block_texts(workflow, project, page: int = 0) -> list[tuple[str, str]]:
    """Each Layout block's kind and whole text, in Layout's order."""
    recognised = workflow.result(project["id"], "ocr").read_json(page_file(page))
    texts = {word["id"]: word["text"] for line in recognised["lines"] for word in line["words"]}
    layout = workflow.result(project["id"], "layout").read_json(page_file(page))
    return [(block["kind"], " ".join(texts[word] for line in block["lines"] for word in line))
            for block in layout["blocks"]]


def test_a_one_word_last_line_of_a_paragraph_stays_in_the_paragraph(tmp_path):
    workflow, project = laid_out(tmp_path, CONCERT)

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
    workflow, project = laid_out(tmp_path, BUS, [[["title"], ["left"], ["right"]]])

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
    workflow, project = laid_out(tmp_path, NEIGHBOURS,
                                 [[["carnival", "carnival text"]], [["vaccination", "vaccination text"]]])

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
    workflow, project = laid_out(tmp_path, TIMETABLE)

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
    workflow, project = laid_out(tmp_path, PROGRAMME)

    texts = [text for _, text in block_texts(workflow, project)]
    assert " ".join(texts).split() == words_of(PROGRAMME, [item.key for item in PROGRAMME])
    assert [text.split()[0] for text in texts] == ["2.", "12.", "22.", "Pořadatel:", "Termín:", "Místo"]


# A table of births as a newsletter prints it: column headings over a printed rule, no lines between the cells,
# and cells so close that Tesseract reads a date and a name as one piece of a line.
BIRTH_COLUMNS = (40, 89, 180, 245)
BIRTH_ROWS = [("datum", "jméno", "místo narození", "jména rodičů"),
              ("10. 2. 2000", "Kristýna Žídková", "Karviná", "Alois a Pavla"),
              ("18. 2. 2000", "Kateřina Kulíšková", "Bohumín", "Martin a Pavla"),
              ("14. 2. 2000", "Michela Šeligová", "Ostrava", "Pavel a Šárka"),
              ("6. 3. 2000", "Barbora Wasniowská", "Bohumín", "Jiří a Alice"),
              ("7. 3. 2000", "Václav Kožušník", "Ostrava", "Jan a Andrea")]
BIRTHS = [Print("title", (40, 30, 200, 50), "Narodili se", 14),
          *[Print(f"r{row}c{column}", (left, 58 + 15 * row + (4 if row else 0), right,
                                       71 + 15 * row + (4 if row else 0)), text, 10)
            for row, cells in enumerate(BIRTH_ROWS)
            for column, (text, left, right) in enumerate(zip(cells, BIRTH_COLUMNS, BIRTH_COLUMNS[1:] + (380,)))],
          Rule("heading rule", (38, 74, 382, 74)),
          Rule("bottom rule", (38, 158, 382, 158)),
          Print("after", (40, 168, 380, 210), "Všem rodičům srdečně blahopřejeme a dětem přejeme hodně zdraví "
                "a radosti do života.")]


def test_a_table_under_a_rule_below_its_column_headings_is_read_row_by_row(tmp_path):
    workflow, project = laid_out(tmp_path, BIRTHS)

    texts = block_texts(workflow, project)
    assert [text for kind, text in texts if kind == "table"] == [" ".join(cell for row in BIRTH_ROWS for cell in row)]
    assert texts[-1] == ("body", BIRTHS[-1].text)


# Two articles over two others, each in two columns, a rule between them; the columns above end in short lines
# right over the rule, which look like the column headings of a table.
RULED_COLUMNS = [Print("a-left", (40, 40, 200, 98), "Obecní zastupitelstvo schválilo rozpočet na příští rok "
                       "a opravu chodníků v centru obce."),
                 Print("a-end", (40, 100, 200, 114), "To je vše."),
                 Print("c-right", (220, 40, 380, 98), "Knihovna rozšiřuje otevírací dobu a nabízí čtenářům "
                       "nové tituly pro děti i dospělé."),
                 Print("c-end", (220, 100, 380, 114), "Přijďte k nám."),
                 Rule("rule", (38, 117, 382, 117)),
                 Print("b-left", (40, 122, 200, 200), "Hasiči oslavili výročí založení sboru slavnostní "
                       "schůzí a ukázkou staré techniky na návsi před obecním úřadem."),
                 Print("d-right", (220, 122, 380, 200), "Fotbalisté vyhráli okresní přebor a postoupili do "
                       "vyšší soutěže, kde se utkají s týmy z celého kraje.")]


def test_a_rule_between_articles_in_columns_is_no_table(tmp_path):
    workflow, project = laid_out(tmp_path, RULED_COLUMNS,
                                 [[["a-left", "a-end"]], [["b-left"]], [["c-right", "c-end"]], [["d-right"]]])

    texts = [text for kind, text in block_texts(workflow, project) if kind != "table"]
    # Column by column, the geometric order.
    assert " ".join(texts).split() == words_of(RULED_COLUMNS, ["a-left", "a-end", "b-left", "c-right", "c-end",
                                                               "d-right"])


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
            box = [words[0]["bbox"][0], min(w["bbox"][1] for w in words), words[-1]["bbox"][2],
                   max(w["bbox"][3] for w in words)]
            return {"id": f"p1-l{len(lines) + 1}", "region": "p1-r1", "bbox": box, "baseline": None, "block": 0,
                    "paragraph": 0, "words": words}

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


def test_layout_takes_headers_footers_and_ads_from_the_regions_and_headings_and_captions_from_the_size(tmp_path,
                                                                                                         client):
    first = RULES[0]
    workflow, project = laid_out(tmp_path, first,
                                 [[["header-1"]], [["C-head", "C1"], ["caption-1"]], [["E1"]], [["ad-1"]],
                                  [["footer-1"]]],
                                 ["header", "article", "article", "ad", "footer"], LAYOUT_STEPS + ("order",))

    blocks = {block["text"].split()[0]: block for block in workflow.overlay(project["id"], "layout", 0)["blocks"]}
    kinds = {word: block["kind"] for word, block in blocks.items()}
    assert kinds == {"Rychvaldský": "header", "Hasiči": "heading", "Sbor": "body", "Obr.": "caption",
                     "Zastupitelé": "body", "Prodám": "ad", "strana": "footer"}
    # The preview shows the blocks of an ad article as ads; Layout's own kinds stay as Layout decided them.
    stored = workflow.result(project["id"], "layout").read_json(page_file(0))["blocks"]
    assert "ad" not in {block["kind"] for block in stored}
    script = client.get("/previews/layout.js").text
    assert 'kind !== "ad"' not in script and '"inzerát"' in script


# A form whose middle row is set in small print, which Layout takes for a caption by its size alone.
FORM = [Print("title", (40, 40, 380, 62), "Zveme Vás na běh", 16),
        Print("organiser", (40, 70, 380, 100), "Pořadatel: Sokolská župa Těšínská ve spolupráci s místní "
              "tělocvičnou jednotou a městem."),
        Print("place", (40, 104, 380, 116), "Místo konání: hřiště u základní školy na sídlišti", size=8),
        Print("money", (40, 120, 380, 170), "Zúčastnit se můžete i formou peněžního příspěvku, který zašlete "
              "na konto pro výzkum rakoviny u spořitelny.")]


def test_small_print_inside_an_article_stays_where_it_stands(tmp_path):
    workflow, project = laid_out(tmp_path, FORM)

    texts = block_texts(workflow, project)
    assert "caption" in [kind for kind, _ in texts]
    assert similarity(" ".join(text for _, text in texts).split(), words_of(FORM, [item.key for item in FORM])) > 0.95
