"""Layout review: the person's half of a layout round (M of ADR 0006), kept apart from the step results.

Between layout rounds the person edits the layout regions of the Layout regions result (`ocrmaker.regions`) page by
page in the layout editor. Every edit is saved at once and runs no step; the project keeps the edits in
`layout.json` (ProjectStore.layout_edits):

    {"pages": {"<page index>": {
        "round": 2,              # the round of the page's layout the edit was made on
        "parts": [part] | None,  # the page's layout as the person left it; None: the AI's layout, untouched
        "note": ""}}}            # the layout note, sent with the page in the next round

    part = {"kind": "article" | "header" | "footer" | "ad", "title": str,
            "continues": False,  # the part continues the previous part's article (one article in the result)
            "regions": [{"box": [x0, y0, x1, y1],   # grid units 0..1000 of the page, as drawn
                         "orientation": "0" | "90" | "180" | "270" | "stacked",
                         "locked": True}]}          # the AI must keep the region as it is

An edit counts only while the page's layout in the result is still of the edit's round: a round that redoes the
page replaces the edit and its note. A page goes to the next round when its edit has an unlocked region or a note;
approval takes every edit into the result as drawn (the Layout regions step), so the steps after Layout read only
the result. Whether the layout waits for the person or is approved is the project's "layout" (WAITING or APPROVED;
None without layout regions), kept by the Workflow; the Layout regions step reads it through ctx.layout_review().
"""
from __future__ import annotations

import math

from .regions import KINDS, ORIENTATIONS, SCALE, grid_box, layout_parts

WAITING, APPROVED = "waiting", "approved"
# A region narrower or lower than this (grid units) is a slip of the mouse, not a region.
MIN_SIZE = 1
MAX_TITLE = 300
MAX_NOTE = 4000


def empty() -> dict:
    return {"pages": {}}


def page_round(entry: dict) -> int:
    """The layout round that last laid out the page (results from before rounds are of round 1)."""
    return entry.get("round", 1)


def current(edits: dict, entry: dict) -> dict | None:
    """The page's edit while it still counts (made on the page's present layout), else None."""
    edit = edits["pages"].get(str(entry["index"]))
    return edit if edit is not None and edit["round"] == page_round(entry) else None


def proposal(entry: dict, page: dict) -> list[dict]:
    """The AI's layout of the page in its round, as parts with every region locked."""
    return layout_parts(entry.get("proposal", entry["regions"]), page["width"], page["height"], page.get("raster"))


def laid_out(entry: dict, page: dict) -> list[dict]:
    """The page's layout in the result, as parts with every region locked."""
    return layout_parts(entry["regions"], page["width"], page["height"], page.get("raster"))


def effective(entry: dict, edit: dict | None, page: dict) -> list[dict]:
    """The page's layout as the person sees it: their edit, else the result's."""
    return edit["parts"] if edit and edit["parts"] is not None else laid_out(entry, page)


def wanted(edit: dict | None) -> bool:
    """Whether the page goes to the next layout round: an unlocked region or a note."""
    if not edit:
        return False
    return bool(edit["note"].strip()) or any(not region["locked"] for part in edit["parts"] or ()
                                             for region in part["regions"])


def to_process(edits: dict, entries: list[dict]) -> list[int]:
    """The indices of the pages the next layout round sends to the AI again."""
    return [entry["index"] for entry in entries if wanted(current(edits, entry))]


def shape(parts: list[dict], *, locks: bool = True) -> list:
    """What makes two layouts of a page the same (boxes to a hundredth of a grid unit)."""
    return [(part["kind"], part["title"], part["continues"],
             [([round(v, 2) for v in region["box"]], region["orientation"], region["locked"] if locks else None)
              for region in part["regions"]]) for part in parts]


def drawn(entry: dict, edit: dict | None, page: dict) -> list[dict] | None:
    """The person's layout of the page when it differs from the result's (locks aside), else None."""
    if not edit or edit["parts"] is None:
        return None
    if shape(edit["parts"], locks=False) == shape(laid_out(entry, page), locks=False):
        return None
    return edit["parts"]


def _page_edit(edits: dict, entry: dict) -> dict:
    """The page's edit to change: the current one, else a new one in place of an edit of an earlier round."""
    edit = current(edits, entry) or {"round": page_round(entry), "parts": None, "note": ""}
    edits["pages"][str(entry["index"])] = edit
    return edit


def put_parts(edits: dict, entry: dict, parts: list[dict]) -> dict:
    """Save the page's layout as the person left it; an edit of an earlier round is replaced, note and all."""
    edit = _page_edit(edits, entry)
    edit["parts"] = parts
    return edit


def put_note(edits: dict, entry: dict, text: str) -> dict:
    """Save the page's layout note."""
    edit = _page_edit(edits, entry)
    edit["note"] = text
    return edit


def validated_note(text) -> str:
    if not isinstance(text, str):
        raise ValueError("Poznámka k rozvržení musí být text.")
    if len(text) > MAX_NOTE:
        raise ValueError(f"Poznámka k rozvržení může mít nejvýše {MAX_NOTE} znaků.")
    return text


def validated(parts) -> list[dict]:
    """The person's layout of a page in the stored form; ValueError (Czech) when it is not one."""
    def fail(message):
        raise ValueError(f"Neplatné rozvržení strany: {message}.")
    if not isinstance(parts, list):
        fail("chybí seznam částí")
    result = []
    for number, part in enumerate(parts, start=1):
        if not isinstance(part, dict) or part.get("kind") not in KINDS:
            fail(f"část {number} nemá známý druh")
        title = part.get("title") or ""
        if not isinstance(title, str) or len(title) > MAX_TITLE:
            fail(f"část {number} má neplatný název")
        regions = part.get("regions")
        if not isinstance(regions, list) or not regions:
            fail(f"část {number} nemá žádnou oblast")
        checked = []
        for region in regions:
            box = region.get("box") if isinstance(region, dict) else None
            if not (isinstance(box, list) and len(box) == 4
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box)
                    and all(0 <= v <= SCALE for v in box)
                    and box[2] - box[0] >= MIN_SIZE and box[3] - box[1] >= MIN_SIZE):
                fail(f"oblast v části {number} nemá platný obdélník")
            if str(region.get("orientation", "0")) not in ORIENTATIONS:
                fail(f"oblast v části {number} má neznámé otočení")
            checked.append({"box": [float(v) for v in box], "orientation": str(region.get("orientation", "0")),
                            "locked": region.get("locked", True) is not False})
        result.append({"kind": part["kind"], "title": title.strip(),
                       "continues": bool(part.get("continues")) and bool(result), "regions": checked})
    return result


def page_view(entry: dict, edit: dict | None, page: dict) -> dict:
    """One page in the layout editor (see Workflow.layout)."""
    parts, suggested = effective(entry, edit, page), proposal(entry, page)
    return {"index": entry["index"], "width": page["width"], "height": page["height"],
            "raster_width": page.get("raster", {}).get("width", page["width"]),
            "raster_height": page.get("raster", {}).get("height", page["height"]),
            "round": page_round(entry), "source": entry["source"], "reason": entry.get("reason"),
            "parts": parts, "proposal": suggested, "edited": shape(parts) != shape(suggested),
            "note": edit["note"] if edit else "", "sent_note": entry.get("note"), "process": wanted(edit),
            "spots": [grid_box(spot, page["width"], page["height"], page.get("raster"))
                      for spot in entry["spots"]]}
