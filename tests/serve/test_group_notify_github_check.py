"""Tests for the `github-check` adapter (BE-0500 unit 7).

The adapter writes one check run per group on the pull request's head commit, recovering the run a
crashed delivery created, and taking over an earlier attempt's finished run, rather than adding a
second. Its output escapes every reported reason, so no worker's text can reshape the check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bajutsu.serve.group_notify import GroupSink
from bajutsu.serve.group_notify.github_check import (
    SUMMARY_LIMIT,
    GitHubApiError,
    GitHubCheckAdapter,
    check_output,
)
from bajutsu.serve.server.db import GroupEventClaim

_SHA = "a" * 40
_SINK = GroupSink(name="github", format="github-check")
_AT = datetime(2026, 11, 24, 8, 30, tzinfo=UTC)
_LIST = f"GET /repos/acme/app/commits/{_SHA}/check-runs?check_name=bajutsu+%2F+demo&filter=all&per_page=100"


def _claim(
    event: str, *, reference: str | None = None, snapshot: dict[str, Any] | None = None
) -> GroupEventClaim:
    return GroupEventClaim(
        id="e1",
        token="t",
        group_id="g2",
        org_id="default",
        sink="github",
        event=event,
        occurred_at=_AT,
        attempts=0,
        reference=reference,
        snapshot=snapshot,
        notify={
            "repository": "acme/app",
            "target": "demo",
            "sinks": [{"sink": "github", "headSha": _SHA}],
        },
    )


class _GitHub:
    """A fake GitHub API: answers each call from ``runs`` and records it."""

    def __init__(
        self,
        runs: list[dict[str, Any]] | None = None,
        *,
        refuse_patch: int | None = None,
        patch_status: str | None = None,
    ) -> None:
        self.runs = runs or []
        self.refuse_patch = refuse_patch
        self.patch_status = patch_status
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.tokens: list[str] = []

    def __call__(self, method: str, url: str, token: str, body: dict[str, Any] | None) -> Any:
        path = url.removeprefix("https://api.github.com")
        self.calls.append((f"{method} {path}", body))
        self.tokens.append(token)
        if method == "GET" and "/commits/" not in path:
            run_id = int(path.rsplit("/", 1)[1])
            known = {"id": run_id, "external_id": "g2"}
            return next((r for r in self.runs if r["id"] == run_id), known)
        if method == "GET":
            return {"total_count": len(self.runs), "check_runs": self.runs}
        if method == "POST":
            return {"id": 99, "status": (body or {}).get("status")}
        if self.refuse_patch:
            raise GitHubApiError(self.refuse_patch, "cannot reopen")
        run_id = int(path.rsplit("/", 1)[1])
        return {"id": run_id, "status": self.patch_status or (body or {}).get("status")}


def _adapter(github: _GitHub) -> GitHubCheckAdapter:
    return GitHubCheckAdapter(token_for=lambda repo: f"token-for-{repo}", request=github)


def test_queued_creates_a_run_named_for_the_target_on_the_head_commit() -> None:
    github = _GitHub()

    assert _adapter(github)(_claim("queued"), _SINK) == "99"

    assert github.calls == [
        (_LIST, None),
        (
            "POST /repos/acme/app/check-runs",
            {"name": "bajutsu / demo", "head_sha": _SHA, "status": "queued", "external_id": "g2"},
        ),
    ]
    assert set(github.tokens) == {"token-for-acme/app"}


def test_queued_adopts_the_run_a_crashed_delivery_created() -> None:
    github = _GitHub([{"id": 5, "external_id": "g2", "status": "queued"}])

    assert _adapter(github)(_claim("queued"), _SINK) == "5"
    assert [c for c, _ in github.calls] == [_LIST]


def test_queued_takes_over_the_newest_finished_run_of_an_earlier_attempt() -> None:
    github = _GitHub(
        [
            {"id": 3, "external_id": "g0", "status": "completed"},
            {"id": 4, "external_id": "g1", "status": "completed"},
            {"id": 6, "external_id": "g9", "status": "in_progress"},
        ]
    )

    assert _adapter(github)(_claim("queued"), _SINK) == "4"

    call, body = github.calls[-1]
    assert call == "PATCH /repos/acme/app/check-runs/4"
    assert body is not None
    assert body["status"] == "queued" and body["external_id"] == "g2"
    assert body["conclusion"] is None and body["completed_at"] is None
    assert body["output"]["title"]


def test_a_refused_takeover_creates_a_new_run() -> None:
    github = _GitHub([{"id": 4, "external_id": "g1", "status": "completed"}], refuse_patch=422)

    assert _adapter(github)(_claim("queued"), _SINK) == "99"
    assert github.calls[-1][0] == "POST /repos/acme/app/check-runs"


def test_a_takeover_answered_with_a_run_still_finished_creates_a_new_run() -> None:
    github = _GitHub(
        [{"id": 4, "external_id": "g1", "status": "completed"}], patch_status="completed"
    )

    assert _adapter(github)(_claim("queued"), _SINK) == "99"
    assert github.calls[-1][0] == "POST /repos/acme/app/check-runs"


@pytest.mark.parametrize("status", [403, 429, 502])
def test_a_takeover_that_fails_for_another_reason_retries_rather_than_creating(
    status: int,
) -> None:
    github = _GitHub([{"id": 4, "external_id": "g1", "status": "completed"}], refuse_patch=status)

    with pytest.raises(GitHubApiError):
        _adapter(github)(_claim("queued"), _SINK)
    assert all(not call.startswith("POST") for call, _ in github.calls)


def test_the_lookup_reads_this_apps_runs_alone() -> None:
    github = _GitHub()
    adapter = GitHubCheckAdapter(token_for=lambda _repo: "t", request=github, app_id="12")

    adapter(_claim("queued"), _SINK)

    assert github.calls[0][0] == _LIST + "&app_id=12"


def test_a_remnant_of_a_refused_takeover_is_not_taken_again() -> None:
    github = _GitHub(
        [
            {"id": 4, "external_id": "g2", "status": "completed"},
            {"id": 3, "external_id": "g1", "status": "completed"},
        ]
    )

    assert _adapter(github)(_claim("queued"), _SINK) == "99"
    assert github.calls[-1][0] == "POST /repos/acme/app/check-runs"


def test_started_moves_the_referenced_run_in_progress_at_the_events_time() -> None:
    github = _GitHub()

    assert _adapter(github)(_claim("started", reference="99"), _SINK) is None

    assert github.calls == [
        ("GET /repos/acme/app/check-runs/99", None),
        (
            "PATCH /repos/acme/app/check-runs/99",
            {"status": "in_progress", "started_at": "2026-11-24T08:30:00Z"},
        ),
    ]


def test_completed_writes_the_conclusion_and_the_output() -> None:
    github = _GitHub()
    snapshot = {
        "conclusion": "failure",
        "total": 2,
        "passed": 1,
        "rows": [{"scenario": "one.yaml", "outcome": "failed", "reason": "boom"}],
    }

    _adapter(github)(_claim("completed", reference="99", snapshot=snapshot), _SINK)

    call, body = github.calls[1]
    assert call == "PATCH /repos/acme/app/check-runs/99"
    assert body is not None
    assert body["status"] == "completed"
    assert body["conclusion"] == "failure"
    assert body["completed_at"] == "2026-11-24T08:30:00Z"
    assert body["output"]["title"] == "1 of 2 scenarios did not pass"


@pytest.mark.parametrize("event", ["started", "completed"])
def test_an_event_with_no_reference_recovers_or_creates_the_run(event: str) -> None:
    snapshot = {"conclusion": "success", "total": 1, "passed": 1, "rows": []}
    found = _GitHub([{"id": 7, "external_id": "g2", "status": "queued"}])
    fresh = _GitHub()

    _adapter(found)(_claim(event, snapshot=snapshot), _SINK)
    _adapter(fresh)(_claim(event, snapshot=snapshot), _SINK)

    assert found.calls[-1][0] == "PATCH /repos/acme/app/check-runs/7"
    call, body = fresh.calls[-1]
    assert call == "POST /repos/acme/app/check-runs"
    assert body is not None
    assert body["name"] == "bajutsu / demo" and body["external_id"] == "g2"
    assert body["status"] == ("in_progress" if event == "started" else "completed")


@pytest.mark.parametrize("event", ["started", "completed"])
def test_an_event_skips_a_run_a_later_group_took_over(event: str) -> None:
    github = _GitHub([{"id": 99, "external_id": "g3", "status": "queued"}])
    snapshot = {"conclusion": "success", "total": 1, "passed": 1, "rows": []}

    assert _adapter(github)(_claim(event, reference="99", snapshot=snapshot), _SINK) is None

    assert [c for c, _ in github.calls] == ["GET /repos/acme/app/check-runs/99"]


def test_a_github_error_raises_for_a_retry() -> None:
    def failing(method: str, url: str, token: str, body: dict[str, Any] | None) -> Any:
        raise GitHubApiError(502, "bad gateway")

    adapter = GitHubCheckAdapter(token_for=lambda _repo: "t", request=failing)

    with pytest.raises(GitHubApiError):
        adapter(_claim("started", reference="99"), _SINK)


def test_a_passing_group_says_so() -> None:
    output = check_output({"conclusion": "success", "total": 3, "passed": 3, "rows": []})

    assert output["title"] == "3 of 3 scenarios passed"
    assert "3 scenarios passed" in output["summary"]


def test_a_reported_reason_cannot_split_a_row_or_render_markdown() -> None:
    reason = "a | b `code` ``x``\x1b[31m\nnext"
    output = check_output(
        {
            "conclusion": "failure",
            "total": 1,
            "passed": 0,
            "rows": [{"scenario": "one.yaml", "outcome": "worker error", "reason": reason}],
        }
    )

    row = output["summary"].splitlines()[2]
    assert row.count(" | ") == 2
    assert "\x1b" not in row
    assert "``` a \\| b `code` ``x``[31mnext ```" in row


def test_the_summary_stops_before_githubs_cap_and_counts_what_it_left_out() -> None:
    rows = [
        {"scenario": f"s{i}.yaml", "outcome": "failed", "reason": "x" * 200} for i in range(1000)
    ]

    output = check_output({"conclusion": "failure", "total": 1000, "passed": 0, "rows": rows})

    lines = output["summary"].splitlines()
    shown = [line for line in lines if line.startswith("| `")]
    left_out = next(line for line in lines if line.startswith("| … |"))
    assert len(output["summary"]) <= SUMMARY_LIMIT
    assert left_out == f"| … | {1000 - len(shown)} more rows not shown | |"
    assert 0 < len(shown) < 1000
    assert output["title"] == "1000 of 1000 scenarios did not pass"
