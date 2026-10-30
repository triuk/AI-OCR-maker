"""Image preparation: a placeholder that leaves every page unchanged (ADR 0003).

Its result already has the full contract (see `ocrmaker.pages`): `pages.json` with each page's working
raster, the raster's mapping back to source page coordinates, and a page transform. The placeholder passes
Import's rasters on by reference and sets the identity transform; real preparation (deskew, dewarp) only
overrides `prepare_page`.
"""
from __future__ import annotations

from ..pages import IDENTITY_TRANSFORM, PAGES_FILE, read_pages
from ..step import Step, StepContext, StepResult


class PrepareStep(Step):
    id = "prepare"

    def prepare_page(self, page: dict, raster: StepResult, ctx: StepContext) -> dict:
        """The prepared page: optionally a new `raster` entry (its file in ctx.output) and a `transform`."""
        return {**page, "transform": dict(IDENTITY_TRANSFORM)}

    def run(self, ctx: StepContext) -> None:
        pages = read_pages(ctx.result("import"))
        prepared = []
        for number, page in enumerate(pages):
            ctx.checkpoint()
            ctx.progress(number, len(pages), f"Stránka {number + 1} z {len(pages)}")
            prepared.append(self.prepare_page(page, ctx.result(page["raster"]["step"]), ctx))
        ctx.progress(len(pages), len(pages), f"Stránka {len(pages)} z {len(pages)}")
        ctx.output.write_json(PAGES_FILE, {"pages": prepared})
        ctx.record("prepare", pages=len(pages), transforms=[page["transform"] for page in prepared])


STEP = PrepareStep()
