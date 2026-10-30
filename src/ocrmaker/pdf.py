"""Read-only facts about a source PDF that a project needs before any step runs."""
from __future__ import annotations

from pathlib import Path

import pymupdf


def inspect_source(path: Path) -> dict:
    """Page count of a readable PDF; anything the application cannot process is refused in Czech."""
    try:
        document = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise ValueError("Soubor není čitelné PDF.") from exc
    with document:
        if document.needs_pass:
            raise ValueError("PDF je chráněné heslem, takové soubory aplikace neotevře.")
        if document.page_count == 0:
            raise ValueError("PDF neobsahuje žádnou stránku.")
        return {"pages": document.page_count}
