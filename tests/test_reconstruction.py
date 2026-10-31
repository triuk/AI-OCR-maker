"""Seam 1 and 2: AI reconstruction of problem spots, only of those (ADR 0001), with a scripted AI."""
from __future__ import annotations

import numpy
import pymupdf
import pytest

from fakes import answer_spots, run_and_export
from ocrmaker.step import Step
from ocrmaker.steps import implementations
from ocrmaker.store import ProjectStore
from ocrmaker.words import page_file
from ocrmaker.workflow import Workflow
from pdfs import CZECH_COLUMNS, printed_text_pdf, printed_words

MARKER = "[NEČITELNÉ]"
TEXT = CZECH_COLUMNS[0] + " " + CZECH_COLUMNS[1]
PRINTED = printed_words(TEXT)  # 85 words on 8 lines, one paragraph


def word_index(text: str) -> int:
    [index] = [index for index, (printed, _) in enumerate(PRINTED) if printed == text]
    return index


class PrintedOcr(Step):
    """Local OCR that reads the printed ground truth exactly, apart from planted damage.

    readings: printed word index -> (text, confidence), a damaged reading in the word's own box ("" makes an
    empty word box). split: printed word index -> the share of its width where Tesseract would have cut it
    in two: two low-confidence fragments that leave its middle and its ends uncovered. joined: printed word indices read together
    with the next word as one low-confidence box. printed: the ground truth of another printed text
    (`printed_words(text)`), PRINTED by default.
    """

    id = "ocr"

    def __init__(self, readings=None, split=None, joined=(), printed=None):
        self.printed = printed or PRINTED
        self.readings = readings or {}
        self.split = split or {}
        self.joined = set(joined)

    def run(self, ctx):
        lines: dict[float, list] = {}
        for index, (text, box) in enumerate(self.printed):
            lines.setdefault(round(box[1], 1), []).append((index, text, list(box)))
        stored, count = [], 0

        def word(text, box, confidence):
            nonlocal count
            count += 1
            return {"id": f"p1-w{count}", "text": text, "bbox": [round(v, 2) for v in box],
                    "confidence": confidence, "chars": []}

        for row, items in enumerate(lines.values()):
            placed, skip = [], False
            for position, (index, text, box) in enumerate(items):
                if skip:
                    skip = False
                    continue
                if index in self.split:
                    share = self.split[index]
                    cut = round(len(text) * share)
                    width = box[2] - box[0]
                    # Blur hides the word's middle and its ends: the fragments cover neither.
                    placed.append(word(text[:cut], [box[0] + width * 0.08, box[1],
                                                    box[0] + width * (share - 0.08), box[3]], 20.0))
                    placed.append(word(text[cut:], [box[0] + width * (share + 0.08), box[1],
                                                    box[2] - width * 0.08, box[3]], 20.0))
                elif index in self.joined:
                    _, following, after = items[position + 1]
                    placed.append(word(text + following, [box[0], box[1], after[2], box[3]], 20.0))
                    skip = True
                elif index in self.readings:
                    reading, confidence = self.readings[index]
                    placed.append(word(reading, box, confidence))
                else:
                    placed.append(word(text, box, 96.0))
            stored.append({"id": f"p1-l{row + 1}", "bbox": [placed[0]["bbox"][0], placed[0]["bbox"][1],
                                                            placed[-1]["bbox"][2], placed[-1]["bbox"][3]],
                           "baseline": None, "block": 0, "paragraph": 0, "words": placed})
        ctx.output.write_json(page_file(0), {"index": 0, "lines": stored})
        ctx.output.write_json("ocr.json", {"engine": {"name": "printed"}, "pages": []})


def run(tmp_path, language_provider, ocr: PrintedOcr, **settings):
    workflow = Workflow(ProjectStore(tmp_path / "library"), steps={**implementations(), "ocr": ocr},
                        language=language_provider)
    project = workflow.create(printed_text_pdf(tmp_path / "zpravodaj.pdf", TEXT), settings=settings or None)
    return workflow, run_and_export(workflow, project["id"])


def ocr_words(workflow, project) -> dict[str, dict]:
    recognised = workflow.result(project["id"], "ocr").read_json(page_file(0))
    return {word["id"]: word for line in recognised["lines"] for word in line["words"]}


def ocr_id(workflow, project, text: str) -> str:
    [word_id] = [word["id"] for word in ocr_words(workflow, project).values() if word["text"] == text]
    return word_id


