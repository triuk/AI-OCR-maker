"""Seam 1 with real Tesseract: Export writes the article text Markdown next to the OCR PDF."""
from __future__ import annotations

import re

import pymupdf
import pytest

from fakes import ScriptedProvider
from layout_pdfs import LEFT, RIGHT, Print, printed_pdf
from pdfs import czech_font_buffer
from ocrmaker.steps import implementations
from ocrmaker.steps.export_step import ExportStep
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow

SIZE = 11
# Marks in hyphenated paragraphs: where the printed line must break.
SOFT = "|"      # a hyphenation point: "zastupi-" / "telstvo"
REPEAT = "¦"    # after a genuine hyphen, Czech style: "česko-" / "-slovenskou"
PLAIN = "‖"     # after a genuine hyphen, without repeating it: "Frýdku-" / "Místku"
MARKS = SOFT + REPEAT + PLAIN


def broken_lines(text: str, width: float, size: float = SIZE) -> list[str] | None:
    """The text set into lines of the given width, words broken only at marks; None unless every mark breaks."""
    font = pymupdf.Font(fontbuffer=czech_font_buffer())

    def fits(line: str) -> bool:
        return font.text_length(line, fontsize=size) <= width

    lines, line = [], ""
    for word in text.split():
        plain = re.sub(f"[{MARKS}]", "", word)
        if fits(f"{line} {plain}".strip()):
            line = f"{line} {plain}".strip()
            continue
        mark = next((char for char in word if char in MARKS), None)
        head, tail = word.split(mark) if mark else (plain, "")
        head = head + "-" if mark == SOFT else head
        tail = "-" + tail if mark == REPEAT else tail
        if mark is None or not fits(f"{line} {head}".strip()):
            lines.append(line)
            line = plain
            continue
        lines.append(f"{line} {head}".strip())
        line = tail
    lines.append(line)
    broken = sum(1 for word in text.split() if any(mark in word for mark in MARKS))
    ends = sum(1 for current in lines if current.endswith("-"))
    return lines if ends == broken else None


def paragraph(key: str, left: float, top: float, bottom: float, text: str, widest: float) -> Print:
    """A paragraph printed with the given line breaks: the widest column width that breaks every mark."""
    width = widest
    while width > widest / 3:
        lines = broken_lines(text, width)
        if lines:
            return Print(key, (left, top, left + width + 4, bottom), "\n".join(lines))
        width -= 0.5
    raise AssertionError(f"{key}: no width breaks every marked word")


COLUMN = LEFT[1] - LEFT[0]
HYPHENATED = paragraph(
    "C1", LEFT[0], 76, 330,
    "Sbor dobrovolných hasičů oslavil sto let od svého založení. Gratulaci poslalo obecní zastupi|telstvo "
    "i česko-slovenské sdružení hasičů z Frýdku-‖Místku, které podporuje česko-¦slovenskou spolupráci "
    "sborů. Na slavnosti vystoupila dechová hudba a děti předvedly ukázku hašení malého požáru.", COLUMN)

PAGES = [
    [
        Print("header-1", (36, 16, 250, 28), "Rychvaldský zpravodaj květen 2000", size=9),
        Print("C-head", (LEFT[0], 44, LEFT[1], 68), "Hasiči slavili", size=17),
        HYPHENATED,
        Print("caption-1", (RIGHT[0], 120, RIGHT[1], 146), "Obr. 1: Nová cisterna před zbrojnicí", size=8),
        Print("E-head", (RIGHT[0], 176, RIGHT[1], 200), "Nový hřbitov", size=17),
        Print("E1", (RIGHT[0], 208, RIGHT[1], 370),
              "Zastupitelé projednali také plán na rozšíření hřbitova. Pozemek za kostelem obec koupí od "
              "místního zemědělského družstva a práce na oplocení"),
        Print("ad-1", (LEFT[0] + 10, 410, RIGHT[1] - 10, 460),
              "Prodám garáž v centru obce, cena dohodou. Volejte večer na telefon 603 123 456."),
        Print("footer-1", (300, 570, 384, 582), "strana 5", size=9),
    ],
    [
        Print("header-2", (36, 16, 250, 28), "Rychvaldský zpravodaj květen 2000", size=9),
        Print("E2", (LEFT[0], 44, LEFT[1], 200),
              "začnou ještě letos na podzim. Nové hroby budou připraveny nejpozději na jaře."),
        Print("F-head", (LEFT[0], 240, LEFT[1], 264), "Turnaj v kopané", size=17),
        Print("F1", (LEFT[0], 272, LEFT[1], 500),
              "Fotbalisté odehráli jarní turnaj na hřišti u nádraží. Domácí tým skončil na prvním místě "
              "a postoupil do krajského kola."),
        Print("footer-2", (36, 570, 120, 582), "strana 6", size=9),
    ],
]


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


