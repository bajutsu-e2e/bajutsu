#!/usr/bin/env python3
"""Measure the over-fire cost of BE-0333's inverted E2E relevance default.

Inverting the default (an unclassified `bajutsu/` file fires every lane instead of none) trades an
unexercised required check for a wasted metered job, and the trade holds only while the waste stays
small (BE-0333 Unit 4). This samples a recent window of merged pull requests and, for each lane,
classifies every PR by what the *new* filter fires against what the *old* one fired:

  same        both filters agree (the common case — the change is squarely on or off the run path)
  over-fire   the new filter fires the lane and the old one did not (the cost this trade accepts)
  under-fire  the old filter fired and the new one does not (the saving; expected to be ~0, since
              inverting the default only ever *adds* coverage for unclassified files)

It runs entirely from `git`: the new `is_relevant` is imported from the working tree, the old one is
loaded from a chosen baseline commit, and each PR's own changed files come from the merge-base
(three-dot) diff of the merge commit's two parents — the same diff the `changes` job charges a PR for.

Usage:
    python3 scripts/e2e_overfire_report.py [--limit N] [--baseline REF]

`--baseline` defaults to the merge-base of `HEAD` and `origin/main`, i.e. the filter as it stood
before this branch. `--limit` is how many recent merge commits on the baseline's history to sample.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LANES = ("ios", "android", "web")

# Import the sibling filter as a top-level module (the scripts/ convention), so mypy sees one module
# name for it rather than both `e2e_changes` and `scripts.e2e_changes`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_changes import is_relevant as new_is_relevant  # noqa: E402


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _load_old_filter(baseline: str) -> ModuleType:
    """Import `scripts/e2e_changes.py` as it stood at `baseline`, under a throwaway module name."""
    source = _git("show", f"{baseline}:scripts/e2e_changes.py")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(source)
        temp_path = fh.name
    try:
        spec = importlib.util.spec_from_file_location("_e2e_changes_baseline", temp_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        Path(temp_path).unlink(missing_ok=True)
    return module


def _merge_commits(baseline: str, limit: int) -> list[str]:
    """Recent two-parent merge commits reachable from `baseline` (the PR merges into main)."""
    out = _git("rev-list", "--merges", "--min-parents=2", f"-n{limit}", baseline)
    return [line for line in out.splitlines() if line]


def _pr_changed_files(merge_sha: str) -> list[str]:
    """A merged PR's own changed files: the three-dot diff of the merge commit's two parents."""
    parents = _git("rev-list", "--parents", "-n1", merge_sha).split()[1:]
    if len(parents) != 2:
        return []
    base, head = parents  # first parent is main's tip, second is the merged branch
    out = _git("diff", "--name-only", f"{base}...{head}")
    return [line for line in out.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--baseline", default=None)
    args = parser.parse_args()

    baseline = args.baseline or _git("merge-base", "HEAD", "origin/main").strip()
    old = _load_old_filter(baseline)

    tallies = {lane: {"same": 0, "over-fire": 0, "under-fire": 0} for lane in _LANES}
    over_fire_examples: dict[str, list[str]] = {lane: [] for lane in _LANES}
    sampled = 0
    for merge_sha in _merge_commits(baseline, args.limit):
        files = _pr_changed_files(merge_sha)
        if not files:
            continue
        sampled += 1
        for lane in _LANES:
            new_fires = new_is_relevant(files, lane)
            old_fires = old.is_relevant(files, lane)
            if new_fires == old_fires:
                tallies[lane]["same"] += 1
            elif new_fires:
                tallies[lane]["over-fire"] += 1
                if len(over_fire_examples[lane]) < 5:
                    over_fire_examples[lane].append(merge_sha[:9])
            else:
                tallies[lane]["under-fire"] += 1

    print(f"Baseline: {baseline}")
    print(f"Sampled {sampled} merged PRs\n")
    print(f"{'lane':8} {'same':>6} {'over-fire':>10} {'under-fire':>11}")
    for lane in _LANES:
        t = tallies[lane]
        print(f"{lane:8} {t['same']:>6} {t['over-fire']:>10} {t['under-fire']:>11}")
    for lane in _LANES:
        if over_fire_examples[lane]:
            print(f"\n{lane} over-fire examples: {', '.join(over_fire_examples[lane])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
