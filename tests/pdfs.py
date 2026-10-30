"""Generated fixture PDFs; no real scans are committed."""
from __future__ import annotations

from pathlib import Path

import pymupdf

# The built-in Helvetica has no Czech glyphs; tickets that need diacritics bring their own font.
SAMPLE_TEXT = "Rychvaldsky zpravodaj, cislo 5"


def scan_pdf(path: Path, *, pages: int = 1, text: str = SAMPLE_TEXT, dpi: int = 150) -> Path:
    """A scan-like PDF: each page is one raster image of printed text and has no text layer."""
    source = pymupdf.open()
    output = pymupdf.open()
    for number in range(pages):
        page = source.new_page(width=595, height=842)
        page.insert_text((72, 100), f"{text} ({number + 1})", fontsize=14, fontname="helv")
        image = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        scanned = output.new_page(width=595, height=842)
        scanned.insert_image(scanned.rect, stream=image.tobytes("png"))
    output.save(path)
    return path


def text_pdf(path: Path, text: str = SAMPLE_TEXT) -> Path:
    """A born-digital PDF with visible text."""
    document = pymupdf.open()
    document.new_page().insert_text((72, 100), text, fontsize=12)
    document.save(path)
    return path
