"""Per-project settings: defaults, Czech-labelled choices and validation."""
from __future__ import annotations

from .tessdata import MODELS

# Document languages offered in the UI; each has a pinned tessdata_best model.
LANGUAGES = {"ces": "čeština", "slk": "slovenština", "eng": "angličtina", "deu": "němčina", "pol": "polština"}
assert set(LANGUAGES) <= set(MODELS)

DEFAULTS = {
    "language": "ces",
}


def _language(value):
    if value not in LANGUAGES:
        raise ValueError("Neznámý jazyk dokumentu.")
    return value


# One validator per setting; later tickets add their settings (AI provider, thresholds, web lookup) here.
VALIDATORS = {
    "language": _language,
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
    return result


def choices() -> dict:
    """What the UI offers for settings with a fixed set of values."""
    return {"language": [{"id": key, "label": label} for key, label in LANGUAGES.items()]}
