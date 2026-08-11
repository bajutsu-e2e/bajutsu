"""Shared ``gh`` CLI subprocess wrapper (BE-0149).

Every roadmap script that talks to GitHub (``sync_roadmap_tracking_issues.py``,
``check_stale_roadmap_prs.py``) shelled out to ``gh`` via its own private helper; this is the one
place that invocation lives, so a fix (retry, error formatting) lands once instead of drifting
across copies — the same reasoning that justified ``scripts/roadmap_ids.py``.
"""

from __future__ import annotations

import subprocess


def run(args: list[str], *, capture: bool = False, stdin: str | None = None) -> str:
    """Run ``gh <args>``, raising on a non-zero exit; return stdout when ``capture`` is set.

    Args:
        stdin: Text piped to the process — how a caller hands ``gh api --input -`` a JSON body it
            cannot express as flat ``-f`` fields (a nested array, say).
    """
    result = subprocess.run(
        ["gh", *args], text=True, capture_output=capture, check=True, input=stdin
    )
    return result.stdout if capture else ""


def run_allow_failure(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``gh <args>`` without raising on failure — the caller inspects ``.returncode``."""
    return subprocess.run(["gh", *args], text=True, capture_output=True, check=False)
