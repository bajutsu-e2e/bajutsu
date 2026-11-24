"""The lease timeouts and reclaim limits the repository enforces (BE-0016)."""

from __future__ import annotations

from typing import Any

DEFAULT_LEASE_MAX_ATTEMPTS = 3

# The structured reason a job failed by lease expiry carries in its result (BE-0500).
LEASE_EXPIRED = "lease-expired"

# The job statuses no transition leaves (BE-0449 added ``cancelled``). One tuple, so every reader that
# asks "has this job finished" — the cap-release probe, the post-completion log bus — agrees.
TERMINAL_JOB_STATUSES = ("done", "failed", "cancelled")


def job_failed(status: str, result: dict[str, Any]) -> bool:
    """Whether a job that ended as *status* with *result* failed (BE-0499).

    One rule for the group's `conclusion` and its fail-fast stop: a job not cancelled fails unless
    it ended ``done`` with exit code 0, so a result that reports no exit code is a failure.
    """
    if status == "cancelled" or result.get("cancelled"):
        return False
    return status != "done" or result.get("exitCode") != 0


def ended_cancelled(row: dict[str, Any]) -> bool:
    """Whether a group job's *row* ended cancelled, by its status or by its worker's result."""
    return row["status"] == "cancelled" or bool(row["result"].get("cancelled"))


def group_conclusion(rows: list[dict[str, Any]]) -> str:
    """A group's outcome from its job *rows*, named after a GitHub check run's conclusion values."""
    # Anything not terminal counts as unfinished, so a status added later never reads as a failure.
    if any(row["status"] not in TERMINAL_JOB_STATUSES for row in rows):
        return "pending"
    if any(job_failed(row["status"], row["result"]) for row in rows):
        return "failure"
    return "cancelled" if any(ended_cancelled(row) for row in rows) else "success"


def cancelled_job_result(job_id: str) -> dict[str, Any]:
    """The terminal result of a cancelled job whose own run reported none (BE-0449).

    Shaped like `Job.view` without its lines, which is what a worker posts, so the job view and the
    post-completion log bus read a cancel the server recorded the same way as one a worker reported.
    """
    return {
        "id": job_id,
        "status": "done",
        "exitCode": None,
        "runId": None,
        "outPath": None,
        "cancelled": True,
        "ok": False,
        "awaitingHuman": False,
    }


# The default newest-N window `list_runs` caps at (`None` means unbounded). Shared so the serve
# read path that re-caps a scoped list post-filter (`operations.reads`) stays in lock-step: a future
# bump here must not silently leave that path capping at a stale number.
DEFAULT_RUN_LIMIT = 50
