"""Seam: scoring an automatic result against a reference transcription (ocrmaker.evaluation.compare).

Pages are lists of words in reading order (findings number pages from 1); boxes are PDF points (x0, y0, x1, y1). Our words carry the id and
whether manual review lists them; reference words only text and box.
"""
from __future__ import annotations

from ocrmaker.evaluation import compare


def ref(text, x0, y0=100, width=40, height=12):
    return {"text": text, "bbox": [x0, y0, x0 + width, y0 + height]}


def our(text, x0, y0=100, width=40, height=12, *, review=False, word_id=None):
    return {"id": word_id or f"w{x0}-{y0}", "text": text, "bbox": [x0, y0, x0 + width, y0 + height],
            "review": review}


def kinds(result):
    return sorted(finding["kind"] for finding in result["findings"])


def test_an_identical_page_scores_every_word_correct():
    reference = [[ref("Rychvald", 10), ref("2000", 60)]]
    ours = [[our("Rychvald", 10), our("2000", 60)]]
    result = compare(ours, reference)
    assert result["totals"]["reference_words"] == 2
    assert result["totals"]["correct"] == 2
    assert result["findings"] == []


def test_a_wrong_word_is_silent_unless_manual_review_lists_it():
    reference = [[ref("Rychvald", 10), ref("2000", 60), ref("květen", 110)]]
    ours = [[our("Rychva1d", 10), our("[NEČITELNÉ]", 60, review=True), our("květen", 110, review=True)]]
    result = compare(ours, reference)
    assert kinds(result) == ["flagged", "silent", "unnecessary"]
    silent = next(f for f in result["findings"] if f["kind"] == "silent")
    assert (silent["page"], silent["reference"], silent["ours"], silent["review"]) == (1, "Rychvald", "Rychva1d", False)
    assert silent["ids"] == ["w10-100"]
    assert silent["bbox"] == [10, 100, 50, 112]
    # A doubtful reading listed for review although it was read right costs review time for nothing.
    assert result["totals"]["unnecessary"] == 1 and result["totals"]["correct"] == 1


def test_text_missing_from_the_result_is_reported_as_missing_not_as_a_wrong_word():
    reference = [[ref("ZPRAVODAJ", 10), ref("2000", 400, y0=20)]]
    ours = [[our("ZPRAVODAJ", 10)]]
    result = compare(ours, reference)
    assert kinds(result) == ["missing"]
    assert result["findings"][0]["reference"] == "2000" and result["findings"][0]["ids"] == []


def test_a_box_where_the_reference_has_no_word_is_extra():
    result = compare([[our("Rychvald", 10), our("~", 300, y0=500)]], [[ref("Rychvald", 10)]])
    assert kinds(result) == ["extra"]


def test_a_word_cut_into_several_boxes_is_a_split_and_its_text_is_judged_whole():
    reference = [[ref("RYCHVALD", 100, width=260)]]
    ours = [[our("RY", 100, width=60), our("(SH", 165, width=60), our("VA", 230, width=60), our("LD", 295, width=60)]]
    result = compare(ours, reference)
    assert kinds(result) == ["silent", "split"]
    assert result["findings"][0]["ours"] == "RY (SH VA LD"


def test_two_printed_words_in_one_box_are_merged():
    reference = [[ref("do", 10, width=18), ref("roku", 32, width=30)]]
    result = compare([[our("doroku", 10, width=52)]], reference)
    assert kinds(result) == ["merged", "silent"]


def test_a_box_slightly_off_is_fine_and_one_far_off_is_misplaced():
    # One point wider and a little lower, as boxes over blurred print are: still the same word.
    close = compare([[our("Rychvald", 10, y0=101, width=41)]], [[ref("Rychvald", 10)]])
    assert close["findings"] == [] and close["totals"]["correct"] == 1
    # Shifted by most of its own height (an underline pulled it down): it still overlaps, but badly.
    far = compare([[our("Rychvald", 10, y0=105, height=14)]], [[ref("Rychvald", 10)]])
    assert kinds(far) == ["box"]


def test_words_read_out_of_the_reference_order_are_order_errors():
    # Two columns; the result reads the right one first.
    left = [ref("Sedmé", 10, y0=100), ref("jednání", 10, y0=120)]
    right = [ref("Karnevalové", 300, y0=100), ref("show", 300, y0=120)]
    ours = [[our("Karnevalové", 300, y0=100), our("show", 300, y0=120),
             our("Sedmé", 10, y0=100), our("jednání", 10, y0=120)]]
    result = compare(ours, [left + right])
    # One misplaced run of two words, not two findings.
    assert (result["totals"]["order"], result["totals"]["order_words"]) == (1, 2)
    assert result["findings"][0]["reference"] == "Sedmé jednání"
    assert result["totals"]["correct"] == 4
    # The same words in the reference order are fine.
    assert compare([[our(w["text"], w["bbox"][0], y0=w["bbox"][1]) for w in left + right]],
                   [left + right])["findings"] == []


def reference_pdf(path, pages):
    """A PDF whose text (visible here; an OCR text layer reads the same) is the given words per page.

    The base font has no Czech letters, hence the plain words."""
    import pymupdf
    document = pymupdf.open()
    for words in pages:
        page = document.new_page(width=400, height=300)
        for text, x, y in words:
            page.insert_text((x, y), text, fontsize=12, fontname="helv")
    document.save(path)
    return path


def test_the_reference_is_read_from_the_pdf_text_layer_page_by_page_in_its_order(tmp_path):
    from ocrmaker.evaluation import read_reference
    pdf = reference_pdf(tmp_path / "ref.pdf", [[("Rychvald", 50, 50), ("2000", 150, 50)], [("cerven", 50, 50)]])
    pages = read_reference(pdf)
    assert [[word["text"] for word in page] for page in pages] == [["Rychvald", "2000"], ["cerven"]]
    x0, y0, x1, y1 = pages[0][0]["bbox"]
    assert 49 <= x0 <= 51 and 35 <= y0 < 50 < y1 <= 55 and x1 > x0 + 30


def test_corrections_of_the_reference_replace_remove_and_add_words(tmp_path):
    from ocrmaker.evaluation import read_reference
    pdf = reference_pdf(tmp_path / "ref.pdf", [[("Rychva1d", 50, 50), ("~", 150, 50), ("2000", 250, 50)]])
    corrections = [
        {"page": 1, "bbox": [50, 40, 100, 52], "text": "Rychvald", "note": "the print reads Rychvald"},
        {"page": 1, "bbox": [149, 40, 158, 52], "text": "", "note": "a speck, no word"},
        {"page": 1, "bbox": [200, 40, 230, 52], "text": "roku", "note": "missing from the reference"},
    ]
    pages = read_reference(pdf, corrections)
    assert [word["text"] for word in pages[0]] == ["Rychvald", "roku", "2000"]
    assert pages[0][1]["bbox"] == [200, 40, 230, 52]
