"""GitHub Actions integration for `bajutsu run`.

Active only inside Actions (`GITHUB_ACTIONS` set). Emits an `::error::` workflow command
for each failed scenario (shown inline on the PR / in the run log) and appends a PASS/FAIL
table to the job summary (`$GITHUB_STEP_SUMMARY`). A no-op everywhere else.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

from bajutsu.orchestrator import RunResult


def _one_line(text: str) -> str:
    """Collapse whitespace — an annotation message must be a single line."""
    return " ".join(text.split())


def emit(
    results: list[RunResult],
    report: Path,
    env: Mapping[str, str] | None = None,
    echo: Callable[[str], None] = print,
) -> bool:
    """Emit annotations + a job summary when running in GitHub Actions.

    Returns whether anything was emitted (False outside Actions, so callers can stay quiet
    locally)."""
    env = os.environ if env is None else env
    if not env.get("GITHUB_ACTIONS"):
        return False
    total = len(results)
    passed = sum(1 for r in results if r.ok)
    for r in results:
        if not r.ok:
            echo(f"::error title=bajutsu: {r.scenario}::{_one_line(r.failure or 'failed')}")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        verdict = "PASS" if passed == total else "FAIL"
        rows = [
            f"## bajutsu — {verdict} ({passed}/{total})",
            "",
            "| | scenario | reason |",
            "|---|---|---|",
        ]
        for r in results:
            reason = "" if r.ok else _one_line(r.failure or "").replace("|", "\\|")
            rows.append(f"| {'✅' if r.ok else '❌'} | {r.scenario} | {reason} |")
        rows += ["", f"Report: `{report}`"]
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n")
    return True
