"""Generated fixture PDFs; no real scans are committed."""
from __future__ import annotations

from pathlib import Path

import pymupdf

# The built-in Helvetica has no Czech glyphs; tickets that need diacritics bring their own font.
SAMPLE_TEXT = "Rychvaldsky zpravodaj, cislo 5"
A4 = (595, 842)


def scan_pdf(path: Path, *, pages: int = 1, text: str = SAMPLE_TEXT, dpi: int = 150, size=A4,
             rotation: int = 0, cropbox: tuple | None = None, mark: tuple | None = None,
             invisible_text: str | None = None, text_under_scan: str | None = None) -> Path:
    """A scan-like PDF: each page is one raster image of printed text covering the page, without a text layer.

    size is the page size in points, dpi the scan image's resolution (pixels per inch of the page).
    rotation sets the page's /Rotate; cropbox (x0, y0, x1, y1, top-left origin as PyMuPDF uses it) sets
    its CropBox. mark is a rectangle in the same coordinates printed solid black into the scan.
    invisible_text adds an invisible text layer (render mode 3), as an earlier OCR would; text_under_scan adds
    painted text hidden beneath the scan image, the other way earlier OCR tools hide their text.
    """
    source = pymupdf.open()
    output = pymupdf.open()
    for number in range(pages):
        page = source.new_page(width=size[0], height=size[1])
        if text:
            page.insert_text((72, 100), f"{text} ({number + 1})", fontsize=14, fontname="helv")
        if mark:
            page.draw_rect(pymupdf.Rect(mark), color=(0, 0, 0), fill=(0, 0, 0))
        image = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        scanned = output.new_page(width=size[0], height=size[1])
        if text_under_scan:
            scanned.insert_text((72, 100), text_under_scan, fontsize=14, fontname="helv")
        scanned.insert_image(scanned.rect, stream=image.tobytes("png"), keep_proportion=False)
        if invisible_text:
            scanned.insert_text((72, 100), invisible_text, fontsize=14, fontname="helv", render_mode=3)
        if cropbox:
            scanned.set_cropbox(pymupdf.Rect(cropbox))
        if rotation:
            scanned.set_rotation(rotation)
    output.save(path)
    return path


def text_pdf(path: Path, text: str = SAMPLE_TEXT) -> Path:
    """A born-digital PDF with visible text."""
    document = pymupdf.open()
    document.new_page().insert_text((72, 100), text, fontsize=12)
    document.save(path)
    return path


def drawing_pdf(path: Path) -> Path:
    """A page with vector drawing only: no scan image and no text."""
    document = pymupdf.open()
    page = document.new_page(width=200, height=300)
    page.draw_rect(pymupdf.Rect(20, 20, 120, 80), color=(0, 0, 0), fill=(0, 0, 0))
    document.save(path)
    return path


def embedded_images(path: Path) -> list[pymupdf.Pixmap]:
    """The scan image of every page, decoded, as the source PDF stores it."""
    with pymupdf.open(path) as document:
        return [pymupdf.Pixmap(document, page.get_images()[0][0]) for page in document]


# Known Czech text for recognition tests, one string per column; the ground truth is its words in order.
CZECH_COLUMNS = (
    "Obecní zastupitelstvo schválilo na svém zasedání rozpočet na příští rok. Největší část peněz "
    "půjde na opravu školy a chodníků v centru obce. Starosta poděkoval všem občanům, kteří se "
    "zúčastnili veřejného projednání, a připomněl, že žádosti o příspěvky spolků lze podávat do "
    "konce měsíce.",
    "Knihovna rozšiřuje otevírací dobu a nabízí čtenářům nové tituly pro děti i dospělé. Ve čtvrtek "
    "proběhne beseda s místním kronikářem o historii hornictví. Hasiči zvou na tradiční pouť, kde "
    "nebude chybět hudba, kolotoče ani výborné občerstvení. Přijďte se pobavit s celou rodinou.",
)


def czech_font_buffer(name: str = "tiro") -> bytes:
    """A built-in MuPDF font with Czech glyphs (tiro is Times-like, helv Helvetica-like)."""
    return pymupdf.Font(name).buffer


def columns_pdf(path: Path, columns=CZECH_COLUMNS, *, size=(420, 595), dpi: int = 300, fontsize: float = 11,
                margin: float = 36, gap: float = 24, rotation: int = 0) -> Path:
    """A scan of a page printed in columns of known Czech text: one grey raster image, no text layer.

    With rotation (90, 180 or 270) the scan is stored turned the other way and the page's /Rotate turns it
    back, as a scanner does for a page fed sideways: the page displays upright text.
    """
    font = pymupdf.Font(fontbuffer=czech_font_buffer())
    printed = pymupdf.open()
    page = printed.new_page(width=size[0], height=size[1])
    width = (size[0] - 2 * margin - gap * (len(columns) - 1)) / len(columns)
    for number, text in enumerate(columns):
        x0 = margin + number * (width + gap)
        writer = pymupdf.TextWriter(page.rect)
        writer.fill_textbox(pymupdf.Rect(x0, margin, x0 + width, size[1] - margin), text, font=font,
                            fontsize=fontsize)
        writer.write_text(page)
    image = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    output = pymupdf.open()
    stored = size[::-1] if rotation % 180 else size
    scanned = output.new_page(width=stored[0], height=stored[1])
    scanned.insert_image(scanned.rect, stream=image.tobytes("png"), keep_proportion=False, rotate=rotation)
    scanned.set_rotation(rotation)
    output.save(path)
    return path


def ground_truth(columns=CZECH_COLUMNS) -> list[str]:
    return [word for text in columns for word in text.split()]
