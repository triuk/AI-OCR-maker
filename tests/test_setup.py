"""The pinned Tesseract model: hash-checked downloads and a model the bundled Tesseract can load."""
from __future__ import annotations

import hashlib

import pytest

from ocrmaker import tessdata


def served(tmp_path, content: bytes) -> str:
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "ces.traineddata").write_bytes(content)
    return origin.as_uri() + "/{language}.traineddata"


def test_a_model_with_the_recorded_hash_is_installed(tmp_path):
    content = b"model bytes"
    models = {"ces": hashlib.sha256(content).hexdigest()}

    path = tessdata.ensure_model("ces", tmp_path / "tessdata", models=models, url=served(tmp_path, content))

    assert path.read_bytes() == content


def test_a_download_with_a_different_hash_is_refused_and_leaves_nothing(tmp_path):
    models = {"ces": hashlib.sha256(b"expected").hexdigest()}
    target = tmp_path / "tessdata"

    with pytest.raises(tessdata.ModelError, match="SHA-256"):
        tessdata.ensure_model("ces", target, models=models, url=served(tmp_path, b"tampered"))
    assert list(target.iterdir()) == []


def test_a_damaged_installed_model_is_replaced(tmp_path):
    content = b"model bytes"
    models = {"ces": hashlib.sha256(content).hexdigest()}
    target = tmp_path / "tessdata"
    target.mkdir()
    (target / "ces.traineddata").write_bytes(b"truncated")

    path = tessdata.ensure_model("ces", target, models=models, url=served(tmp_path, content))

    assert path.read_bytes() == content


def test_the_installed_czech_model_loads_in_the_bundled_tesseract():
    import tesserocr

    model = tessdata.ensure_model("ces")  # installed by ./setup.sh, so this only verifies the hash

    with tesserocr.PyTessBaseAPI(path=str(model.parent), lang="ces") as api:
        assert api.GetInitLanguagesAsString() == "ces"
