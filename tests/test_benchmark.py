"""Seam: a benchmark trial (ocrmaker.benchmark.Benchmark), the workflow run on a benchmark document with one
configuration, exported without manual corrections and scored against the document's reference transcription.

The AI is scripted (fakes.default_provider); OCR, layout and the scoring are the real ones.
"""
from __future__ import annotations

from pdfs import SAMPLE_TEXT, scan_pdf

from ocrmaker.benchmark import Benchmark


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
    # Import, image preparation, local OCR and layout ran once, for the first trial only.
    assert first["base"] == second["base"]
    ran = {step["id"] for step in second["steps"] if not step["shared"]}
    assert ran == {"order", "problems", "reconstruct", "lookup", "review", "export"}
    assert second["ai"]["requests"] >= 1
    assert [trial["id"] for trial in benchmark.trials()] == [first["id"], second["id"]]
