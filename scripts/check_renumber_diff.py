#!/usr/bin/env python3
"""Guard the merge-time renumber commit's blast radius (BE-0089).

The `roadmap-id` workflow allocates BE IDs on `main` after a merge, pushing the rename with a token
that can bypass `main`'s branch protection. That token is a high-value credential, so this guard
keeps its power structurally small: the legitimate commit is always the same narrow mechanical shape
(a `BE-XXXX` → `BE-NNNN` rename plus the regenerated index, all under `roadmaps/`), and the guard
fails the job if the staged commit touches anything outside `roadmaps/` — capping any misuse to that
tree before the push happens.

Run it after staging the renumber but before committing::

    git add -A && python3 scripts/check_renumber_diff.py
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable

_ROADMAPS = "roadmaps/"


def disallowed_paths(paths: Iterable[str]) -> list[str]:
    """The changed paths that fall outside `roadmaps/`, preserving input order.

    A path counts as inside the tree only with the trailing slash, so a sibling like
    `roadmaps-notes.md` is correctly flagged rather than matched as a prefix.
    """
    return [p for p in paths if not p.startswith(_ROADMAPS)]


def _staged_paths() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def main() -> int:
    bad = disallowed_paths(_staged_paths())
    if bad:
        print(
            "check-renumber-diff: the renumber commit must touch only roadmaps/, but these are "
            "outside it:",
            file=sys.stderr,
        )
        for p in bad:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("check-renumber-diff: staged changes are confined to roadmaps/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
