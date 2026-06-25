"""Provenance display grouping (bajutsu/provenance.py, BE-0044).

Pure: given each step's `from:` in order, decide where to *show* the label so a run of identical
consecutive values is labeled once (the emergent grouping BE-0044 specifies). No I/O, no scenario.
"""

from __future__ import annotations

from bajutsu.provenance import grouped_provenance


def test_consecutive_equal_values_are_labeled_once() -> None:
    assert grouped_provenance(["open", "open", "reindex"]) == ["open", None, "reindex"]


def test_a_gap_breaks_a_run_so_the_value_shows_again() -> None:
    # None (no provenance) never displays and ends the run, so the later equal value is a new group.
    assert grouped_provenance(["open", None, "open"]) == ["open", None, "open"]


def test_all_absent_shows_nothing() -> None:
    assert grouped_provenance([None, None]) == [None, None]


def test_leading_absent_then_a_run() -> None:
    assert grouped_provenance([None, "go", "go"]) == [None, "go", None]


def test_empty_string_is_treated_as_absent() -> None:
    # An empty `from:` carries no provenance — it must not render an empty label.
    assert grouped_provenance(["", "go"]) == [None, "go"]


def test_empty_input() -> None:
    assert grouped_provenance([]) == []
