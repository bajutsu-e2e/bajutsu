"""The `completed` snapshot a group's check output renders, built once per event (BE-0500)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bajutsu.common.report.load import results_from_manifest
from bajutsu.serve.server.db import LEASE_EXPIRED, ended_cancelled, group_conclusion, job_failed

# What a bare `run` exit code means, beyond pass (0) and fail (1).
_USAGE_ERROR = 2
_SIGNAL_BASE = 128


def group_snapshot(
    rows: list[dict[str, Any]], read_manifest: Callable[[str], dict[str, Any] | None]
) -> dict[str, Any]:
    """The conclusion, the counts, and one row per scenario that did not pass, from job *rows*.

    Args:
        rows: The group's jobs as `Repository.group_jobs` returns them.
        read_manifest: A run's parsed `manifest.json` by run id, or None when it is unavailable.

    Returns:
        ``conclusion``, ``total``, ``passed``, and ``rows`` of ``scenario``, ``outcome``, and
        ``reason``. Every reason is the raw text a worker or a manifest reported; rendering it is the
        adapter's to make safe.
    """
    listed: list[dict[str, str]] = []
    for row in rows:
        outcome = _outcome(row, read_manifest)
        if outcome is not None:
            listed.append({"scenario": _scenario(row), "outcome": outcome[0], "reason": outcome[1]})
    return {
        "conclusion": group_conclusion(rows),
        "total": len(rows),
        "passed": len(rows) - len(listed),
        "rows": listed,
    }


def _outcome(
    row: dict[str, Any], read_manifest: Callable[[str], dict[str, Any] | None]
) -> tuple[str, str] | None:
    result: dict[str, Any] = row["result"] or {}
    if ended_cancelled(row):
        return ("skipped" if row.get("cancel_reason") == "failFast" else "cancelled"), ""
    if not job_failed(row["status"], result):
        return None
    if row["status"] == "failed":
        if result.get("reason") == LEASE_EXPIRED:
            return "worker lost", f"lease expired after {result.get('attempts')} attempts"
        return "worker error", _first_line(str(result.get("error") or "no error reported"))
    exit_code = result.get("exitCode")
    if exit_code == 1:
        return "failed", _failure_line(result.get("runId"), read_manifest)
    return "error", _exit_meaning(exit_code)


def _failure_line(run_id: object, read_manifest: Callable[[str], dict[str, Any] | None]) -> str:
    manifest = read_manifest(run_id) if isinstance(run_id, str) and run_id else None
    if manifest is not None:
        for result in results_from_manifest(manifest):
            if result.failure:
                return _first_line(result.failure)
    return "exit code 1: no failure detail recorded"


def _exit_meaning(code: object) -> str:
    if not isinstance(code, int):
        return "no exit code reported"
    if code == _USAGE_ERROR:
        return f"exit code {code} (usage error)"
    if code > _SIGNAL_BASE:
        return f"exit code {code} (signal {code - _SIGNAL_BASE})"
    if code < 0:
        return f"exit code {code} (signal {-code})"
    return f"exit code {code}"


def _scenario(row: dict[str, Any]) -> str:
    intent = (row.get("spec") or {}).get("intent") or {}
    scenario = intent.get("scenario")
    return scenario if isinstance(scenario, str) and scenario else f"job {row['id']}"


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text