def exported(workflow, project) -> list[tuple[str, pymupdf.Rect]]:
    [pdf] = [output["name"] for output in workflow.outputs(project["id"]) if output["name"].endswith(".pdf")]
    with pymupdf.open(workflow.output(project["id"], pdf)) as document:
        return [(word[4], pymupdf.Rect(word[:4])) for word in document[0].get_text("words", sort=False)]


def states(project) -> dict:
    return {step["id"]: step["state"] for step in project["steps"]}


DAMAGED = {word_index("opravu"): ("opr", 20.0), word_index("čtvrtek"): ("čtv", 20.0),
           word_index("kronikářem"): ("", 0.0)}


def test_confident_reconstructions_replace_the_spot_and_less_confident_ones_of_unreadable_words_become_the_marker(
        tmp_path, language_provider):
    language_provider.on("reconstruction", lambda request: answer_spots({
        spot_id(request, "opr"): {"text": "opravu", "confidence": 95},
        spot_id(request, "čtv"): {"text": "čtvrtek", "confidence": 40},
        spot_id(request, ""): {"text": "kronikářem", "confidence": 90},
    })(request))
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    assert states(project)["reconstruct"] == "done"
    words = [text for text, _ in exported(workflow, project)]
    assert words == [text if text != "čtvrtek" else MARKER for text, _ in PRINTED]
    overlay = workflow.overlay(project["id"], "reconstruct", 0)
    marker = next(word for word in overlay["words"] if word["text"] == MARKER)
    assert marker["hint"] == "čtv" and marker["candidate"] == "čtvrtek" and marker["status"] == "unresolved"


def spot_id(request, reading: str) -> str:
    [spot] = [spot for spot in request.data["spots"] if spot["reading"] == reading]
    return spot["id"]


def problem_spots(workflow, project) -> dict[str, dict]:
    return {spot["id"]: spot for spot in workflow.overlay(project["id"], "problems", 0)["spots"]}


def test_only_problem_spots_are_sent_as_targets_and_clearly_recognised_words_never_change(
        tmp_path, language_provider):
    # The AI tries to "improve" every word it is shown; only the marked ones may change.
    def eager(request):
        return {"readings": [{"ids": [spot["id"]], "words": ["PŘEPSÁNO"], "confidence": 99, "error": "recognition",
                              "reasoning": "Rewritten.", "intended": None} for spot in request.data["spots"]]}

    language_provider.on("reconstruction", eager)
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    [request] = language_provider.requests_for("reconstruction")
    spots = problem_spots(workflow, project)
    assert {spot["id"] for spot in request.data["spots"]} == set(spots)
    marks = {mark.split(":")[0] for paragraph in request.data["paragraphs"]
             for mark in paragraph["text"].split("[[")[1:]}
    assert marks == set(spots)
    recognised = ocr_words(workflow, project)
    written = exported(workflow, project)
    assert len(written) == len(PRINTED)
    for (text, _), (printed, box) in zip(written, PRINTED):
        word = next(word for word in recognised.values() if word["bbox"][0] == round(box[0], 2)
                    and word["bbox"][1] == round(box[1], 2))
        assert text == ("PŘEPSÁNO" if word["id"] in spots else printed)


def test_an_answer_item_touching_an_unmarked_word_is_rejected_and_the_page_keeps_its_valid_items(
        tmp_path, language_provider):
    def wandering(request):
        answer = answer_spots({spot_id(request, "opr"): {"text": "opravu"},
                               spot_id(request, "čtv"): {"text": "čtvrtek", "confidence": 40}})(request)
        answer["readings"].append({"ids": ["p1-w1"], "words": ["Obecný"], "confidence": 99, "error": "recognition",
                                   "reasoning": "Looks better.", "intended": None})
        return answer

    language_provider.on("reconstruction", wandering)
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    assert "p1-w1" not in problem_spots(workflow, project)
    reconstruct = next(step for step in project["steps"] if step["id"] == "reconstruct")
    assert reconstruct["state"] == "warning" and "odmítnut" in reconstruct["message"]
    words = [text for text, _ in exported(workflow, project)]
    assert words[0] == "Obecní"
    assert words[word_index("opravu")] == "opravu"  # the page's valid item is kept
    assert words[word_index("čtvrtek")] == words[word_index("kronikářem")] == MARKER
    entry = workflow.result(project["id"], "reconstruct").read_json(page_file(0))
    assert entry["rejected"] is None
    [ignored] = entry["ignored"]
    assert ignored["item"]["ids"] == ["p1-w1"] and "not marked" in ignored["problems"][0]
    assert "Rejected readings (their spots stay unresolved) on pages 1" in workflow.report_summary(project["id"])


