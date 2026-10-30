"""Pinned hunspell dictionaries for the dictionary check, downloaded once into a shared cache, verified by SHA-256.

The dictionaries come from LibreOffice's dictionary repository at a pinned commit and are read with spylls
(a pure-Python hunspell). The setup script installs the Czech one; the other document languages are fetched
when a project needs them, as Tesseract models are (ocrmaker.tessdata).
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

# LibreOffice/dictionaries tag libreoffice-26.2.7.3.
COMMIT = "6aaf119ded9ae96db29d82a036250824ecf5eb9d"
URL = "https://raw.githubusercontent.com/LibreOffice/dictionaries/{commit}/{path}.{suffix}"
# Document language -> (path in the repository without suffix, {suffix: SHA-256}).
DICTIONARIES = {
    "ces": ("cs_CZ/cs_CZ", {"aff": "7ecb20620ecd46ebd9c36f3f33e69dd4eda385cba5b2bb4e6bc396d910e297f7",
                            "dic": "d8e8c88c006fdae72dac8c85df11b0c99a773e05a4ab0fcbe92244876668ca74"}),
    "slk": ("sk_SK/sk_SK", {"aff": "af67bbe8ea9dea74968ec01acd266b3f74177ca087ee6eb7898c576e0aef7a3d",
                            "dic": "3e3dbd5c6af8431a3a47652c69692f3f86d0cd82deb4418e49a057a33ef56063"}),
    "eng": ("en/en_US", {"aff": "e746c882dd6f303c2c46e7452804b9201115a6942cfeb15f18f8edf774d2e24e",
                         "dic": "f0b1a234bd178bdd01875b2a392a9647f888b8fe879f79c52aae62c2759b3647"}),
    "deu": ("de/de_DE_frami", {"aff": "646bf3333ac69c23e9d794533ee5241d6f755c359e8fe10a648f87613743d594",
                               "dic": "4ca3c958b0e5545910999bc246f668840bf8ede3df8e5e6790d05edd5a586c38"}),
    "pol": ("pl_PL/pl_PL", {"aff": "82973651651aa930335c865b339b98db376ca3dbf3a661b70b9eeb71fdf41dca",
                            "dic": "c0848440599eb88e5aca500418d5f389e562ec2c157b63dbe39d354658ffba49"}),
}
SETUP_LANGUAGES = ("ces",)


class DictionaryError(RuntimeError):
    """A dictionary could not be provided; the message is Czech and shown to the user."""


def dictionary_dir() -> Path:
    """The directory holding the dictionaries, shared by every checkout of the application."""
    override = os.environ.get("OCRMAKER_HUNSPELL")
    if override:
        return Path(override).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache) if cache and Path(cache).is_absolute() else Path.home() / ".cache"
    return base / "ai-ocr-maker" / "hunspell" / COMMIT[:12]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch(url: str, target: Path, expected: str, language: str) -> None:
    handle, partial = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".partial", dir=target.parent)
    os.close(handle)
    partial = Path(partial)
    try:
        try:
            with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as output:
                while chunk := response.read(1 << 20):
                    output.write(chunk)
        except OSError as exc:
            raise DictionaryError(f"Slovník pro jazyk „{language}“ se nepodařilo stáhnout: {exc}") from exc
        if _sha256(partial) != expected:
            raise DictionaryError(f"Stažený slovník pro jazyk „{language}“ nemá očekávaný otisk SHA-256.")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def ensure_dictionary(language: str, directory: Path | None = None) -> Path:
    """The verified dictionary of a language as a path without suffix (spylls' form), downloading it if needed."""
    if language not in DICTIONARIES:
        raise DictionaryError(f"Pro jazyk „{language}“ není k dispozici ověřený slovník.")
    path, hashes = DICTIONARIES[language]
    directory = directory or dictionary_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / Path(path).name
    for suffix, expected in hashes.items():
        target = stem.with_name(f"{stem.name}.{suffix}")
        if not (target.is_file() and _sha256(target) == expected):
            _fetch(URL.format(commit=COMMIT, path=path, suffix=suffix), target, expected, language)
    return stem


_loaded: dict[str, object] = {}
_lock = threading.Lock()


def speller(language: str):
    """The loaded hunspell dictionary of a language (spylls), kept for the life of the process."""
    with _lock:
        if language not in _loaded:
            from spylls.hunspell import Dictionary

            _loaded[language] = Dictionary.from_files(str(ensure_dictionary(language)))
        return _loaded[language]


def main() -> None:
    try:
        for language in SETUP_LANGUAGES:
            print(f"Slovník „{language}“ je ověřený: {ensure_dictionary(language)}")
    except DictionaryError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
