#!/usr/bin/env python3
"""Render a coverage.json report as a GitHub Actions job summary.

``make test`` writes ``coverage.json`` (coverage.py's JSON report) next to the term-missing
output. CI feeds that file to this script, which emits a Markdown table — overall coverage
versus the floor, plus the files that fall short — into ``$GITHUB_STEP_SUMMARY`` so the result
is visible in the run UI without any external service, token, or PR-write permission.

This is a *presentation* layer only: pass/fail is still decided by ``--cov-fail-under`` in the
pytest step. The script never fails a run (it is meant to run with ``if: always()`` so the
report shows even when the floor is breached); a missing or malformed ``coverage.json`` only
prints a notice.

Usage::

    python scripts/coverage_summary.py                     # read coverage.json, floor 85
    python scripts/coverage_summary.py --floor 90 cov.json  # custom floor / path
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Show at most this many under-floor files; the rest collapse into a "+N more" line so the
# summary stays scannable on a large regression.
MAX_FILES = 30


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    except (OSError, ValueError):
        return None


def _bar(percent: float) -> str:
    """A 20-cell text meter — readable in the plain-text fallback and in Markdown alike."""
    filled = round(percent / 5)
    return "█" * filled + "░" * (20 - filled)


def render(data: dict[str, Any], floor: float) -> str:
    totals = data["totals"]
    percent = float(totals["percent_covered"])
    covered = int(totals["covered_lines"])
    statements = int(totals["num_statements"])
    passed = percent >= floor

    lines = [
        "## Coverage",
        "",
        f"{'✅' if passed else '❌'} **{percent:.2f}%** "
        f"({covered}/{statements} statements) — floor {floor:.0f}%",
        "",
        f"`{_bar(percent)}`",
        "",
    ]

    # Files below 100%, worst first — the actionable list. Fully-covered files are omitted
    # (the same intent as pytest's ``skip-covered``).
    files = data.get("files", {})
    short = [
        (name, info["summary"])
        for name, info in files.items()
        if float(info["summary"]["percent_covered"]) < 100.0
    ]
    short.sort(key=lambda item: float(item[1]["percent_covered"]))

    if short:
        lines += [
            "<details>",
            f"<summary>{len(short)} file(s) below 100%</summary>",
            "",
            "| File | Stmts | Miss | Cover |",
            "| --- | ---: | ---: | ---: |",
        ]
        for name, summary in short[:MAX_FILES]:
            lines.append(
                f"| `{name}` | {summary['num_statements']} | "
                f"{summary['missing_lines']} | {float(summary['percent_covered']):.1f}% |"
            )
        if len(short) > MAX_FILES:
            lines.append(f"| _…+{len(short) - MAX_FILES} more_ | | | |")
        lines += ["", "</details>", ""]
    else:
        lines += ["Every measured file is fully covered. 🎉", ""]

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="coverage.json", type=Path)
    parser.add_argument("--floor", type=float, default=85.0)
    args = parser.parse_args()

    data = _load(args.path)
    if data is None:
        # Never fail the run over the report itself; just leave a trace.
        sys.stderr.write(f"coverage_summary: no readable coverage data at {args.path}\n")
        return 0

    summary = render(data, args.floor)

    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with Path(out).open("a") as fh:
            fh.write(summary + "\n")
    else:
        sys.stdout.write(summary + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