def test_answer_items_repeating_an_id_are_both_rejected_and_their_spot_stays_unresolved(tmp_path, language_provider):
    def repeating(request):
        answer = answer_spots({spot_id(request, "opr"): {"text": "opravu"},
                               spot_id(request, "čtv"): {"text": "čtvrtek"}})(request)
        answer["readings"].append({**answer["readings"][1], "words": ["čtvrť"]})
        return answer

    language_provider.on("reconstruction", repeating)
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    words = [text for text, _ in exported(workflow, project)]
    assert words[word_index("opravu")] == "opravu"
    assert words[word_index("čtvrtek")] == MARKER
    reasons = {word["hint"]: word["reason"] for word in workflow.overlay(project["id"], "reconstruct", 0)["words"]}
    assert reasons["čtv"] == "rejected" and reasons["opr"] is None
    entry = workflow.result(project["id"], "reconstruct").read_json(page_file(0))
    assert len(entry["ignored"]) == 2


def test_an_answer_without_a_list_of_readings_is_rejected_as_a_whole(tmp_path, language_provider):
    language_provider.on("reconstruction", {"answer": "opravu"})
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    assert [text for text, _ in exported(workflow, project)][word_index("opravu")] == MARKER
    overlay = workflow.overlay(project["id"], "reconstruct", 0)
    assert overlay["rejected"] and {word["reason"] for word in overlay["words"]} == {"rejected"}


LINE_PIXELS = (PRINTED[0][1][3] - PRINTED[0][1][1]) * 300 / 72  # one printed line in the 300 dpi scan


def crops(language_provider) -> list[tuple[dict, pymupdf.Pixmap]]:
    [request] = language_provider.requests_for("reconstruction")
    return [(crop, pymupdf.Pixmap(request.images[crop["image"] - 1]))
            for paragraph in request.data["paragraphs"] for crop in paragraph["images"]]


def test_an_isolated_spot_is_shown_with_its_line_and_one_line_above_and_below(tmp_path, language_provider):
    run(tmp_path, language_provider, PrintedOcr(readings={word_index("příspěvky"): ("přísp", 20.0)}))

    [(crop, image)] = crops(language_provider)
    assert crop["covers"] == "lines" and crop["lines"] == [3, 5]
    assert 2.5 * LINE_PIXELS < image.height < 4.5 * LINE_PIXELS


def test_the_crop_grows_to_the_whole_paragraph_when_the_spots_are_dense(tmp_path, language_provider):
    dense = {word_index(text): (text[:3], 20.0) for text in ("schválilo", "občanům,", "nabízí", "tradiční")}
    run(tmp_path, language_provider, PrintedOcr(readings=dense))

    [(crop, image)] = crops(language_provider)
    assert crop["covers"] == "paragraph" and crop["lines"] == [1, 8]
    assert image.height >= 8 * LINE_PIXELS
    assert len(crop["spots"]) == 4


def test_a_publisher_error_stays_as_printed_and_is_listed_in_the_report(tmp_path, language_provider):
    language_provider.on("reconstruction", lambda request: answer_spots({
        spot_id(request, "zasedáni"): {"text": "zasedáni", "confidence": 93, "error": "publisher",
                                       "intended": "zasedání", "reasoning": "The print lacks the accent."},
    })(request))
    workflow, project = run(tmp_path, language_provider,
                            PrintedOcr(readings={word_index("zasedání"): ("zasedáni", 60.0)}))

    words = [text for text, _ in exported(workflow, project)]
    assert words[word_index("zasedání")] == "zasedáni"
    section = workflow.report(project["id"])["sections"]["reconstruct"]
    assert section["total"]["publisher_errors"] == 1
    [error] = section["publisher_errors"]
    assert (error["page"], error["printed"], error["intended"]) == (0, "zasedáni", "zasedání")
    summary = workflow.report_summary(project["id"])
    assert "## AI reconstruction" in summary and "printed 'zasedáni', meant 'zasedání'" in summary
    [word] = workflow.overlay(project["id"], "reconstruct", 0)["words"]
    assert word["status"] == "publisher_error"


def ink(source, box) -> numpy.ndarray:
    """The dark pixels of the scan inside a box, as (x, y) points in source page coordinates."""
    with pymupdf.open(source) as document:
        page = document[0]
        image = pymupdf.Pixmap(document, page.get_images()[0][0])
        scale = image.width / page.rect.width
    pixels = numpy.frombuffer(image.samples, dtype=numpy.uint8).reshape(image.height, image.width)
    x0, y0, x1, y1 = (round(value * scale) for value in box)
    ys, xs = numpy.nonzero(pixels[y0:y1, x0:x1] < 128)
    return numpy.stack([(xs + x0 + 0.5) / scale, (ys + y0 + 0.5) / scale], axis=1)


