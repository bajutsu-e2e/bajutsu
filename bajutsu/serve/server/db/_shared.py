"""The lease timeouts and reclaim limits the repository enforces (BE-0016)."""

from __future__ import annotations

from typing import Any

DEFAULT_LEASE_MAX_ATTEMPTS = 3

# The job statuses no transition leaves (BE-0449 added ``cancelled``). One tuple, so every reader that
# asks "has this job finished" — the cap-release probe, the post-completion log bus — agrees.
TERMINAL_JOB_STATUSES = ("done", "failed", "cancelled")


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
