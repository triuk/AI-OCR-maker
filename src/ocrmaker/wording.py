"""Czech wording shared by the steps' user-facing messages."""
from __future__ import annotations

# Page lists show at most this many page numbers, then "…".
SHOWN_PAGES = 10


def page_numbers(numbers: list[int]) -> str:
    """1-based page numbers as "1, 2, 3 …"."""
    shown = ", ".join(str(number) for number in numbers[:SHOWN_PAGES])
    return shown + (" …" if len(numbers) > SHOWN_PAGES else "")


def on_pages(numbers: list[int]) -> str:
    """"na straně 3" / "na stranách 1, 2, 3 …"."""
    return ("na straně " if len(numbers) == 1 else "na stranách ") + page_numbers(numbers)


def named_pages(numbers: list[int]) -> str:
    """"strana 3" / "strany 1, 2, 3 …"."""
    return ("strana " if len(numbers) == 1 else "strany ") + page_numbers(numbers)
