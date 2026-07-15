"""Serve endpoints reporting bajutsu's own version and Git checkout identity (BE-0272)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from bajutsu import __version__

# Anchor the Git reads at bajutsu's own package directory, not the process CWD. A bound
# `github:` config repoints serve's CWD at the config's checkout cache, so reading Git there
# would report the *config's* commit — the "which build of the tool" vs "where the config came
# from" conflation this badge exists to dispel. The package dir is always the tool's own source,
# so `git` walks up to the checkout serving the page (or finds none, for a pip-installed copy).
_REPO_ANCHOR = Path(__file__).resolve().parent

# Git's own location env vars override `cwd` for repo discovery, so an ambient one (a git hook
# exports GIT_DIR / GIT_INDEX_FILE into its child environment) would silently redirect these reads
# away from `_REPO_ANCHOR`. Strip them so discovery is purely cwd-based and the anchor always wins.
_GIT_LOCATION_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_NAMESPACE",
)


def _git(*args: str) -> str | None:
    """Run a read-only `git` command anchored at bajutsu's source, or None on any failure.

    A missing `git`, a non-checkout install, or a non-zero exit all collapse to None so the
    caller simply omits the field. A deterministic, no-LLM subprocess read (prime directive 1).
    """
    env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_ENV}
    try:
        out = subprocess.run(
            ["git", *args],  # noqa: S607 — git resolved on PATH; any failure → None below
            cwd=_REPO_ANCHOR,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def server_version() -> tuple[Any, int]:
    """The running server's own version string — always available, never sensitive (BE-0272)."""
    return {"version": __version__}, 200


def server_checkout() -> tuple[Any, int]:
    """bajutsu's Git checkout identity: short commit SHA, branch name, and dirty flag (BE-0272).

    Read fresh per request so a serve left running while its checkout is edited stays accurate.
    Every field is null/False when serve runs outside a Git checkout (e.g. a pip-installed copy).
    admin-gated by the caller (`authz`): a branch name routinely encodes an in-progress BE slug.
    """
    commit = _git("rev-parse", "--short", "HEAD")
    if commit is None:  # not a Git checkout — nothing to report
        return {"commit": None, "branch": None, "dirty": False}, 200
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":  # detached HEAD — "HEAD" is not a branch name, so report none
        branch = None
    dirty = bool(_git("status", "--porcelain"))
    return {"commit": commit, "branch": branch, "dirty": dirty}, 200
