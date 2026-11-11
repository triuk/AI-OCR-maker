"""Seam 1 with real Tesseract: Layout (regions) divides a page into AI layout regions in reading order (ADR 0006)."""
from __future__ import annotations

import pymupdf
import pytest

from fakes import ScriptedProvider
from layout_pdfs import CROSSING, LEFT, RIGHT, Print, Sketch, printed_pdf
from ocrmaker import regions
from ocrmaker.language import UNAVAILABLE, LanguageError
from ocrmaker.steps import implementations
from ocrmaker.steps.regions_step import REGIONS_FILE
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow

SIZE = (420, 595)
# A letter whose greeting runs out of a drawing between two paragraphs (the 1994 newsletter's "Blahopřání vám všem")
LETTER = [Print("letter-1", (LEFT[0], 40, RIGHT[1], 120), CROSSING[1].text),
          Sketch("greeting", (LEFT[0], 128, RIGHT[1], 172), "Blahopřejeme vám všem a hodně zdraví."),
          Print("letter-2", (LEFT[0], 182, RIGHT[1], 262), CROSSING[3].text)]
BY_KEY = {item.key: item.rect for item in CROSSING + LETTER}


def grid(*keys, pad=4) -> list[int]:
    """The grid box (0..1000) around the printed items, a little loose as an AI draws it."""
    x0 = min(BY_KEY[key][0] for key in keys) - pad
    y0 = min(BY_KEY[key][1] for key in keys) - pad
    x1 = max(BY_KEY[key][2] for key in keys) + pad
    y1 = max(BY_KEY[key][3] for key in keys) + pad
    return [round(x0 * 1000 / SIZE[0]), round(y0 * 1000 / SIZE[1]), round(x1 * 1000 / SIZE[0]),
            round(y1 * 1000 / SIZE[1])]


def part(title, *boxes, kind="article") -> dict:
    return {"kind": kind, "title": title, "regions": [{"box": box, "orientation": "0"} for box in boxes]}


READING = {"parts": [part("Oprava školy", grid("A-head", "A1"), grid("A2")),
                     part("Nové knihy", grid("B-head", "B1"), grid("B2"))]}


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    return printed_pdf(tmp_path_factory.mktemp("regions") / "zpravodaj-2000-05.pdf", [CROSSING])


def layout(tmp_path, pdf, *answers, method="regions") -> tuple[dict, ScriptedProvider, dict]:
    """Import, Image preparation and Layout (regions) of the crossing page with scripted AI answers."""
    provider = ScriptedProvider()
    if answers:
        provider.on(regions.PURPOSE, *answers)
    shipped = implementations()
    workflow = Workflow(ProjectStore(tmp_path / "library"), language=provider,
                        steps={step: shipped[step] for step in ("import", "prepare", "regions")})
    project = workflow.create(pdf)
    workflow.update_settings(project["id"], {"layout_method": method})
    project = workflow.run(project["id"])
    return workflow.result(project["id"], "regions").read_json(REGIONS_FILE), provider, project


def labels(entry) -> list[str]:
    return [f"{region['article']}{'abc'[region['column'] - 1]}" for region in entry["regions"]]


def holds(region, key) -> bool:
    """The region holds the item's first printed line (the text fills only the top of the item's box)."""
    x0, y0, x1, y1 = BY_KEY[key]
    return pymupdf.Rect(region["bbox"]).contains(pymupdf.Point(x0 + 6, y0 + 8))


def test_the_ai_regions_become_one_sequence_in_reading_order_snapped_to_the_print(tmp_path, pdf):
    result, provider, project = layout(tmp_path, pdf, READING)

    [entry] = result["pages"]
    assert result["method"] == "regions" and entry["source"] == "ai"
    assert labels(entry) == ["1a", "1b", "2a", "2b"]
    assert [region["continues"] for region in entry["regions"]] == [False, True, False, True]
    assert [region["kind"] for region in entry["regions"]] == ["article"] * 4
    for region, keys in zip(entry["regions"], (("A-head", "A1"), ("A2",), ("B-head", "B1"), ("B2",))):
        assert all(holds(region, key) for key in keys)
        # snapped to the ink: inside the loose box the AI drew
        x0, y0, x1, y1 = region["bbox"]
        assert x0 > min(BY_KEY[key][0] for key in keys) - 4 and x1 < max(BY_KEY[key][2] for key in keys) + 4
    assert entry["spots"] == [] and entry["check"]["score"] == 0
    # a clean answer needs no corrective request
    assert entry["requests"] == 1 and len(provider.requests_for(regions.PURPOSE)) == 1
    [request] = provider.requests_for(regions.PURPOSE)
    assert len(request.images) == 1
    assert states(project)["regions"] == "done"


