"""Shared fixtures: a workflow over a temporary project store and an authorised API client."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakes import default_provider  # noqa: E402
from pdfs import scan_pdf  # noqa: E402

from ocrmaker import settings  # noqa: E402
from ocrmaker.store import ProjectStore  # noqa: E402
from ocrmaker.workflow import Workflow  # noqa: E402

TOKEN = "test-token"


# The suite approves the AI's layout at once unless a test asks for layout rounds, module-scoped fixtures included.
REAL_AUTO_APPROVE = settings.DEFAULTS["layout_auto_approve"]
settings.DEFAULTS["layout_auto_approve"] = True


@pytest.fixture
def real_auto_approve() -> bool:
    """The application's own default of `layout_auto_approve`, which the rest of the suite overrides."""
    return REAL_AUTO_APPROVE


@pytest.fixture
def library(tmp_path) -> Path:
    return tmp_path / "library"


@pytest.fixture
def source_pdf(tmp_path) -> Path:
    return scan_pdf(tmp_path / "zpravodaj-2000-05.pdf", pages=2)


@pytest.fixture
def step_implementations():
    """The step implementations a test runs with; None means the ones shipped with the application."""
    return None


@pytest.fixture
def language_provider():
    """The AI stand-in for every provider name; script answers with language_provider.on(purpose, answer).

    Layout answers by default with one region over the whole page, Reading order links no article across
    pages; the read-through names no word; AI reconstruction confirms every problem spot's own reading and leaves empty word boxes unanswered
    (fakes.answer_spots).
    """
    return default_provider()


@pytest.fixture
def workflow(library, step_implementations, language_provider) -> Workflow:
    return Workflow(ProjectStore(library), steps=step_implementations, language=language_provider)


@pytest.fixture
def client(workflow):
    from fastapi.testclient import TestClient

    from ocrmaker.api import create_api

    with TestClient(create_api(workflow, token=TOKEN), headers={"X-Ocrmaker-Token": TOKEN}) as client:
        yield client
