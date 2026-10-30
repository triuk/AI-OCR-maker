"""Generated scans of laid-out pages: text printed into given boxes, so layout and reading order are known.

A page is a list of Print items. Each one names its role in the ground truth (`key`), so a test can find the
block holding it by its first word and state the expected reading order as a list of keys.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdfs import czech_font_buffer


@dataclass(frozen=True)
class Print:
    key: str
    rect: tuple[float, float, float, float]
    text: str
    size: float = 11
    frame: bool = False  # a printed frame around the text, as around an ad

    @property
    def words(self) -> list[str]:
        return self.text.split()


def printed_pdf(path: Path, pages: list[list[Print]], *, size=(420, 595), dpi: int = 300) -> Path:
    """A scan PDF: every page one grey raster image of the printed items, no text layer."""
    font = pymupdf.Font(fontbuffer=czech_font_buffer())
    output = pymupdf.open()
    for items in pages:
        printed = pymupdf.open()
        page = printed.new_page(width=size[0], height=size[1])
        for item in items:
            writer = pymupdf.TextWriter(page.rect)
            rect = pymupdf.Rect(item.rect)
            if item.frame:
                page.draw_rect(rect + (-5, -5, 5, 5), color=(0, 0, 0), width=1.2)
            overflow = writer.fill_textbox(rect, item.text, font=font, fontsize=item.size)
            assert not overflow, f"{item.key} does not fit its box"
            writer.write_text(page)
        image = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        scanned = output.new_page(width=size[0], height=size[1])
        scanned.insert_image(scanned.rect, stream=image.tobytes("png"), keep_proportion=False)
    output.save(path)
    return path


# Two columns with two articles that cross: article A starts in the left column and continues at the top
# of the right one, article B starts lower in the left column and continues below A in the right one. A
# person reads A (heading, left, right) and then B; a column-by-column order mixes them.
LEFT, RIGHT = (36, 198), (222, 384)
CROSSING = [
    Print("A-head", (LEFT[0], 40, LEFT[1], 64), "Oprava školy", size=17),
    Print("A1", (LEFT[0], 72, LEFT[1], 290),
          "Obecní zastupitelstvo schválilo na svém zasedání rozpočet na příští rok. Největší část peněz "
          "půjde na opravu školy a chodníků v centru obce. Starosta poděkoval všem občanům, kteří se "
          "zúčastnili veřejného projednání."),
    Print("B-head", (LEFT[0], 320, LEFT[1], 344), "Nové knihy", size=17),
    Print("B1", (LEFT[0], 352, LEFT[1], 560),
          "Knihovna rozšiřuje otevírací dobu a nabízí čtenářům nové tituly pro děti i dospělé. Ve čtvrtek "
          "proběhne beseda s místním kronikářem o historii hornictví v našem kraji."),
    Print("A2", (RIGHT[0], 40, RIGHT[1], 290),
          "Stavební práce začnou hned po skončení školního roku a potrvají celé prázdniny. Žáci se do "
          "opravených tříd vrátí v září, kdy bude hotová i nová jídelna."),
    Print("B2", (RIGHT[0], 320, RIGHT[1], 560),
          "Čtenáři mohou nové knihy rezervovat také telefonicky. Hasiči zvou na tradiční pouť, kde nebude "
          "chybět hudba ani výborné občerstvení."),
]
CROSSING_READING = ["A-head", "A1", "A2", "B-head", "B1", "B2"]
CROSSING_COLUMNS = ["A-head", "A1", "B-head", "B1", "A2", "B2"]

# Two pages with every kind of block: running header and footer, an article with a caption, an ad, an
# article that continues on the next page, and on page 2 its continuation, another article and a table.
RULES = [
    [
        Print("header-1", (36, 16, 250, 28), "Rychvaldský zpravodaj květen 2000", size=9),
        Print("C-head", (LEFT[0], 44, LEFT[1], 68), "Hasiči slavili", size=17),
        Print("C1", (LEFT[0], 76, LEFT[1], 370),
              "Sbor dobrovolných hasičů oslavil sto let od svého založení. Na slavnosti vystoupila "
              "dechová hudba a děti předvedly ukázku hašení malého požáru."),
        # The picture above the caption is left blank: only its caption is printed.
        Print("caption-1", (RIGHT[0], 150, RIGHT[1], 176), "Obr. 1: Nová cisterna před zbrojnicí", size=8),
        Print("E1", (RIGHT[0], 200, RIGHT[1], 370),
              "Zastupitelé projednali také plán na rozšíření hřbitova. Pozemek za kostelem obec koupí od "
              "místního zemědělského družstva a práce na oplocení"),
        Print("ad-1", (LEFT[0] + 10, 410, RIGHT[1] - 10, 460),
              "Prodám garáž v centru obce, cena dohodou. Volejte večer na telefon 603 123 456.", frame=True),
        Print("footer-1", (190, 570, 240, 582), "strana 1", size=9),
    ],
    [
        Print("header-2", (36, 16, 250, 28), "Rychvaldský zpravodaj květen 2000", size=9),
        Print("E2", (LEFT[0], 44, LEFT[1], 200),
              "začnou ještě letos na podzim. Nové hroby budou připraveny nejpozději na jaře příštího roku."),
        Print("F-head", (LEFT[0], 240, LEFT[1], 264), "Turnaj v kopané", size=17),
        Print("F1", (LEFT[0], 272, LEFT[1], 500),
              "Fotbalisté odehráli jarní turnaj na hřišti u nádraží. Domácí tým skončil na prvním místě "
              "a postoupil do krajského kola."),
        Print("T-r1", (RIGHT[0], 60, RIGHT[0] + 70, 76), "Družstvo"),
        Print("T-r1b", (RIGHT[0] + 90, 60, RIGHT[0] + 150, 76), "Body"),
        Print("T-r2", (RIGHT[0], 82, RIGHT[0] + 70, 98), "Sokol"),
        Print("T-r2b", (RIGHT[0] + 90, 82, RIGHT[0] + 150, 98), "21"),
        Print("T-r3", (RIGHT[0], 104, RIGHT[0] + 70, 120), "Baník"),
        Print("T-r3b", (RIGHT[0] + 90, 104, RIGHT[0] + 150, 120), "18"),
        Print("T-r4", (RIGHT[0], 126, RIGHT[0] + 70, 142), "Slavoj"),
        Print("T-r4b", (RIGHT[0] + 90, 126, RIGHT[0] + 150, 142), "15"),
        Print("footer-2", (190, 570, 240, 582), "strana 2", size=9),
    ],
]
TABLE_ROWS = [["Družstvo", "Body"], ["Sokol", "21"], ["Baník", "18"], ["Slavoj", "15"]]
