"""Pinned tessdata_best models, downloaded once into a shared cache and verified by SHA-256."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

# tessdata_best tag 4.1.0; the commit is pinned so that the recorded hashes stay valid.
COMMIT = "e2aad9b983032bb1beff9133104a67cdbb87ca4d"
URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/{commit}/{language}.traineddata"
MODELS = {
    "ces": "d821773116d3c4e0360ea750066436c60ae45af02c39da3a840a543d761b3f41",
    "slk": "3553e335f64408412c8741fec19e443b5fda81d88abe59aa1401cba4e8825bed",
    "eng": "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba",
    "deu": "8407331d6aa0229dc927685c01a7938fc5a641d1a9524f74838cdac599f0d06e",
    "pol": "e80cc4cefbdface06e9223f43f089556b9dcf104020fbc0a200f6863c57d4405",
}
# The setup script installs only these; other pinned languages are fetched when a project needs them.
SETUP_LANGUAGES = ("ces",)


class ModelError(RuntimeError):
    """A model could not be provided; the message is Czech and shown to the user."""


def tessdata_dir() -> Path:
    """The directory holding the models, shared by every checkout of the application."""
    override = os.environ.get("OCRMAKER_TESSDATA")
    if override:
        return Path(override).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache) if cache and Path(cache).is_absolute() else Path.home() / ".cache"
    return base / "ai-ocr-maker" / "tessdata-best" / COMMIT[:12]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_model(language: str, directory: Path | None = None, *, models: dict[str, str] = MODELS,
                 url: str = URL) -> Path:
    """Return the verified model file for a language, downloading it first when missing or damaged."""
    if language not in models:
        raise ModelError(f"Pro jazyk „{language}“ není k dispozici ověřený model Tesseractu.")
    directory = directory or tessdata_dir()
    target = directory / f"{language}.traineddata"
    expected = models[language]
    if target.is_file() and _sha256(target) == expected:
        return target
    directory.mkdir(parents=True, exist_ok=True)
    handle, partial = tempfile.mkstemp(prefix=f".{language}.", suffix=".partial", dir=directory)
    os.close(handle)
    partial = Path(partial)
    try:
        try:
            with urllib.request.urlopen(url.format(commit=COMMIT, language=language), timeout=60) as response, \
                    partial.open("wb") as output:
                while chunk := response.read(1 << 20):
                    output.write(chunk)
        except OSError as exc:
            raise ModelError(f"Model Tesseractu pro jazyk „{language}“ se nepodařilo stáhnout: {exc}") from exc
        if _sha256(partial) != expected:
            raise ModelError(f"Stažený model Tesseractu pro jazyk „{language}“ nemá očekávaný otisk SHA-256.")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def main() -> None:
    try:
        for language in SETUP_LANGUAGES:
            path = ensure_model(language)
            print(f"Model Tesseractu „{language}“ je ověřený: {path}")
    except ModelError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
