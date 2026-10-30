"""Domain exceptions; their messages are Czech because the UI shows them as they are."""
from __future__ import annotations


class Conflict(RuntimeError):
    """The request clashes with the project's current state, such as a run already in progress."""


class ConfirmationRequired(Conflict):
    """A rerun would discard manual corrections and the user has not confirmed how many."""

    def __init__(self, message: str, corrections: int):
        super().__init__(message)
        self.corrections = corrections


class StepError(RuntimeError):
    """A step failed for a reason the user can act on; earlier results stay intact."""


class StepInterrupted(RuntimeError):
    """The user stopped the run; the interrupted step returns to pending."""
