"""Shared ``gh`` CLI subprocess wrapper (BE-0149).

Every roadmap script that talks to GitHub (``sync_roadmap_tracking_issues.py``,
``check_stale_roadmap_prs.py``) shelled out to ``gh`` via its own private helper; this is the one
place that invocation lives, so a fix (retry, error formatting) lands once instead of drifting
across copies — the same reasoning that justified ``scripts/roadmap_ids.py``.
"""

from __future__ import annotations

import subprocess


def run(args: list[str], *, capture: bool = False) -> str:
    """Run ``gh <args>``, raising on a non-zero exit; return stdout when ``capture`` is set."""
    result = subprocess.run(["gh", *args], text=True, capture_output=capture, check=True)
    return result.stdout if capture else ""


def run_allow_failure(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``gh <args>`` without raising on failure — the caller inspects ``.returncode``."""
    return subprocess.run(["gh", *args], text=True, capture_output=True, check=False)
