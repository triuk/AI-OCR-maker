"""Recognizer: Tesseract 5 (tesserocr) over a working raster, returning lines, words and chars (ocrmaker.words)."""
from __future__ import annotations

import unicodedata
from pathlib import Path

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
                word = {"text": _text(iterator.GetUTF8Text(RIL.WORD)),
                        "bbox": _box(to_source, iterator.BoundingBox(RIL.WORD)),
                        "confidence": round(iterator.Confidence(RIL.WORD), 2), "chars": []}
                line["words"].append(word)
            symbol = _text(iterator.GetUTF8Text(RIL.SYMBOL))
            box = iterator.BoundingBox(RIL.SYMBOL)
            if symbol and box:
                word["chars"].append({"text": symbol, "bbox": _box(to_source, box),
                                      "confidence": round(iterator.Confidence(RIL.SYMBOL), 2)})
            if not iterator.Next(RIL.SYMBOL):
                break
        # Whitespace-only junk boxes carry no word (ADR 0005); lines left without words go with them.
        kept, count = [], 0
        for line in lines:
            words = []
            for word in line["words"]:
                if word["text"]:
                    count += 1
                    words.append({"id": f"p{number}-w{count}", **word})
            if words:
                kept.append({"id": f"p{number}-l{len(kept) + 1}", **line, "words": words})
        return kept
