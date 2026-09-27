"""Tests for `_fold_groups`: folding a run of consecutive `group_id`-tagged rows."""

from __future__ import annotations

from typing import Any

from bajutsu.common.report.rows import _fold_groups


def _row(group: str | None, group_id: int | None, *, failed: bool = False) -> dict[str, Any]:
    return {
        "group": group,
        "group_id": group_id,
        "result": {"cls": "ng" if failed else "ok", "text": "FAIL" if failed else "PASS"},
    }


def test_ungrouped_rows_pass_through_unchanged() -> None:
    rows = [_row(None, None), _row(None, None)]
    assert _fold_groups(rows) == rows


def test_a_run_of_grouped_rows_gets_one_heading() -> None:
    rows = [_row("login", 0), _row("login", 0), _row(None, None)]
    out = _fold_groups(rows)
    assert out[0]["heading"]["name"] == "login"
    assert out[0]["heading"]["count"] == 2
    assert out[0]["heading"]["id"] == 0
    assert out[1]["group_id"] == 0 and out[2]["group_id"] == 0
    assert out[3]["group_id"] is None


def test_a_passing_run_starts_hidden() -> None:
    rows = [_row("login", 0), _row("login", 0)]
    out = _fold_groups(rows)
    assert out[0]["heading"]["open"] is False
    assert out[1]["hidden"] is True
    assert out[2]["hidden"] is True


def test_a_run_with_a_failure_starts_open() -> None:
    rows = [_row("login", 0), _row("login", 0, failed=True)]
    out = _fold_groups(rows)
    assert out[0]["heading"]["open"] is True
    assert out[1]["hidden"] is False
    assert out[2]["hidden"] is False


def test_two_separate_invocations_of_the_same_name_get_separate_headings() -> None:
    # Two `group: { name: retry }` calls that land adjacent in the row list must not merge into
    # one fold — `_fold_groups` keys a run on `group_id`, not on the display name.
    rows = [_row("retry", 0), _row("retry", 1)]
    out = _fold_groups(rows)
    headings = [row["heading"] for row in out if "heading" in row]
    assert len(headings) == 2
    assert headings[0]["id"] == 0
    assert headings[1]["id"] == 1


def test_a_network_row_interrupting_a_group_splits_it_into_two_folds() -> None:
    # Accepted scope limit: an interrupted `group:` invocation renders as separate, independently
    # headed folds rather than one.
    exchange = {"group": None, "group_id": None, "result": None}
    rows = [_row("login", 0), exchange, _row("login", 0)]
    out = _fold_groups(rows)
    headings = [row["heading"] for row in out if "heading" in row]
    assert len(headings) == 2
    assert headings[0]["count"] == 1
    assert headings[1]["count"] == 1
