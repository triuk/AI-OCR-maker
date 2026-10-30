"""The language providers the application offers: Claude (`claude -p`) and Codex (`codex exec`)."""
from __future__ import annotations

from .claude import ClaudeProvider
from .codex import CodexProvider
from .language import LanguageProvider

DEFAULT = "claude"


def available() -> dict[str, LanguageProvider]:
    """One instance of each provider; the application passes them to its Workflow."""
    return {provider.name: provider for provider in (ClaudeProvider(), CodexProvider())}


# Used for labels and model lists in the settings; requests go through the Workflow's own instances.
CATALOG = available()
