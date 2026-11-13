"""The application's own defaults, which the rest of the suite overrides (conftest)."""


def test_a_run_stops_at_layout_for_the_person_by_default(real_auto_approve):
    assert real_auto_approve is False
