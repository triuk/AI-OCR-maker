"""Import: reads the source PDF, refuses one with a text layer, records page geometry, renders working rasters.

Its result is the page geometry contract described in `ocrmaker.pages`: `pages.json` plus one PNG working
raster per page, rendered at the scan image's native resolution where possible.
"""
from __future__ import annotations

import time

from .. import pdf
from ..errors import StepError
from ..pages import PAGES_FILE, apply_rect, raster_name, read_pages
from ..step import Step, StepContext, StepResult

# Below this scan resolution recognition gets unreliable; the user is warned, the run continues.
LOW_DPI = 150


def _page_list(numbers: list[int]) -> str:
    shown = ", ".join(str(number) for number in numbers[:10])
    return shown + (" …" if len(numbers) > 10 else "")


class ImportStep(Step):
    id = "import"

    def run(self, ctx: StepContext) -> None:
        try:
            document = pdf.open_source(ctx.source_pdf)
        except ValueError as exc:  # already a Czech message
            raise StepError(str(exc)) from exc
        with document:
            total = document.page_count
            text_pages = {page.number: count for page in document if (count := pdf.text_word_count(page))}
            if text_pages:
                numbers = [index + 1 for index in text_pages]
                ctx.record("import", pages=total, text_layer_pages=numbers,
                           text_layer_words=sum(text_pages.values()), outcome="text_layer")
                raise StepError(
                    "Zdrojové PDF už obsahuje textovou vrstvu "
                    f"({'strana' if len(numbers) == 1 else 'strany'} {_page_list(numbers)}). "
                    "Nové OCR by text zdvojilo, proto se projekt nezpracuje. "
                    "Odstranění existující textové vrstvy zatím aplikace nenabízí.")
            pages, records, low = [], [], []
            for page in document:
                ctx.checkpoint()
                ctx.progress(page.number, total, f"Stránka {page.number + 1} z {total}")
                started = time.monotonic()
                geometry = pdf.page_geometry(page)
                name = raster_name(page.number)
                raster = pdf.render_raster(page, geometry, ctx.output.path(name))
                geometry["raster"] = {"step": self.id, "file": name, **raster}
                pages.append(geometry)
                image = geometry["image"]
                if image and min(image["dpi"]) < LOW_DPI:
                    low.append(page.number + 1)
                records.append({"index": page.number, "width": geometry["width"], "height": geometry["height"],
                                "rotation": geometry["rotation"], "image_dpi": image and image["dpi"],
                                "raster_size": [raster["width"], raster["height"]], "raster_dpi": raster["dpi"],
                                "native": raster["native"], "seconds": round(time.monotonic() - started, 3)})
            ctx.progress(total, total, f"Stránka {total} z {total}")
        ctx.output.write_json(PAGES_FILE, {"pages": pages})
        ctx.record("import", pages=total, outcome="imported", page_details=records)
        resampled = [page["index"] + 1 for page in pages if not page["raster"]["native"]]
        if low:
            where = "na straně" if len(low) == 1 else "na stranách"
            ctx.warn(f"Sken má nízké rozlišení (pod {LOW_DPI} dpi) {where} {_page_list(low)}; "
                     "rozpoznávání textu může být nepřesné.")
        if resampled:
            which = "Strana {} nemá" if len(resampled) == 1 else "Strany {} nemají"
            ctx.warn(which.format(_page_list(resampled)) + " sken v použitelném rozlišení; "
                     "pracovní obraz se vykreslil v náhradním rozlišení.")

    def overlay(self, result: StepResult, page: int) -> dict | None:
        """Page bounds, MediaBox, scan image placement and resolutions, in source page coordinates."""
        pages = read_pages(result)
        if not 0 <= page < len(pages):
            return None
        geometry = pages[page]
        return {
            "page": {key: geometry[key] for key in ("index", "width", "height", "rotation", "mediabox", "cropbox")},
            "bounds": [0, 0, geometry["width"], geometry["height"]],
            "mediabox": apply_rect(geometry["pdf_to_source"], geometry["mediabox"]),
            "image": geometry["image"],
            "raster": {key: geometry["raster"][key] for key in ("width", "height", "dpi", "native")},
        }


STEP = ImportStep()
