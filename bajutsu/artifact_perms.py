"""Owner-only permissions for run artifacts (BE-0131).

A run's evidence — screenshots, ``network.json``, the copied scenario — can carry
sensitive data (request/response bodies, on-screen secrets). Created with the process
umask alone, they land world-readable on a typical ``0022`` host, so on a shared CI
runner another local account can read them. These helpers pin the run directory to
owner-only (``0700``) and the sensitive files to ``0600`` at creation time, ``chmod``-ing
after the write so the guarantee holds regardless of the ambient umask.
"""

from __future__ import annotations

import os
from pathlib import Path

# The run dir gates every artifact under it; sensitive files are additionally locked so the
# directory mode is not the only thing between a secret-bearing artifact and another local account.
RUN_DIR_MODE = 0o700
ARTIFACT_FILE_MODE = 0o600


def make_run_dir(path: Path) -> Path:
    """Create `path` (with parents) as an owner-only run directory.

    chmods after creating so the mode is the umask-independent `0700`, not `0700 & ~umask`.
    Idempotent: an already-existing dir (e.g. created world-readable by an earlier step-dir
    write) is re-restricted rather than left as-is.

    Raises:
        ValueError: `path` is a symlink. The run id is a predictable timestamp, so on a
            world-writable runs dir another local account could pre-plant a symlink there and
            redirect the `chmod` onto its target; refuse it loudly rather than follow it.
    """
    if path.is_symlink():
        raise ValueError(f"refusing to use a symlinked run directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, RUN_DIR_MODE)
    return path


def restrict_file(path: Path) -> Path:
    """Restrict an existing artifact file to owner-only (`0600`).

    A no-op when the file is absent: some drivers (the fake/headless path) record a screenshot
    without writing bytes, and there is nothing to protect then.
    """
    if path.exists():
        os.chmod(path, ARTIFACT_FILE_MODE)
    return path
