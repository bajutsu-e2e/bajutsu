#!/usr/bin/env python3
"""Guard the merge-time renumber commit before it lands on `main` (BE-0089, BE-0149).

The `roadmap-id` workflow allocates BE IDs on `main` after a merge, pushing the rename with a token
that can bypass `main`'s branch protection. That commit lands with no PR and no required status
check standing in its way, so this guard is the only gate on it, and it checks two things:

- **Blast radius (BE-0089).** The legitimate commit is always the same narrow mechanical shape (a
  `BE-XXXX` → `BE-NNNN` rename, all under `roadmaps/`); the guard fails if it touches anything
  outside `roadmaps/`, capping any misuse of the high-value token.
- **Format conformance (BE-0149).** Renumbering is the first moment a placeholder's shape becomes
  checkable, and it happens on this ungated path — so the guard also runs the stdlib format check
  over the renumbered tree. A placeholder that drifted out of shape while it sat in review (as
  BE-0137/BE-0138 did) is caught here and the push is aborted, rather than landing red on `main`.
  Kept stdlib-only (`check_roadmap_format`, no `pytest`) so this privileged, token-holding job pulls
  in no third-party code (BE-0089).

Run it on the **committed** renumber, after `git commit` and before `git push`, so the property is
tied to the artifact that actually lands on `main`, not to a transient index state::

    git add -A && git commit -m … && python3 scripts/check_renumber_diff.py && git push …
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

_ROADMAPS = "roadmaps/"


def disallowed_paths(paths: Iterable[str]) -> list[str]:
    """The changed paths that fall outside `roadmaps/`, preserving input order.

    A path counts as inside the tree only with the trailing slash, so a sibling like
    `roadmaps-notes.md` is correctly flagged rather than matched as a prefix.
    """
    return [p for p in paths if not p.startswith(_ROADMAPS)]


def _committed_paths() -> list[str]:
    # The renumber is a single commit on top of `main`, so HEAD~1..HEAD is exactly its diff — the
    # tree that will be pushed. Checking the commit (not the index) keeps the guard tied to the
    # artifact even if a future step writes between staging and committing.
    out = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"], capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def _format_problems(roadmap: Path | None = None) -> list[str]:
    """Format violations across the renumbered tree, via the shared stdlib checker (BE-0149).

    ``roadmap`` defaults to the real ``roadmaps/`` tree; a test passes a throwaway one to pin the
    failure path without touching the committed tree. Imported lazily so the module-level surface
    this file exposes to its test stays just ``disallowed_paths``, and so the sibling import resolves
    whether the file is run as a script or loaded under its bare name.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_roadmap_format import ROADMAP, all_problems

    return all_problems(roadmap if roadmap is not None else ROADMAP)


def main() -> int:
    bad = disallowed_paths(_committed_paths())
    if bad:
        print(
            "check-renumber-diff: the renumber commit must touch only roadmaps/, but these are "
            "outside it:",
            file=sys.stderr,
        )
        for p in bad:
            print(f"  {p}", file=sys.stderr)
        return 1

    if problems := _format_problems():
        print(
            "check-renumber-diff: the renumbered tree fails the roadmap format check — refusing to "
            "push it to main:",
            file=sys.stderr,
        )
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    print(
        "check-renumber-diff: the renumber commit is confined to roadmaps/ and passes the format "
        "check."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
