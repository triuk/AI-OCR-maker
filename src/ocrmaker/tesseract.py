"""Recognizer: Tesseract 5 (tesserocr) over a working raster, returning lines, words and chars (ocrmaker.words)."""
from __future__ import annotations

import unicodedata
from pathlib import Path

import numpy
import tesserocr
from tesserocr import OEM, PSM, RIL, PyTessBaseAPI

from .pages import Matrix, apply, apply_rect


def version() -> str:
    return tesserocr.tesseract_version().split()[1]


def _box(to_source: Matrix, pixels) -> list[float]:
    return [round(value, 2) for value in apply_rect(to_source, pixels)]


def _point(to_source: Matrix, point) -> list[float]:
    return [round(value, 2) for value in apply(to_source, *point)]


def _text(raw: str | None) -> str:
    return unicodedata.normalize("NFC", (raw or "").strip())


# Tesseract reads a narrow space as none: "19. 2. 2000" becomes one word "19.2.2000", "8 do" becomes "8do". Within a
# word, a gap after "." or "," or between a digit and a letter at least this share of the word's letter height is
# such a space (printed narrow spaces measure 0.35-0.7 of it, the dot in a time like "16.15" at most 0.3).
SPACE_SHARE = 0.33


def split_narrow_spaces(word: dict) -> list[dict]:
    """The word (text, bbox, confidence, chars; boxes in display-upright pixels) cut at its narrow spaces
    (see SPACE_SHARE); each part keeps the word's confidence and vertical extent."""
    chars = word["chars"]
    heights = sorted(char["bbox"][3] - char["bbox"][1] for char in chars if char["text"].isalnum())
    if not heights or "".join(char["text"] for char in chars) != word["text"]:
        return [word]
    least = SPACE_SHARE * heights[len(heights) // 2]
    cuts = [0]
    for n in range(1, len(chars)):
        before, after = chars[n - 1]["text"][-1], chars[n]["text"][0]
        spaced = before in ".," or before.isalnum() and before.isdigit() != after.isdigit()
        if spaced and after.isalnum() and chars[n]["bbox"][0] - chars[n - 1]["bbox"][2] >= least:
            cuts.append(n)
    if len(cuts) == 1:
        return [word]
    parts = [chars[start:end] for start, end in zip(cuts, cuts[1:] + [len(chars)])]
    return [{"text": "".join(char["text"] for char in part),
             "bbox": (part[0]["bbox"][0], word["bbox"][1], part[-1]["bbox"][2], word["bbox"][3]),
             "confidence": word["confidence"], "chars": part} for part in parts]


class Recognizer:
    """One loaded Tesseract model; recognise any number of pages with it, then close it."""

    def __init__(self, model: Path, language: str):
        self.language = language
        self._api = PyTessBaseAPI(path=str(model.parent), lang=language, psm=PSM.AUTO, oem=OEM.LSTM_ONLY)

    def close(self) -> None:
        self._api.End()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def recognize(self, raster: Path, *, index: int, dpi: float, to_source: Matrix) -> dict:
        """The page file content (see ocrmaker.words) of one working raster, in source page coordinates."""
        api = self._api
        api.SetImageFile(str(raster))
        api.SetSourceResolution(round(dpi))
        api.Recognize()
        return {"index": index, "lines": self._lines(index, to_source)}

    def recognize_line(self, raster: Path, box: tuple[int, int, int, int], *, index: int, dpi: float,
                       to_source: Matrix) -> list[dict]:
        """The words (without ids) Tesseract reads in one region of a working raster (pixels) taken as a
        single text line: for ink the automatic page segmentation dropped."""
        api = self._api
        api.SetImageFile(str(raster))
        api.SetSourceResolution(round(dpi))
        pad = max(4, round((box[3] - box[1]) / 2))
        left, top = max(0, box[0] - pad), max(0, box[1] - pad)
        api.SetRectangle(left, top, box[2] + pad - left, box[3] + pad - top)
        api.SetPageSegMode(PSM.SINGLE_LINE)
        try:
            api.Recognize()
            return [word for line in self._lines(index, to_source) for word in line["words"]]
        finally:
            api.SetPageSegMode(PSM.AUTO)

    def recognize_pixels(self, pixels: numpy.ndarray, *, index: int, dpi: float, to_source: Matrix) -> dict:
        """The page file content of a grey image (raster pixels, uint8) instead of a raster file: for a page
        prepared in memory, such as inkgaps.cleared_page."""
        api = self._api
        height, width = pixels.shape
        api.SetImageBytes(numpy.ascontiguousarray(pixels).tobytes(), width, height, 1, width)
        api.SetSourceResolution(round(dpi))
        api.Recognize()
        return {"index": index, "lines": self._lines(index, to_source)}

    def _lines(self, index: int, to_source: Matrix) -> list[dict]:
        """The recognised lines of the last Recognize, words numbered on the page."""
        api = self._api
        lines, line, word = [], None, None
        block = paragraph = -1
        iterator = api.GetIterator()
        number = index + 1
        while iterator and not iterator.Empty(RIL.SYMBOL):
            if iterator.IsAtBeginningOf(RIL.BLOCK):
                block += 1
            if iterator.IsAtBeginningOf(RIL.PARA):
                paragraph += 1
            if iterator.IsAtBeginningOf(RIL.TEXTLINE):
                baseline = iterator.Baseline(RIL.TEXTLINE)
                line = {"bbox": _box(to_source, iterator.BoundingBox(RIL.TEXTLINE)),
                        "baseline": [_point(to_source, point) for point in baseline] if baseline else None,
                        "block": block, "paragraph": paragraph, "words": []}
                lines.append(line)
            if iterator.IsAtBeginningOf(RIL.WORD):
                word = {"text": _text(iterator.GetUTF8Text(RIL.WORD)), "bbox": iterator.BoundingBox(RIL.WORD),
                        "confidence": round(iterator.Confidence(RIL.WORD), 2), "chars": []}
                line["words"].append(word)
            symbol = _text(iterator.GetUTF8Text(RIL.SYMBOL))
            box = iterator.BoundingBox(RIL.SYMBOL)
            if symbol and box:
                word["chars"].append({"text": symbol, "bbox": box,
                                      "confidence": round(iterator.Confidence(RIL.SYMBOL), 2)})
            if not iterator.Next(RIL.SYMBOL):
                break
        # Whitespace-only junk boxes carry no word (ADR 0005); lines left without words go with them.
        kept, count = [], 0
        for line in lines:
            words = []
            for word in (part for word in line["words"] if word["text"] for part in split_narrow_spaces(word)):
                count += 1
                words.append({"id": f"p{number}-w{count}", **word, "bbox": _box(to_source, word["bbox"]),
                              "chars": [{**char, "bbox": _box(to_source, char["bbox"])} for char in word["chars"]]})
            if words:
                kept.append({"id": f"p{number}-l{len(kept) + 1}", **line, "words": words})
        return kept
