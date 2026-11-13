"""The application's own defaults, which the rest of the suite overrides (conftest)."""


def test_layout_regions_are_the_default_method(real_layout_method):
    # benchmark/JOURNAL.md 2026-11-11: reading order matters more than the 0.3-0.5 point of text accuracy.
    assert real_layout_method == "regions"