def inside(points: numpy.ndarray, box, tolerance: float = 0.3) -> bool:
    return bool(((points[:, 0] >= box[0] - tolerance) & (points[:, 0] <= box[2] + tolerance)
                 & (points[:, 1] >= box[1] - tolerance) & (points[:, 1] <= box[3] + tolerance)).all())


def test_a_merge_or_split_proposal_gets_word_boxes_measured_locally_that_cover_the_ink(tmp_path, language_provider):
    def merge_and_split(request):
        merged = [spot["id"] for spot in request.data["spots"] if spot["reading"] in ("Star", "osta")]
        [joined] = [spot["id"] for spot in request.data["spots"] if spot["reading"] == "opravuškoly"]
        return answer_spots({tuple(merged): {"text": "Starosta"},
                             joined: {"words": ["opravu", "školy"]}})(request)

    language_provider.on("reconstruction", merge_and_split)
    workflow, project = run(tmp_path, language_provider,
                            PrintedOcr(split={word_index("Starosta"): 0.5}, joined=[word_index("opravu")]))

    assert [text for text, _ in exported(workflow, project)] == [text for text, _ in PRINTED]
    boxes = {word["text"]: word["bbox"] for word in workflow.overlay(project["id"], "reconstruct", 0)["words"]}
    source = tmp_path / "zpravodaj.pdf"
    for text in ("Starosta", "opravu", "školy"):
        printed = PRINTED[word_index(text)][1]
        word_ink = ink(source, printed)
        assert len(word_ink) > 50 and inside(word_ink, boxes[text]), text
        assert boxes[text][0] >= printed[0] - 1 and boxes[text][2] <= printed[2] + 1, text
    assert boxes["opravu"][2] < boxes["školy"][0]
    assert not inside(ink(source, PRINTED[word_index("školy")][1])[:1], boxes["opravu"])


def scripted_damage(request):
    return answer_spots({spot_id(request, "opr"): {"text": "opravu", "confidence": 95,
                                                    "reasoning": "The print shows 'opravu'."},
                         spot_id(request, "čtv"): {"text": "čtvrtek", "confidence": 40}})(request)


def test_every_reconstruction_records_its_input_candidate_confidence_reasoning_provider_and_model(
        tmp_path, language_provider):
    language_provider.on("reconstruction", scripted_damage)
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    records = workflow.result(project["id"], "reconstruct").read_json(page_file(0))["reconstructions"]
    assert len(records) == 3
    opravu = next(record for record in records if record["candidate"] == ["opravu"])
    assert opravu["status"] == "accepted" and opravu["confidence"] == 95
    assert opravu["reasoning"] == "The print shows 'opravu'."
    assert (opravu["provider"], opravu["model"]) == ("claude", "scripted-model")  # the project's provider
    assert opravu["input"]["spots"][0]["reading"] == "opr"
    assert f"[[{opravu['ids'][0]}: opr]]" in opravu["input"]["text"]
    assert opravu["input"]["context"]["scope"] in ("article", "neighbours")
    [crop] = opravu["input"]["images"]
    assert workflow.result(project["id"], "reconstruct").exists(crop)
    unanswered = next(record for record in records if record["input"]["spots"][0]["reading"] == "")
    assert (unanswered["status"], unanswered["reason"], unanswered["candidate"]) == ("unresolved", "no_answer", None)


def test_the_export_writes_the_reconstructed_text_and_the_markers_into_both_outputs(tmp_path, language_provider):
    language_provider.on("reconstruction", scripted_damage)
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    words = [text for text, _ in exported(workflow, project)]
    assert words[word_index("opravu")] == "opravu"
    assert words[word_index("čtvrtek")] == words[word_index("kronikářem")] == MARKER
    [markdown] = [output["name"] for output in workflow.outputs(project["id"]) if output["name"].endswith(".md")]
    text = workflow.output(project["id"], markdown).read_text(encoding="utf-8")
    assert "na opravu školy" in text and f"Ve {MARKER} proběhne" in text and f"místním {MARKER} o" in text
    assert " opr " not in text and "čtv " not in text