def reading_order(request):
    """The scripted AI: page 1 has two articles (one with a caption) and an ad, page 2 continues the second."""
    page = request.data["page"]
    number = numbers_by_key(request, PAGES[page - 1])
    if page == 1:
        return {"articles": [article(number["header-1"], kind="header"),
                             article(number["C-head"], number["C1"], number["caption-1"]),
                             article(number["E-head"], number["E1"]),
                             article(number["ad-1"], kind="ad"),
                             article(number["footer-1"], kind="footer")]}
    [continued] = [item["id"] for item in request.data["previous_page"]["articles"]
                   if item["ends"].endswith("oplocení")]
    return {"articles": [article(number["header-2"], kind="header"),
                         article(number["E2"], continues=continued),
                         article(number["F-head"], number["F1"]),
                         article(number["footer-2"], kind="footer")]}


def markdown(workflow, project) -> str:
    names = [output["name"] for output in workflow.outputs(project["id"])]
    assert "zpravodaj-2000-05-ocr.md" in names
    return workflow.output(project["id"], "zpravodaj-2000-05-ocr.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    directory = tmp_path_factory.mktemp("article-text")
    provider = ScriptedProvider().on("reading_order", reading_order)
    workflow = Workflow(ProjectStore(directory / "library"), language=provider)
    project = workflow.run(workflow.create(printed_pdf(directory / "zpravodaj-2000-05.pdf", PAGES))["id"])
    return workflow, project, markdown(workflow, project)


def test_an_article_continued_on_the_next_page_is_one_article_with_a_page_comment_at_the_boundary(exported):
    _, _, text = exported

    assert re.search(r"a práce na oplocení\n<!-- page pdf=2 [^\n]*-->\nzačnou ještě letos", text), text
    # One article from its heading to its end, no other article in between.
    start, end = text.index("## Nový hřbitov"), text.index("nejpozději na jaře.")
    assert not re.search(r"<!-- (article|ad) ", text[start:end])
    assert text.count("Nový hřbitov") == 1


def front(text: str) -> dict[str, str]:
    """The front matter's fields (values as written, quotes removed)."""
    assert text.startswith("---\n")
    block = text[4:text.index("\n---\n")]
    return {key: value.strip().strip('"') for key, value in (line.split(":", 1) for line in block.splitlines())}


def body(text: str) -> str:
    """The text without front matter and without the lines that are only comments."""
    rest = text[text.index("\n---\n") + 5:]
    return "\n".join(line for line in rest.splitlines() if not re.fullmatch(r"<!--.*-->", line))


def test_the_page_comments_carry_the_pdf_page_the_printed_page_and_the_running_header_and_footer(exported):
    _, _, text = exported

    comments = re.findall(r"<!-- page [^\n]*-->", text)
    first = '<!-- page pdf=1 printed=5 header="Rychvaldský zpravodaj květen 2000" footer="strana 5" -->'
    second = '<!-- page pdf=2 printed=6 header="Rychvaldský zpravodaj květen 2000" footer="strana 6" -->'
    assert comments[0] == first and second in comments
    # Every text after a comment is printed on that page: the ad of page 1 follows page 2's continuation.
    assert comments == [first, second, first, second]
    assert text.index(comments[0]) < text.index("Hasiči slavili")
    ad = text.index("Prodám garáž")
    assert text.rindex(first, 0, ad) > text.rindex(second, 0, ad)
    assert text.index("Turnaj v kopané") > text.rindex(second)
    # Running headers and footers are not repeated as article text.
    assert "strana" not in body(text) and "Rychvaldský" not in body(text)


def test_line_end_hyphenation_is_joined_while_genuine_hyphens_are_kept(exported):
    workflow, project, text = exported

    assert "obecní zastupitelstvo i česko-slovenské sdružení" in text   # joined; mid-line hyphen kept
    assert "z Frýdku-Místku, které" in text                               # upper-case continuation
    assert "podporuje česko-slovenskou spolupráci" in text               # hyphen repeated on the next line
    assert "zastupi-" not in text and "-slovenskou" not in text.replace("česko-slovenskou", "")
    # The OCR PDF's text layer keeps the hyphenation as printed.
    [output] = [item for item in workflow.outputs(project["id"]) if item["name"].endswith(".pdf")]
    with pymupdf.open(workflow.output(project["id"], output["name"])) as document:
        words = [word[4] for word in document[0].get_text("words", sort=False)]
    assert {"zastupi-", "telstvo", "česko-", "-slovenskou"} <= set(words)


def test_the_front_matter_names_the_source_file_and_the_detected_title_and_date(exported):
    _, _, text = exported

    assert front(text) == {"source": "zpravodaj-2000-05.pdf", "title": "Rychvaldský zpravodaj",
                           "date": "květen 2000", "pages": "2"}


def test_captions_follow_their_article_and_ads_are_marked(exported):
    _, _, text = exported

    caption = text.index("<!-- caption -->\nObr. 1: Nová cisterna před zbrojnicí\n")
    assert text.index("ukázku hašení malého požáru.") < caption < text.index("## Nový hřbitov")
    assert re.search(r"<!-- ad id=p1-a\d+ -->\n\nProdám garáž v centru obce, cena dohodou\. "
                     r"Volejte večer na telefon 603 123 456\.\n", text)
    assert len(re.findall(r"<!-- article id=", text)) == 3
    assert text.count("Prodám garáž") == 1


class DictionaryExport(ExportStep):
    """Export with a fixed dictionary in place of hunspell."""

    def __init__(self, words):
        self.words = {word.lower() for word in words}

    def dictionary(self, ctx):
        return lambda word: word.lower() in self.words


UNCLEAR = [
    Print("G-head", (LEFT[0], 44, LEFT[1], 68), "Nová silnice", size=17),
    paragraph("G1", LEFT[0], 76, 200, "Nová silnice povede ve směru sever-‖jih přes celou obec až k nádraží.",
              COLUMN),
    paragraph("G2", LEFT[0], 220, 340, "Zastupitelé o její stavbě hlasovali na veřejném zasedá|ní v pondělí "
              "večer.", COLUMN),
    paragraph("G3", LEFT[0], 360, 560, "Obec vyvěsila modro-bílý prapor a nad radnicí vlaje i druhý "
              "velký modro-‖bílý prapor od hasičů.", COLUMN),
]


def test_unclear_hyphens_are_decided_by_the_dictionary_and_the_documents_own_words(tmp_path):
    provider = ScriptedProvider().on("reading_order", lambda request: {"articles": [
        article(*(block["number"] for block in request.data["blocks"]))]})
    steps = implementations() | {"export": DictionaryExport(["sever", "jih", "zasedání"])}
    workflow = Workflow(ProjectStore(tmp_path / "library"), steps=steps, language=provider)
    project = workflow.run(workflow.create(printed_pdf(tmp_path / "zpravodaj-2000-05.pdf", [UNCLEAR]))["id"])
    text = markdown(workflow, project)

    assert "ve směru sever-jih přes" in text          # both parts known, the joined word is not
    assert "na veřejném zasedání v pondělí" in text  # the joined word is known
    assert "velký modro-bílý prapor" in text           # the document prints it with a hyphen inside a line


def test_without_a_reading_order_each_page_is_written_in_recognition_order_and_no_text_is_lost(tmp_path):
    steps = {step: implementation for step, implementation in implementations().items()
             if step not in ("layout", "order")}
    workflow = Workflow(ProjectStore(tmp_path / "library"), steps=steps)
    project = workflow.run(workflow.create(printed_pdf(tmp_path / "zpravodaj-2000-05.pdf", PAGES))["id"])
    text = markdown(workflow, project)

    assert re.findall(r"<!-- page [^\n]*-->", text) == ["<!-- page pdf=1 -->", "<!-- page pdf=2 -->"]
    recognised = [word["text"] for page in (0, 1) for word in workflow.overlay(project["id"], "ocr", page)["words"]]
    # Every recognised word in recognition order; only line-end hyphens and spacing differ.
    assert re.sub(r"[-\s]", "", body(text)) == re.sub(r"[-\s]", "", "".join(recognised))
    assert front(text) == {"source": "zpravodaj-2000-05.pdf", "pages": "2"}


def test_the_report_counts_the_articles_page_numbers_and_hyphen_decisions_of_the_article_text(exported):
    workflow, project, _ = exported

    section = workflow.report(project["id"])["sections"]["export"]["article_text"]

    assert (section["articles"], section["ads"], section["captions"]) == (3, 1, 1)
    assert section["printed_pages"] == [5, 6]
    assert section["hyphens"] == {"joined": 1, "kept": 2}
    assert "Article text zpravodaj-2000-05-ocr.md: 3 articles, 1 ad, 1 caption" in workflow.report_summary(project["id"])