def test_an_answer_in_image_pixels_is_read_as_grid_units(tmp_path, pdf):
    def in_pixels(request):
        png = request.images[0]  # the PNG header holds the size in pixels
        width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
        return {"parts": [{**item, "regions": [
            {"box": [round(v * size / 1000) for v, size in zip(region["box"], (width, height) * 2)],
             "orientation": "0"} for region in item["regions"]]} for item in READING["parts"]]}

    result, _, _ = layout(tmp_path, pdf, in_pixels)

    assert labels(result["pages"][0]) == ["1a", "1b", "2a", "2b"]
    assert result["pages"][0]["check"]["score"] == 0


def test_a_missed_column_is_reported_back_once_and_the_correction_kept(tmp_path, pdf):
    missing = {"parts": [part("Oprava školy", grid("A-head", "A1"), grid("A2")),
                         part("Nové knihy", grid("B-head", "B1"))]}
    result, provider, _ = layout(tmp_path, pdf, missing, READING)

    entry = result["pages"][0]
    first, fix = provider.requests_for(regions.PURPOSE)
    assert fix.data["task"] == "Correct your previous answer."
    assert fix.data["problems"] and len(fix.data["previous_answer"]["parts"]) == 2
    assert entry["chosen"] == "fixed" and entry["requests"] == 2
    assert labels(entry) == ["1a", "1b", "2a", "2b"]


def test_a_worse_correction_is_refused(tmp_path, pdf):
    missing = {"parts": [part("Oprava školy", grid("A-head", "A1"), grid("A2")),
                         part("Nové knihy", grid("B-head", "B1"))]}
    worse = {"parts": [part("Oprava školy", grid("A-head", "A1"))]}
    result, _, _ = layout(tmp_path, pdf, missing, worse)

    entry = result["pages"][0]
    assert entry["chosen"] == "first" and labels(entry) == ["1a", "1b", "2a"]
    # the uncovered column stays visible as words in no region
    assert any("Čtenáři mohou" in problem and "in no region" in problem for problem in entry["check"]["problems"])


def test_a_line_lost_between_regions_is_reported_back_though_its_words_look_like_decoration(tmp_path):
    # Recognition reads the greeting with little confidence over the drawing's ink, so its words count as
    # decoration and not as words in no region; as a line of words it is still lost text.
    pdf = printed_pdf(tmp_path / "dopis.pdf", [LETTER])
    lost = {"parts": [part("Dopis", grid("letter-1"), grid("letter-2"))]}
    whole = {"parts": [part("Dopis", grid("letter-1", "greeting", "letter-2"))]}
    result, provider, _ = layout(tmp_path, pdf, lost, whole)

    entry = result["pages"][0]
    first, fix = provider.requests_for(regions.PURPOSE)
    [problem] = [problem for problem in fix.data["problems"] if problem.startswith("Text line")]
    assert "všem" in problem and "in no region" in problem
    assert entry["chosen"] == "fixed" and entry["check"]["lost"] == 0


def test_an_invalid_answer_leaves_one_region_over_the_whole_page_and_warns(tmp_path, pdf):
    result, _, project = layout(tmp_path, pdf, {"parts": [{"kind": "poem", "regions": []}]})

    entry = result["pages"][0]
    assert entry["source"] == "failed" and "kind" in entry["reason"]
    assert labels(entry) == ["1a"]
    assert all(holds(entry["regions"][0], key) for key in BY_KEY)
    assert states(project)["regions"] == "warning"


def test_without_ai_every_page_is_one_region(tmp_path, pdf):
    result, _, project = layout(tmp_path, pdf, LanguageError("Není nainstalováno.", code=UNAVAILABLE))

    entry = result["pages"][0]
    assert entry["source"] == "failed" and entry["reason"].startswith(UNAVAILABLE)
    assert labels(entry) == ["1a"]
    assert states(project)["regions"] == "warning"


def test_the_blocks_method_asks_nothing(tmp_path, pdf):
    result, provider, project = layout(tmp_path, pdf, method="blocks")

    assert result == {"method": "blocks", "pages": []}
    assert provider.requests == [] and states(project)["regions"] == "done"


def states(project) -> dict:
    return {step["id"]: step["state"] for step in project["steps"]}
