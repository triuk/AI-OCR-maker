"""An existing text layer in a source PDF: whether it is invisible (an earlier OCR) or visible text, and removing
an invisible one into a clean working copy.

A text layer is visible when some text is painted (filled or stroked) and no later scan image covers it; such a
PDF is born-digital, not a scan, and is not processed. Text in render mode 3 or hidden beneath the scan image is
invisible, as earlier OCR tools write it; removing it lets the document be OCRed anew without duplicated text.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf

PAINTED_TEXT = ("fill-text", "stroke-text")
# Larger than any page, so the removal reaches text placed anywhere, also outside the visible page area.
EVERYWHERE = pymupdf.Rect(-1e5, -1e5, 1e5, 1e5)
# The action Import's stop offers for an invisible text layer (the step state's "action").
REMOVE_ACTION = "remove_text_layer"


def survey(document: pymupdf.Document) -> dict | None:
    """The document's text layer, or None without one.

    Returns {"pages": [...], "visible_pages": [...], "words": n, "visible": bool}; page numbers are 1-based.
    """
    pages, visible, words = [], [], 0
    for page in document:
        count = len(page.get_text("words"))
        if not count:
            continue
        pages.append(page.number + 1)
        words += count
        if _shows_text(page):
            visible.append(page.number + 1)
    if not pages:
        return None
    return {"pages": pages, "visible_pages": visible, "words": words, "visible": bool(visible)}


def survey_file(path: Path) -> dict | None:
    with pymupdf.open(path, filetype="pdf") as document:
        return survey(document)


def _shows_text(page: pymupdf.Page) -> bool:
    """Whether some painted text on the page is not covered by an image drawn after it."""
    later_images = []
    for kind, bbox in reversed(page.get_bboxlog()):
        if kind == "fill-image":
            later_images.append(pymupdf.Rect(bbox) + (-1, -1, 1, 1))
        elif kind in PAINTED_TEXT:
            text = pymupdf.Rect(bbox)
            if not text.is_empty and not any(image.contains(text) for image in later_images):
                return True
    return False


def page_list(numbers: list[int]) -> str:
    """Czech "strana 1" / "strany 1, 2, 3 …" for a list of 1-based page numbers."""
    shown = ", ".join(str(number) for number in numbers[:10])
    return ("strana " if len(numbers) == 1 else "strany ") + shown + (" …" if len(numbers) > 10 else "")


def invisible_message(layer: dict) -> str:
    return (f"Zdrojové PDF už obsahuje neviditelnou textovou vrstvu ({page_list(layer['pages'])}), nejspíš ze "
            "staršího OCR. Nové OCR by text zdvojilo. Vrstvu lze odstranit a pokračovat; zdrojový soubor "
            "zůstane beze změny.")


def visible_message(layer: dict) -> str:
    """Why Import stops for good on visible text."""
    return (f"Zdrojové PDF obsahuje viditelný text ({page_list(layer['visible_pages'])}). Nejde o sken, ale "
            "o digitálně vytvořený dokument, proto se projekt nezpracuje.")


def refusal_message(layer: dict | None) -> str:
    """Why a removal of the text layer is refused (visible text, or no text layer at all)."""
    if layer is None:
        return "Zdrojové PDF nemá textovou vrstvu, kterou by bylo třeba odstranit."
    return (f"Textovou vrstvu nelze odstranit: zdrojové PDF obsahuje viditelný text "
            f"({page_list(layer['visible_pages'])}). Nejde o sken, ale o digitálně vytvořený dokument.")


def _image_streams(document: pymupdf.Document) -> dict[int, str]:
    """A digest of every image the pages use, its dictionary and raw (still encoded) stream bytes, by xref."""
    digests = {}
    for page in document:
        for image in page.get_images(full=True):
            xref = image[0]
            if xref not in digests:
                digest = hashlib.sha256(document.xref_object(xref, compressed=True).encode())
                digest.update(document.xref_stream_raw(xref) or b"")
                digests[xref] = digest.hexdigest()
    return digests


def remove(source: Path, target: Path) -> dict:
    """Write a copy of the source PDF without text-showing operations; image objects stay byte for byte.

    Page content (also inside form XObjects) is filtered by MuPDF's redaction with images and vector graphics
    left alone; object numbers are kept. The copy is checked: no text may remain and every image object must
    be identical, otherwise ValueError (Czech) is raised and the copy must not be used.
    """
    with pymupdf.open(source, filetype="pdf") as document:
        images = _image_streams(document)
        for page in document:
            page.add_redact_annot(EVERYWHERE, fill=None, cross_out=False)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        # garbage=1 drops the replaced content streams without renumbering; no deflate, so image streams are
        # copied as they are.
        document.save(target, garbage=1)
    with pymupdf.open(target, filetype="pdf") as clean:
        if survey(clean) is not None:
            raise ValueError("Textovou vrstvu se nepodařilo úplně odstranit.")
        if _image_streams(clean) != images:
            raise ValueError("Odstranění textové vrstvy by změnilo obrázky skenu, proto se neprovedlo.")
        return {"pages": clean.page_count, "images": len(images)}
