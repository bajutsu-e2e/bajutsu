#!/usr/bin/env python3
"""Per-file coverage floors that only ever rise (BE-0385).

The gate's total-coverage floor (``fail_under`` in ``pyproject.toml``) is a single number over the
whole package, so it hides where coverage is weak: a file can fall from 65% to 40% while the total
stays comfortably above the floor, because the rest of the codebase absorbs the loss. This script
closes that blind spot with a checked-in snapshot — ``coverage-floors.json`` — recording the branch
coverage each source file was last measured at, and a gate step that fails when a file drops below
its own recorded number.

Two modes, mirroring the ``format`` / ``format-check`` split so the gate can never quietly move the
bar it enforces:

Check mode (the default — no flag to pass; ``make check`` runs it as ``make lint-coverage-floors``)
    Compare ``coverage.json`` against the snapshot and fail on a drop. Never writes.

``--write`` (``make coverage-floors``)
    Rewrite the snapshot to what was just measured. A human runs this deliberately and commits the
    result — normally once coverage has risen, and occasionally to accept a drop they have decided
    to take. Drops are printed separately from rises so an accepted drop is visible before it is
    committed.

A rise never fails: blocking a contributor's PR for having *improved* coverage would punish exactly
the behaviour the ratchet exists to encourage. Only a drop blocks.

Deterministic and offline — plain arithmetic over two JSON files, with no model anywhere near the
verdict (prime directive 1).

Usage::

    python scripts/coverage_floors.py            # check against coverage-floors.json
    python scripts/coverage_floors.py --write    # rewrite the snapshot to today's measurement
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

# Below this many statements a single missed branch swings a file's percentage by several points,
# so a floor there would fail the gate on measurement noise rather than on a regression. It is also
# what keeps `bajutsu/__main__.py` — four never-imported lines sitting at 0% — out of the snapshot.
MIN_STATEMENTS = 10

DEFAULT_COVERAGE = Path("coverage.json")
DEFAULT_SNAPSHOT = Path("coverage-floors.json")


def truncate(percent: float) -> float:
    """The percentage a floor records: truncated to two decimals, never rounded up.

    Rounding could record a floor a hair above what was actually measured, which would fail the very
    run that produced it. Truncating keeps the recorded number at or below the measurement without
    handing the file a usable margin (BE-0385 rejects a slack margin outright).
    """
    return math.floor(percent * 100) / 100


def measured(data: dict[str, Any]) -> dict[str, float]:
    """Per-file coverage from a coverage.py JSON report, for the files a floor applies to."""
    return {
        name: truncate(float(info["summary"]["percent_covered"]))
        for name, info in sorted(data["files"].items())
        if int(info["summary"]["num_statements"]) >= MIN_STATEMENTS
    }


def compare(current: dict[str, float], floors: dict[str, float]) -> tuple[list[str], list[str]]:
    """Split a measurement against recorded floors into (drops, notes).

    A drop is the only failure. The notes cover the two shapes that must *not* fail: a file with no
    floor yet (new, or newly past `MIN_STATEMENTS`), and a recorded floor whose file is no longer
    measured (deleted, renamed, or shrunk below the threshold). Both are resolved by a deliberate
    ``--write``, never by the gate.
    """
    drops = [
        f"{name}: {current[name]:.2f}% is below its recorded floor of {floor:.2f}%"
        for name, floor in sorted(floors.items())
        if name in current and current[name] < floor
    ]
    notes = [f"{name}: no recorded floor yet" for name in current if name not in floors]
    notes += [
        f"{name}: recorded floor but no longer measured" for name in floors if name not in current
    ]
    return drops, sorted(notes)


def render(current: dict[str, float], floors: dict[str, float]) -> list[str]:
    """The rise/drop lines `--write` prints, so a human sees what they are about to commit."""
    lines = []
    for name, value in current.items():
        floor = floors.get(name)
        if floor is None:
            lines.append(f"  + {name}: recorded at {value:.2f}%")
        elif value > floor:
            lines.append(f"  ↑ {name}: {floor:.2f}% -> {value:.2f}%")
        elif value < floor:
            lines.append(f"  ↓ {name}: {floor:.2f}% -> {value:.2f}%  (accepted drop)")
    lines += [f"  - {name}: dropped (no longer measured)" for name in floors if name not in current]
    return lines


def load_snapshot(path: Path) -> dict[str, float]:
    """The recorded floors, or an empty mapping when the snapshot does not exist yet.

    Only ``--write`` may act on the empty mapping, seeding the snapshot from scratch. ``--check``
    refuses it (see `main`): a missing snapshot there would let every file through with no floor,
    turning a deleted file into a silently disabled gate.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {str(name): float(value) for name, value in data["files"].items()}


def save_snapshot(path: Path, current: dict[str, float]) -> None:
    """Write the snapshot: sorted keys and a trailing newline, so its diff reads one file per line."""
    body = {"min_statements": MIN_STATEMENTS, "files": current}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or rewrite the per-file coverage floors.")
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite the snapshot to the current measurement instead of checking against it",
    )
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.coverage.read_text())
        current = measured(data)
    except OSError:
        print(
            f"coverage-floors: no coverage report at {args.coverage} — run `make test` first.",
            file=sys.stderr,
        )
        return 1
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        print(
            f"coverage-floors: unreadable coverage report {args.coverage}: {exc}", file=sys.stderr
        )
        return 1

    if not args.write and not args.snapshot.exists():
        # Failing loudly beats passing 278 unchecked files: without the snapshot there is no floor
        # to enforce, and a green gate would report otherwise.
        print(
            f"coverage-floors: no snapshot at {args.snapshot} — the per-file floors are "
            "unenforceable. Restore the committed file, or seed one with `make coverage-floors`.",
            file=sys.stderr,
        )
        return 1

    try:
        floors = load_snapshot(args.snapshot)
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"coverage-floors: unreadable snapshot {args.snapshot}: {exc}", file=sys.stderr)
        return 1

    if not args.write and not floors:
        # An existing-but-empty snapshot disables the gate exactly as a deleted one does — a botched
        # `--write` against a truncated report, or a merge that resolves `files` to `{}`.
        print(
            f"coverage-floors: {args.snapshot} records no floors — the per-file floors are "
            "unenforceable. Restore the committed file, or seed one with `make coverage-floors`.",
            file=sys.stderr,
        )
        return 1

    if args.write:
        changes = render(current, floors)
        save_snapshot(args.snapshot, current)
        print(f"coverage-floors: wrote {len(current)} floor(s) to {args.snapshot}")
        for line in changes:
            print(line)
        if not changes:
            print("  (no change)")
        return 0

    drops, notes = compare(current, floors)
    if drops:
        print(
            "coverage-floors: per-file coverage dropped below its recorded floor:", file=sys.stderr
        )
        for drop in drops:
            print(f"  {drop}", file=sys.stderr)
        print(
            "  Add tests to restore it, or accept the drop deliberately with "
            "`make coverage-floors` and commit the rewritten snapshot.",
            file=sys.stderr,
        )
        return 1

    # Count the files a floor was actually applied to, not every file measured: a file with no
    # recorded floor was not checked, so counting it would overstate what the gate just enforced.
    checked = sum(1 for name in current if name in floors)
    print(f"coverage-floors: {checked} file(s) at or above their recorded floor.")
    for note in notes:
        print(f"  {note}")
    if notes:
        print("  Run `make coverage-floors` to record them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
