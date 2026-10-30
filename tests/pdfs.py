"""Generated fixture PDFs; no real scans are committed."""
from __future__ import annotations

from pathlib import Path

import pymupdf

# The built-in Helvetica has no Czech glyphs; tickets that need diacritics bring their own font.
SAMPLE_TEXT = "Rychvaldsky zpravodaj, cislo 5"
A4 = (595, 842)


def scan_pdf(path: Path, *, pages: int = 1, text: str = SAMPLE_TEXT, dpi: int = 150, size=A4,
             rotation: int = 0, cropbox: tuple | None = None, mark: tuple | None = None,
             invisible_text: str | None = None) -> Path:
    """A scan-like PDF: each page is one raster image of printed text covering the page, without a text layer.

    size is the page size in points, dpi the scan image's resolution (pixels per inch of the page).
    rotation sets the page's /Rotate; cropbox (x0, y0, x1, y1, top-left origin as PyMuPDF uses it) sets
    its CropBox. mark is a rectangle in the same coordinates printed solid black into the scan.
    invisible_text adds an invisible text layer (render mode 3), as an earlier OCR would.
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
