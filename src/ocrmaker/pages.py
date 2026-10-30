"""Page geometry: the contract every step relies on for page sizes, working rasters and coordinates.

Source page coordinates
    All geometry any step stores is in source page coordinates: PDF points in the page's unrotated
    coordinate space, origin at the top-left corner of the CropBox, x to the right, y downwards. This is
    the space PyMuPDF uses for an unrotated page (`page.get_image_info()` bboxes, `page.insert_text()`
    positions), so word boxes in it land on the original scan image (ADR 0003). The page's /Rotate only
    changes how the page is displayed; it never changes stored coordinates.

The Import result (`steps/import/`) holds `pages.json` and one working raster per page:

    {"pages": [{
        "index": 0,                          # 0-based page index in the source PDF
        "width": 595.0, "height": 842.0,     # extent of source page coordinates (unrotated CropBox), points
        "rotation": 0,                       # /Rotate: 0, 90, 180 or 270 (clockwise when displayed)
        "mediabox": [x0, y0, x1, y1],        # as written in the PDF: PDF user space, y upwards
        "cropbox": [x0, y0, x1, y1],         # likewise; equals mediabox when the PDF has no CropBox
        "pdf_to_source": [a, b, c, d, e, f], # PDF user space -> source page coordinates
        "image": {                           # the page's scan image (the largest one), or null
            "xref": 5, "width": 2480, "height": 3508,   # pixels
            "dpi": [300.0, 300.0],           # pixels per inch along the page's x and y axes
            "bbox": [x0, y0, x1, y1],        # where it lies, source page coordinates
            "components": 1, "bpc": 8},      # colour components and bits per component
        "raster": {                          # the working raster, see below
            "step": "import", "file": "rasters/page-0001.png",
            "width": 2480, "height": 3508,   # pixels
            "dpi": [300.0, 300.0],           # pixels per inch along the raster's x and y axes
            "native": true,                  # rendered at the scan image's own resolution (no resampling)
            "to_source": [a, b, c, d, e, f], # raster pixel -> source page coordinates
            "to_raster": [a, b, c, d, e, f]} # source page coordinates -> raster pixel
    }]}

Working rasters
    A working raster is a PNG of the page as displayed (rotation and CropBox applied, so text is upright)
    that recognition runs on. Its pixel coordinates have the origin at the top-left pixel corner, so
    pixel (i, j) covers [i, i+1) x [j, j+1). `to_source` maps raster pixel coordinates back to source page
    coordinates and `to_raster` is its inverse. Rasters are identified by the step whose result holds the
    file plus the file name, so a later step (Image preparation) can pass on Import's rasters unchanged
    or replace them with prepared ones that carry their own mapping, without copying files.

Matrices are 6-tuples [a, b, c, d, e, f] in the PDF/PyMuPDF convention: a point (x, y) maps to
(a*x + c*y + e, b*x + d*y + f). Only affine mappings fit here; non-linear preparation (dewarping) must
store its own mapping beside the raster and is out of scope until it exists (ADR 0003).
"""
from __future__ import annotations

from collections.abc import Sequence

# The step whose result is the authority on page geometry and the source-view rasters.
PAGES_STEP = "import"
PAGES_FILE = "pages.json"

Matrix = Sequence[float]


def raster_name(index: int) -> str:
    return f"rasters/page-{index + 1:04d}.png"


def apply(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def apply_rect(matrix: Matrix, rect: Sequence[float]) -> list[float]:
    """The axis-aligned rectangle covering a transformed rectangle (exact for multiples of 90 degrees)."""
    x0, y0, x1, y1 = rect
    corners = [apply(matrix, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    return [min(xs), min(ys), max(xs), max(ys)]


def multiply(first: Matrix, then: Matrix) -> list[float]:
    """The matrix applying `first`, then `then`."""
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = then
    return [a1 * a2 + b1 * c2, a1 * b2 + b1 * d2, c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2]


def invert(matrix: Matrix) -> list[float]:
    a, b, c, d, e, f = matrix
    det = a * d - b * c
    if not det:
        raise ValueError("Singular page matrix")
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return [ia, ib, ic, id_, -(e * ia + f * ic), -(e * ib + f * id_)]


def read_pages(result) -> list[dict]:
    """The page geometry list stored in a step result (Import's, or a later step passing it on)."""
    return result.read_json(PAGES_FILE)["pages"]
