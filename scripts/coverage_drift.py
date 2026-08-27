#!/usr/bin/env python3
"""Notice when measured coverage has drifted well above the gate's floor (BE-0385).

The total-coverage floor (``fail_under`` in ``pyproject.toml``) only ever moves when someone
measures the suite and edits the number by hand, so it drifts below what the suite actually
delivers — and the gap between the two is room a regression can use unnoticed. This script closes
that loop by saying, after every measured run, how wide the gap has grown.

It is an **advisory**: it prints a reminder and returns 0. A hard failure here would block an
unrelated PR that happens to add well-tested code, purely for widening the gap further — punishing
work that is not itself the problem. That is why it runs from ``make lint-pr``, the target this
repository reserves for advisory checks, and not from ``make check``, whose every prerequisite fails
the build on a nonzero exit: a non-blocking step placed there would have to swallow its own exit
status, hiding this script's *real* failures (a crash, a bad parse) behind a green gate. A missing
``coverage.json`` is the one input problem that is not a failure — ``make lint-pr`` is run before
pushing, with or without a prior ``make test`` — so it prints a notice and still returns 0.

Deterministic and offline: two numbers read from two files, with no model anywhere near it.

Usage::

    python scripts/coverage_drift.py                    # read coverage.json + pyproject.toml
    python scripts/coverage_drift.py --coverage cov.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

# How far measured coverage may sit above the floor before the reminder fires. Small enough that
# the floor tracks real coverage, wide enough that ordinary run-to-run movement stays quiet.
DRIFT_POINTS = 2.0

DEFAULT_COVERAGE = Path("coverage.json")
DEFAULT_PYPROJECT = Path("pyproject.toml")


def read_floor(path: Path) -> float:
    """The gate's total-coverage floor, from ``[tool.coverage.report]``'s ``fail_under``.

    Deliberately strict: BE-0385 moved the floor here to give the gate and this advisory one
    declarative source, so a missing key means that source is gone and the advisory has nothing
    meaningful to compare against — a real failure, not something to guess a default for.
    """
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    return float(config["tool"]["coverage"]["report"]["fail_under"])


def message(percent: float, floor: float) -> str | None:
    """The reminder to print, or None while the gap is still narrow."""
    gap = percent - floor
    if gap <= DRIFT_POINTS:
        return None
    return (
        f"measured coverage is {percent:.2f}% against a floor of {floor:.2f}% "
        f"({gap:.2f} points of drift). Raise `fail_under` in pyproject.toml to close the gap."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report drift between measured coverage and the floor."
    )
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    args = parser.parse_args(argv)

    if not args.coverage.exists():
        print(
            f"coverage-drift: no coverage report at {args.coverage} — run `make test` to measure."
        )
        return 0

    try:
        percent = float(json.loads(args.coverage.read_text())["totals"]["percent_covered"])
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"coverage-drift: unreadable coverage report {args.coverage}: {exc}", file=sys.stderr)
        return 1

    try:
        floor = read_floor(args.pyproject)
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"coverage-drift: no usable floor in {args.pyproject}: {exc}", file=sys.stderr)
        return 1

    reminder = message(percent, floor)
    if reminder is None:
        print(
            f"coverage-drift: {percent:.2f}% against a floor of {floor:.2f}% — no drift to report."
        )
        return 0
    print(f"coverage-drift: reminder (advisory, not failing): {reminder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
