"""Tests for the `completed` snapshot a group's check output renders (BE-0500 unit 5).

The snapshot reads job statuses, exit codes, and run manifests alone, and names every scenario that
did not pass with its outcome and the reason, so no model sits anywhere on what the check says.
"""

from __future__ import annotations

from typing import Any

import pytest

from bajutsu.serve.group_notify import group_snapshot


def _row(
    scenario: str,
    status: str,
    result: dict[str, Any],
    *,
    cancel_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": scenario,
        "status": status,
        "result": result,
        "cancel_reason": cancel_reason,
        "spec": {"intent": {"scenario": f"{scenario}.yaml"}},
    }


def _manifest(failure: str | None) -> dict[str, Any]:
    return {
        "scenarios": [
            {"scenario": "a", "ok": True},
            {"scenario": "b", "ok": False, "failure": failure},
        ]
    }


def test_a_passing_group_counts_its_scenarios_and_lists_none() -> None:
    rows = [_row("one", "done", {"exitCode": 0}), _row("two", "done", {"exitCode": 0})]

    snapshot = group_snapshot(rows, lambda _run: None)

    assert snapshot == {"conclusion": "success", "total": 2, "passed": 2, "rows": []}


def test_a_failed_scenario_shows_the_first_failure_line_of_its_manifest() -> None:
    manifests = {"r1": _manifest("expected 'Done' to be visible\nmore detail")}
    rows = [
        _row("one", "done", {"exitCode": 1, "runId": "r1"}),
        _row("two", "done", {"exitCode": 0}),
    ]

    snapshot = group_snapshot(rows, manifests.get)

    assert snapshot["conclusion"] == "failure"
    assert snapshot["passed"] == 1
    assert snapshot["rows"] == [
        {"scenario": "one.yaml", "outcome": "failed", "reason": "expected 'Done' to be visible"}
    ]


def test_a_failed_scenario_without_a_manifest_names_its_exit_code() -> None:
    rows = [_row("one", "done", {"exitCode": 1, "runId": "gone"})]

    assert group_snapshot(rows, lambda _run: None)["rows"] == [
        {
            "scenario": "one.yaml",
            "outcome": "failed",
            "reason": "exit code 1: no failure detail recorded",
        }
    ]


@pytest.mark.parametrize(
    ("exit_code", "reason"),
    [
        (2, "exit code 2 (usage error)"),
        (137, "exit code 137 (signal 9)"),
        (-15, "exit code -15 (signal 15)"),
        (3, "exit code 3"),
        (None, "no exit code reported"),
    ],
)
def test_another_exit_code_is_an_error_with_its_meaning(exit_code: int | None, reason: str) -> None:
    rows = [_row("one", "done", {"exitCode": exit_code})]

    assert group_snapshot(rows, lambda _run: None)["rows"] == [
        {"scenario": "one.yaml", "outcome": "error", "reason": reason}
    ]


def test_a_lost_worker_is_told_from_a_reported_error() -> None:
    rows = [
        _row(
            "one",
            "failed",
            {"error": "lease expired after 3 attempts", "reason": "lease-expired", "attempts": 3},
        ),
        _row("two", "failed", {"error": "device vanished\ntraceback ..."}),
    ]

    assert group_snapshot(rows, lambda _run: None)["rows"] == [
        {
            "scenario": "one.yaml",
            "outcome": "worker lost",
            "reason": "lease expired after 3 attempts",
        },
        {"scenario": "two.yaml", "outcome": "worker error", "reason": "device vanished"},
    ]


def test_a_cancelled_job_is_cancelled_or_skipped_by_its_reason_whatever_its_exit_code() -> None:
    rows = [
        _row("one", "done", {"exitCode": 1, "runId": "r1"}),
        _row("two", "cancelled", {"cancelled": True}, cancel_reason="failFast"),
        _row("three", "done", {"exitCode": 1, "cancelled": True}, cancel_reason="user"),
    ]

    snapshot = group_snapshot(rows, lambda _run: None)

    assert [(r["scenario"], r["outcome"], r["reason"]) for r in snapshot["rows"]] == [
        ("one.yaml", "failed", "exit code 1: no failure detail recorded"),
        ("two.yaml", "skipped", ""),
        ("three.yaml", "cancelled", ""),
    ]
    assert snapshot["conclusion"] == "failure"


def test_a_job_with_no_intent_is_named_by_its_id() -> None:
    row = _row("one", "done", {"exitCode": 3})
    row["spec"] = {}

    assert group_snapshot([row], lambda _run: None)["rows"][0]["scenario"] == "job one"
