"""Manual review: the manual step where an automatic run stops and the user finishes what automation left.

The user works through the review queue and corrects words (`ocrmaker.review`, Workflow.review_queue and
Workflow.correct) while this step is pending; making a correction never runs a step (ADR 0004). The step
itself runs only as part of an export, right before Export: it hands the manual corrections as they stand
to Export and records what review left open. Its result (`steps/review/`) holds

    corrections.json   the manual corrections Export applies (ocrmaker.corrections), as they were then
    review.json        {"counts": {"markers", "corrected", "remaining", "doubtful", "corrections"},
                        "markers": [queue items still showing the illegible marker (ocrmaker.review)],
                        "doubtful": [doubtful readings kept as read that no correction checked]}

Export never waits for the queue to be empty: remaining markers are exported as `[NEČITELNÉ]`, unchecked
doubtful readings as read, and both are listed in the report next to the manual corrections.
"""
from __future__ import annotations

from .. import corrections as layer
from .. import review
from ..step import ReportSection, Step, StepContext, StepResult

REVIEW_FILE = "review.json"
# Corrections and remaining markers listed one by one in the human summary; the report JSON has them all.
REPORTED = 50


class ReviewStep(Step):
    id = "review"

    def run(self, ctx: StepContext) -> None:
        corrections = ctx.corrections()
        state = review.queue(ctx, corrections)
        remaining = [item for item in state["items"] if item["correction"] is None]
        ctx.output.write_json(layer.CORRECTIONS_FILE, corrections)
        ctx.output.write_json(REVIEW_FILE, {
            "counts": state["counts"],
            "markers": [item for item in remaining if item["kind"] == review.MARKER_ITEM],
            "doubtful": [item for item in remaining if item["kind"] == review.DOUBTFUL_ITEM]})
        ctx.record("review", **state["counts"], all_corrections=len(corrections))

    def report(self, result: StepResult) -> ReportSection:
        """The manual corrections the export applied and the illegible markers it left."""
        corrections = result.read_json(layer.CORRECTIONS_FILE)
        state = result.read_json(REVIEW_FILE)
        counts = state["counts"]
        texts, notes = layer.text_corrections(corrections), layer.notes(corrections)
        summary = [f"{len(corrections)} manual corrections ({len(texts)} word texts, {len(notes)} region notes, "
                   f"{len(layer.of_kind(layer.ORDER, corrections))} article orders); "
                   f"{counts['markers']} illegible markers, {counts['corrected']} corrected, "
                   f"{counts['remaining']} remaining; {counts.get('doubtful', 0)} doubtful "
                   f"reading{'' if counts.get('doubtful', 0) == 1 else 's'} kept as read."]
        if texts:
            summary += ["", "Manual corrections:", ""]
            summary += [f"- page {item['page'] + 1}, {', '.join(item['ids'])}: {item['previous']!r} -> "
                        f"{item['text']!r}" for item in texts[:REPORTED]]
            if len(texts) > REPORTED:
                summary.append(f"- and {len(texts) - REPORTED} more (see the report JSON)")
        if notes:
            summary += ["", "Region notes:", ""]
            summary += [f"- page {item['page'] + 1}, region [{', '.join(f'{value:g}' for value in item['bbox'])}]: "
                        f"{item['text']!r}" for item in notes[:REPORTED]]
            if len(notes) > REPORTED:
                summary.append(f"- and {len(notes) - REPORTED} more (see the report JSON)")
        orders = layer.of_kind(layer.ORDER, corrections)
        if orders:
            summary += ["", "Manual article orders:", ""]
            summary += [f"- page {item['page'] + 1}: articles {', '.join(item['articles'])} "
                        f"(automatic: {', '.join(item['previous'])})" for item in orders]
        if state["markers"]:
            summary += ["", "Remaining illegible markers:", ""]
            summary += [f"- page {item['page'] + 1}, {', '.join(item['ids'])}: hint {item['hint']!r}, "
                        f"candidate {item['candidate']!r}" for item in state["markers"][:REPORTED]]
            if len(state["markers"]) > REPORTED:
                summary.append(f"- and {len(state['markers']) - REPORTED} more (see the report JSON)")
        doubtful = state.get("doubtful", [])
        if doubtful:
            summary += ["", "Doubtful readings kept as read, unchecked:", ""]
            summary += [f"- page {item['page'] + 1}, {', '.join(item['ids'])}: {item['hint']!r}, "
                        f"candidate {item['candidate']!r}" for item in doubtful[:REPORTED]]
            if len(doubtful) > REPORTED:
                summary.append(f"- and {len(doubtful) - REPORTED} more (see the report JSON)")
        fields = ("page", "ids", "hint", "candidate", "confidence", "reason", "source", "context")
        data = {"counts": counts, "corrections": corrections, "notes": notes,
                "markers": [{key: item[key] for key in fields} for item in state["markers"]],
                "doubtful": [{key: item[key] for key in fields} for item in doubtful]}
        return ReportSection("Manual review", data, summary)


STEP = ReviewStep()