def test_the_report_counts_resolved_spots_markers_and_publisher_errors(tmp_path, language_provider):
    language_provider.on("reconstruction", scripted_damage)
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    section = workflow.report(project["id"])["sections"]["reconstruct"]
    assert section["total"] == {"spots": 3, "resolved": 1, "markers": 2, "kept": 0, "publisher_errors": 0}
    assert section["pages"] == [{"index": 0, "spots": 3, "resolved": 1, "markers": 2, "kept": 0,
                                 "publisher_errors": 0, "rejected": None}]
    assert "3 problem spots: 1 resolved, 2 illegible markers, 0 doubtful readings kept as read (listed for " \
           "review), 0 publisher errors" in \
        workflow.report_summary(project["id"])


def test_without_an_ai_provider_every_unreadable_word_gets_the_marker_and_the_step_warns(tmp_path):
    workflow, project = run(tmp_path, None, PrintedOcr(readings=DAMAGED))

    reconstruct = next(step for step in project["steps"] if step["id"] == "reconstruct")
    assert reconstruct["state"] == "warning" and "AI není k dispozici" in reconstruct["message"]
    words = [text for text, _ in exported(workflow, project)]
    assert [words[index] for index in DAMAGED] == [MARKER] * 3
    hints = {word["hint"] for word in workflow.overlay(project["id"], "reconstruct", 0)["words"]}
    assert hints == {"opr", "čtv", ""}


def test_a_lower_acceptance_threshold_accepts_a_less_confident_reading(tmp_path, language_provider):
    language_provider.on("reconstruction", scripted_damage)
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED),
                            reconstruction_accept_confidence=30)

    assert [text for text, _ in exported(workflow, project)][word_index("čtvrtek")] == "čtvrtek"


def test_the_api_serves_the_reconstruction_preview_and_takes_the_acceptance_threshold(client, workflow,
                                                                                      source_pdf):
    project = workflow.create(source_pdf)
    settings = f"/api/projects/{project['id']}/settings"

    assert client.get(settings).json()["reconstruction_accept_confidence"] == 80
    saved = client.put(settings, json={"reconstruction_accept_confidence": 65}).json()
    assert saved["settings"]["reconstruction_accept_confidence"] == 65
    assert client.put(settings, json={"reconstruction_accept_confidence": 101}).status_code == 422
    listing = {step["id"]: step["preview"] for step in client.get("/api/projects").json()["steps"]}
    assert listing["reconstruct"] is True
    assert "unresolved" in client.get("/previews/reconstruct.js").text


DOUBTFUL = {word_index("zasedání"): ("zasedáni", 60.0)}  # clearly read, but not in the dictionary


def test_without_an_ai_provider_a_doubtful_reading_keeps_its_printed_text_and_only_unreadable_words_get_the_marker(
        tmp_path):
    workflow, project = run(tmp_path, None, PrintedOcr(readings={**DAMAGED, **DOUBTFUL}))

    assert problem_spots(workflow, project)[ocr_id(workflow, project, "zasedáni")]["kind"] == "doubtful"
    words = [text for text, _ in exported(workflow, project)]
    assert [words[index] for index in DAMAGED] == [MARKER] * 3
    assert words[word_index("zasedání")] == "zasedáni"
    kept = next(word for word in workflow.overlay(project["id"], "reconstruct", 0)["words"]
                if word["hint"] == "zasedáni")
    assert (kept["status"], kept["reason"], kept["text"]) == ("unresolved", "unavailable", "zasedáni")
    section = workflow.report(project["id"])["sections"]["reconstruct"]
    assert section["total"] == {"spots": 4, "resolved": 0, "markers": 3, "kept": 1, "publisher_errors": 0}
    assert "4 problem spots: 0 resolved, 3 illegible markers, 1 doubtful reading kept as read" in \
        workflow.report_summary(project["id"])


@pytest.mark.parametrize("outcome", ["low_confidence", "rejected"])
def test_an_unsettled_doubtful_reading_keeps_its_printed_text(tmp_path, language_provider, outcome):
    def answer(request):
        found = answer_spots({spot_id(request, "zasedáni"): {"text": "zasedání", "confidence": 30}})(request)
        if outcome == "rejected":
            found["readings"][0]["confidence"] = 300
        return found

    language_provider.on("reconstruction", answer)
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DOUBTFUL))

    assert [text for text, _ in exported(workflow, project)][word_index("zasedání")] == "zasedáni"
    [word] = workflow.overlay(project["id"], "reconstruct", 0)["words"]
    assert (word["status"], word["reason"], word["text"]) == ("unresolved", outcome, "zasedáni")
