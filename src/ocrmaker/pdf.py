"""Reading source PDFs: facts a project needs before any step runs, page geometry, text and working rasters.

Coordinates follow the page geometry contract in `ocrmaker.pages` (source page coordinates).
"""
from __future__ import annotations

import math
from pathlib import Path

import pymupdf

from . import pages as geometry

# Rendering resolution for a page without a scan image, and the range a native resolution must fall into
# to be used as it is; anything outside is clamped, so the raster is resampled and not "native".
FALLBACK_DPI = 300.0
MIN_DPI, MAX_DPI = 72.0, 1200.0


def inspect_source(path: Path) -> dict:
    """Page count of a readable PDF; anything the application cannot process is refused in Czech."""
    with open_source(path) as document:
        if document.page_count == 0:
            raise ValueError("PDF neobsahuje žádnou stránku.")
        return {"pages": document.page_count}


def open_source(path: Path) -> pymupdf.Document:
    try:
        document = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise ValueError("Soubor není čitelné PDF.") from exc
    if document.needs_pass:
        document.close()
        raise ValueError("PDF je chráněné heslem, takové soubory aplikace neotevře.")
    return document


def _number(value: float) -> float:
    """Round away floating point noise so stored geometry reads cleanly (1e-4 pt is far below a pixel)."""
    rounded = round(value, 4)
    return int(rounded) if rounded == int(rounded) else rounded


def _numbers(values) -> list:
    return [_number(value) for value in values]


def text_word_count(page: pymupdf.Page) -> int:
    """Words of an existing text layer on the page, visible or not."""
    return len(page.get_text("words"))


def page_geometry(page: pymupdf.Page) -> dict:
    """The page's boxes, rotation and scan image, everything but the raster (see ocrmaker.pages)."""
    mediabox = page.mediabox  # PDF user space as written
    crop = page.cropbox       # PyMuPDF: x as written, y measured down from the MediaBox top
    cropbox = [crop.x0, mediabox.y1 - crop.y1, crop.x1, mediabox.y1 - crop.y0]
    return {
        "index": page.number,
        "width": _number(crop.width),
        "height": _number(crop.height),
        "rotation": page.rotation,
        "mediabox": _numbers(mediabox),
        "cropbox": _numbers(cropbox),
        "pdf_to_source": _numbers([1, 0, 0, -1, -cropbox[0], cropbox[3]]),
        "image": _scan_image(page, [(crop.width, crop.height), (mediabox.width, mediabox.height)]),
    }


def _scan_image(page: pymupdf.Page, page_sizes: list[tuple[float, float]]) -> dict | None:
    """The page's largest image, taken to be the scan, with its resolution along the page axes."""
    best, best_area = None, 0.0
    for info in page.get_image_info(xrefs=True):
        bbox = pymupdf.Rect(info["bbox"])
        if bbox.is_empty or not info["width"] or not info["height"]:
            continue
        if bbox.get_area() > best_area:
            best, best_area = info, bbox.get_area()
    if best is None:
        return None
    a, b, c, d, _, _ = best["transform"]
    # The image's own x axis is (a, b) in page space, its y axis (c, d); either may lie along page x.
    along_x = abs(a) >= abs(b)
    pixels = (best["width"], best["height"]) if along_x else (best["height"], best["width"])
    extents = (math.hypot(a, b), math.hypot(c, d)) if along_x else (math.hypot(c, d), math.hypot(a, b))
    dpi = [_resolution(pixels[axis], extents[axis], [size[axis] for size in page_sizes]) for axis in (0, 1)]
    return {"xref": best["xref"], "width": best["width"], "height": best["height"],
            "dpi": dpi, "bbox": _numbers(best["bbox"]), "components": best["colorspace"], "bpc": best["bpc"]}


def _resolution(pixels: int, extent: float, page_extents: list[float]) -> float:
    """Pixels per inch of an image along one page axis, exact enough to render on its own pixel grid.

    MuPDF reports image placement with float32 noise, which would resample a whole page by a fraction of
    a pixel. An image spanning the page (within a pixel) takes the page's exact extent; otherwise a
    resolution within 0.02 dpi of a whole number, as scanners write, is taken as that number.
    """
    measured = pixels * 72 / extent
    for page_extent in page_extents:
        if abs(extent - page_extent) < 72 / measured:
            return _number(pixels * 72 / page_extent)
    return round(measured) if abs(measured - round(measured)) < 0.02 else _number(measured)


def render_raster(page: pymupdf.Page, page_geometry: dict, target: Path) -> dict:
    """Render the page as displayed into a PNG working raster; returns the raster entry of the geometry.

    The scan image's own resolution is used where possible, so the raster reproduces its pixels exactly.
    """
    image = page_geometry["image"]
    if image:
        zoom = tuple(value / 72 for value in image["dpi"])
        native = all(MIN_DPI <= value * 72 <= MAX_DPI for value in zoom)
        zoom = tuple(min(max(value, MIN_DPI / 72), MAX_DPI / 72) for value in zoom)
    else:
        zoom, native = (FALLBACK_DPI / 72, FALLBACK_DPI / 72), False
    rotation = pymupdf.Matrix(page.rotation_matrix)
    # Zoom is per page axis; on a page displayed at 90 or 270 degrees the display x axis is page y.
    display_zoom = zoom[::-1] if page.rotation % 180 else zoom
    to_raster = geometry.multiply(list(rotation), [display_zoom[0], 0, 0, display_zoom[1], 0, 0])
    width = round(page.rect.width * display_zoom[0])
    height = round(page.rect.height * display_zoom[1])
    # The clip keeps the size at the rounded pixel count; MuPDF would otherwise add a pixel for float noise.
    clip = pymupdf.Rect(0, 0, width / display_zoom[0], height / display_zoom[1])
    gray = image is not None and image["components"] == 1
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(*display_zoom), clip=clip, alpha=False, annots=False,
                             colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB)
    pixmap.save(target, output="png")
    return {"width": pixmap.width, "height": pixmap.height,
            "dpi": _numbers(value * 72 for value in display_zoom), "native": native,
            "to_source": geometry.invert(to_raster), "to_raster": to_raster}
