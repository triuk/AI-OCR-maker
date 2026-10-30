"""The fixed workflow: ten steps in order, and discovery of the steps implemented so far.

A step is implemented by adding the module `ocrmaker/steps/<id>_step.py` with a module-level `STEP`,
an instance of `ocrmaker.step.Step` whose `id` matches its definition below. Nothing else needs to change:
`implementations()` finds the module, and a step without a module stays pending and is skipped by a run.
"""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass

from ..step import Step


@dataclass(frozen=True)
class StepDefinition:
    id: str
    label: str  # Czech, shown on the workflow tile
    # A manual step is where an automatic run stops and waits for the user.
    manual: bool = False

    @property
    def module(self) -> str:
        return f"{__name__}.{self.id}_step"


STEPS = (
    StepDefinition("import", "Import"),
    StepDefinition("prepare", "Příprava obrazu"),
    StepDefinition("ocr", "Lokální OCR"),
    StepDefinition("layout", "Rozvržení"),
    StepDefinition("order", "Pořadí čtení"),
    StepDefinition("problems", "Detekce problémů"),
    StepDefinition("reconstruct", "AI rekonstrukce"),
    StepDefinition("lookup", "Dohledání na webu"),
    StepDefinition("review", "Ruční kontrola", manual=True),
    StepDefinition("export", "Export"),
)
STEP_IDS = tuple(step.id for step in STEPS)
DEFINITIONS = {step.id: step for step in STEPS}


def implementations() -> dict[str, Step]:
    """The step implementations shipped with the application, keyed by step id."""
    found = {}
    for definition in STEPS:
        if importlib.util.find_spec(definition.module) is None:
            continue
        step = importlib.import_module(definition.module).STEP
        if step.id != definition.id:
            raise RuntimeError(f"{definition.module} implements step {step.id!r}, expected {definition.id!r}")
        found[definition.id] = step
    return found
