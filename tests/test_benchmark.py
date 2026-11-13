"""Seam: a benchmark trial (ocrmaker.benchmark.Benchmark), the workflow run on a benchmark document with one
configuration, exported without manual corrections and scored against the document's reference transcription.

The AI is scripted (fakes.default_provider); OCR, layout and the scoring are the real ones.
"""
from __future__ import annotations

import pytest
from pdfs import SAMPLE_TEXT, scan_pdf

from ocrmaker.benchmark import Benchmark, summary
from ocrmaker.language import USAGE_LIMIT, LanguageError


def sample_document(tmp_path):
    """A one-page scan and its reference: the same scan with the printed text as its text layer."""
    source = scan_pdf(tmp_path / "scan.pdf", pages=1)
    reference = scan_pdf(tmp_path / "scan-ocr.pdf", pages=1, invisible_text=f"{SAMPLE_TEXT} (1)")
    return source, reference


def test_trials_share_the_local_steps_and_are_scored_against_the_reference(tmp_path, language_provider):
    benchmark = Benchmark(tmp_path / "benchmark", language=language_provider)
    benchmark.add_document("sample", *sample_document(tmp_path))

    first, = benchmark.run({"ai_provider": "claude", "ai_model": "haiku", "ai_effort": "high"}, label="haiku high")
    second, = benchmark.run({"ai_provider": "claude", "ai_model": "sonnet"})

    assert (first["document"], first["label"]) == ("sample", "haiku high")
    assert (first["settings"]["ai_model"], first["settings"]["ai_effort"]) == ("haiku", "high")
    words = len(f"{SAMPLE_TEXT} (1)".split())
    assert first["totals"]["reference_words"] == words
    assert first["totals"]["correct"] == words  # a clean synthetic print reads perfectly
    # Import and image preparation ran once, for the first trial only; the layout regions come from the AI, so
    # they belong to the configuration and not to the shared base.
    assert first["base"] == second["base"]
    ran = {step["id"] for step in second["steps"] if not step["shared"]}
    assert ran == {"regions", "ocr", "layout", "order", "problems", "reconstruct", "lookup", "review", "export"}
    # No person approves a trial's layout: it measures the automatic result.
    assert first["settings"]["layout_auto_approve"] is True
    assert second["ai"]["requests"] >= 1
    assert [trial["id"] for trial in benchmark.trials()] == [first["id"], second["id"]]


def test_a_trial_records_the_commit_it_started_on(tmp_path, language_provider, monkeypatch):
    # Trials run for many minutes; committing the next change meanwhile must not relabel the running trial.
    head = {"commit": "1111111"}
    monkeypatch.setattr("ocrmaker.benchmark.commit", lambda: head["commit"])
    benchmark = Benchmark(tmp_path / "benchmark", language=language_provider)
    benchmark.add_document("sample", *sample_document(tmp_path))
    run = benchmark.workflow.run

    def run_while_committing(project_id):
        run(project_id)
        head["commit"] = "2222222"
    benchmark.workflow.run = run_while_committing

    trial, = benchmark.run({"ai_provider": "claude"})

    assert trial["commit"] == "1111111"


def test_a_trial_whose_run_failed_is_recorded_as_failed_with_nothing_exported(tmp_path, language_provider):
    benchmark = Benchmark(tmp_path / "benchmark", language=language_provider)
    benchmark.add_document("sample", *sample_document(tmp_path))
    language_provider.on("layout_regions", LanguageError("Limit předplatného je vyčerpaný.", code=USAGE_LIMIT))
    trial, = benchmark.run({"ai_provider": "claude"})
    assert trial["failed"] == {"step": "regions", "message": "Limit předplatného je vyčerpaný."}
    assert trial["review"] is None and trial["totals"]["correct"] == 0
    row = summary(trial)
    assert row["failed"] == "regions"
    # Text missing from the result is a silent error (GLOSSARY), so the silent column counts it.
    assert row["silent"] == trial["totals"]["reference_words"]


def test_a_reference_whose_pages_differ_from_the_scan_is_refused(tmp_path, language_provider):
    benchmark = Benchmark(tmp_path / "benchmark", language=language_provider)
    source = scan_pdf(tmp_path / "scan.pdf", pages=2)
    reference = scan_pdf(tmp_path / "scan-ocr.pdf", pages=1, invisible_text="Rychvald")
    with pytest.raises(ValueError, match="do not match"):
        benchmark.add_document("sample", source, reference)
