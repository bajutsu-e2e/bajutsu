"""The format-neutral run summary every notification channel renders from."""

from __future__ import annotations

from dataclasses import dataclass

from .failure_summary import FailureSummary


@dataclass
class RunNotification:
    """The format-neutral summary projected from run results."""

    run_id: str
    ok: bool
    total: int
    passed: int
    failed: int
    source_name: str
    backend: str
    duration_s: float
    failures: list[FailureSummary]
    failures_remaining: int
    report_url: str | None
    engine: str
    # Runs no available device fell in an open `runsOn` range for (BE-0452): neither passed nor
    # failed, so they sit outside `total`.
    not_applicable: int = 0
