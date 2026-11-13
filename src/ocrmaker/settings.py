"""Per-project settings: defaults, Czech-labelled choices and validation."""
from __future__ import annotations

import unicodedata

from . import providers
from .language import DEFAULT_EFFORT, EFFORTS
from .problems import THRESHOLDS
from .tessdata import MODELS

# Document languages offered in the UI; each has a pinned tessdata_best model.
LANGUAGES = {"ces": "čeština", "slk": "slovenština", "eng": "angličtina", "deu": "němčina", "pol": "polština"}
assert set(LANGUAGES) <= set(MODELS)

DEFAULTS = {
    "language": "ces",
    "ai_provider": providers.DEFAULT,
    # None means the provider's default model.
    "ai_model": None,
    # Reasoning effort of every AI request.
    "ai_effort": DEFAULT_EFFORT,
    # Problem detection: Tesseract confidence thresholds (0-100) and words the project accepts as correct.
    **THRESHOLDS,
    "project_dictionary": [],
    # AI reconstruction: a reading at least this confident (0-100) is accepted, a less confident one is not.
    "reconstruction_accept_confidence": 80.0,
    # Web lookup: short web searches settle the spots AI reconstruction left unresolved (off: they stay unresolved).
    "web_lookup": True,
    # A run stops at Layout until the person approves the layout regions; on, the AI's layout is
    # approved at once (batch work, the benchmark).
    "layout_auto_approve": False,
}


def _language(value):
    if value not in LANGUAGES:
        raise ValueError("Neznámý jazyk dokumentu.")
    return value


def _ai_provider(value):
    if value not in providers.CATALOG:
        raise ValueError("Neznámý poskytovatel AI.")
    return value


def _ai_model(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Neplatný model AI.")
    return value


def _ai_effort(value):
    if value not in {effort["id"] for effort in EFFORTS}:
        raise ValueError("Neznámá úroveň uvažování AI.")
    return value


def _confidence(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        raise ValueError("Práh důvěry musí být číslo od 0 do 100.")
    return float(value)


def _switch(value):
    if not isinstance(value, bool):
        raise ValueError("Přepínač musí být zapnuto, nebo vypnuto.")
    return value


def _project_dictionary(value):
    """A list of words, or text with one word per line; kept NFC, stripped, without duplicates."""
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list) or not all(isinstance(word, str) for word in value):
        raise ValueError("Slovník projektu musí být seznam slov.")
    words = []
    for word in value:
        word = unicodedata.normalize("NFC", word.strip())
        if not word:
            continue
        if any(char.isspace() for char in word):
            raise ValueError(f"Položka slovníku projektu „{word}“ musí být jedno slovo.")
        if word not in words:
            words.append(word)
    return words


# One validator per setting.
VALIDATORS = {
    "language": _language,
    "ai_provider": _ai_provider,
    "ai_model": _ai_model,
    "ai_effort": _ai_effort,
    "problem_unreadable_confidence": _confidence,
    "problem_doubtful_confidence": _confidence,
    "project_dictionary": _project_dictionary,
    "reconstruction_accept_confidence": _confidence,
    "web_lookup": _switch,
    "layout_auto_approve": _switch,
}
assert set(VALIDATORS) == set(DEFAULTS)


def merged(current: dict, changes: dict) -> dict:
    """The settings after applying changes; unknown keys and invalid values are refused with a Czech message."""
    unknown = sorted(set(changes) - set(VALIDATORS))
    if unknown:
        raise ValueError(f"Neznámé nastavení: {', '.join(unknown)}.")
    result = DEFAULTS | current
    for key, value in changes.items():
        result[key] = VALIDATORS[key](value)
    if result["problem_unreadable_confidence"] > result["problem_doubtful_confidence"]:
        raise ValueError("Práh nečitelného slova nesmí být vyšší než práh pochybného čtení.")
    if {"ai_provider", "ai_model"} & set(changes) and result["ai_model"] is not None:
        provider = providers.CATALOG[result["ai_provider"]]
        if result["ai_model"] not in {model["id"] for model in provider.models()}:
            if "ai_model" in changes:
                raise ValueError(f"Model „{result['ai_model']}“ není u poskytovatele {provider.label} k dispozici.")
            result["ai_model"] = None  # another provider was chosen: its default model replaces the old one
    return result


def choices() -> dict:
    """What the UI offers for settings with a fixed set of values."""
    return {"language": [{"id": key, "label": label} for key, label in LANGUAGES.items()],
            "ai_provider": [{"id": name, "label": provider.label, "default_model": provider.default_model()}
                            for name, provider in providers.CATALOG.items()],
            "ai_model": {name: provider.models() for name, provider in providers.CATALOG.items()},
            "ai_effort": EFFORTS}
