"""Git identity of a directory: which commit its working tree is at, and whether it has changes.

Deterministic, no-LLM subprocess reads (prime directive 1), used for the run's provenance stamp
(BE-0506) and for serve's version badge (BE-0272).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import bajutsu
from bajutsu.common.run_identity.gap import Gap, Reason, with_gaps

PACKAGE_DIR = Path(bajutsu.__file__).resolve().parent

# Git's own location env vars override `cwd` for repo discovery, so an ambient one (a git hook
# exports GIT_DIR / GIT_INDEX_FILE into its child environment) would silently redirect these reads
# away from the directory asked about. Strip them so discovery is purely cwd-based.
GIT_LOCATION_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_NAMESPACE",
)

_TIMEOUT = 5
# `status` walks the whole working tree, which takes longer than a ref read on a large app repo.
_STATUS_TIMEOUT = 30


def run_git(
    cwd: Path, *args: str, timeout: float = _TIMEOUT
) -> subprocess.CompletedProcess[str] | None:
    """Run a read-only `git` command in *cwd*, or None when git is missing or timed out.

    The directory can belong to someone else's config, so the read is kept from running that
    repository's `core.fsmonitor` command or hooks, and from writing its index. That does not make
    `status` safe there: see `_uncommitted`.
    """
    env = {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_ENV}
    try:
        return subprocess.run(
            [  # noqa: S607 — git resolved on PATH
                "git",
                "--no-optional-locks",
                *("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}"),
                *args,
            ],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def build_commit() -> str | None:
    """The commit embedded at image build time, for an install with no `.git` (BE-0277)."""
    return os.environ.get("BAJUTSU_BUILD_COMMIT", "").strip() or None


def _head(root: Path) -> str | Gap:
    out = run_git(root, "rev-parse", "HEAD")
    if out is None:
        return Gap(Reason.GIT_UNAVAILABLE)
    # A shimmed `git` could exit 0 with blank stdout; that is no commit either.
    commit = out.stdout.strip() if out.returncode == 0 else ""
    return commit or Gap(Reason.NOT_A_GIT_REPO)


def _uncommitted(root: Path, *, trusted: bool) -> bool | Gap:
    # `status` runs the filter commands the repository, or the operator's global config over its
    # files, names. For a tree that came with an uploaded or fetched config that would run outside
    # the worker's command policy (BE-0492), and no list of keys to turn off is complete.
    if not trusted:
        return Gap(Reason.STATUS_READ_FAILED)
    # Spelled out so a `status.showUntrackedFiles` or `submodule.<name>.ignore` setting cannot make
    # a changed tree read as clean.
    out = run_git(
        root,
        "status",
        "--porcelain",
        "--untracked-files=normal",
        "--ignore-submodules=none",
        timeout=_STATUS_TIMEOUT,
    )
    if out is None:
        return Gap(Reason.GIT_UNAVAILABLE)
    if out.returncode != 0:
        return Gap(Reason.STATUS_READ_FAILED)
    return bool(out.stdout.strip())


def _tracked_head(root: Path) -> str | Gap:
    """The commit of the repository that tracks *root*'s files.

    `git` walks up from any directory, so an ignored or unpacked copy inside another repository
    would otherwise be reported as that repository's clean commit.
    """
    tracked = run_git(root, "ls-files", "-z", "--", ".")
    if tracked is None:
        return Gap(Reason.GIT_UNAVAILABLE)
    if tracked.returncode != 0 or not tracked.stdout:
        return Gap(Reason.NOT_A_GIT_REPO)
    return _head(root)


def source_identity(root: Path, *, trusted: bool) -> dict[str, object]:
    """The `appSource` stamp for the source repository at *root*.

    The commit describes the working tree, not the built binary; the two are recorded separately.

    Args:
        root: The configured source repository.
        trusted: The operator chose the config naming it. Otherwise `status` is not run, since it
            can execute commands the tree configures.
    """
    # `os.path.isdir` answers False where `Path.is_dir` would raise on an unreadable parent. Checked
    # first: a missing `cwd` raises OSError, which would read as git being unavailable.
    if not os.path.isdir(root):  # noqa: PTH112
        gap = Gap(Reason.NOT_A_GIT_REPO)
        return with_gaps({"commit": gap, "uncommitted": gap})
    commit = _tracked_head(root)
    uncommitted = commit if isinstance(commit, Gap) else _uncommitted(root, trusted=trusted)
    return with_gaps({"commit": commit, "uncommitted": uncommitted})


def tool_source(package_dir: Path = PACKAGE_DIR) -> dict[str, object] | None:
    """The `toolSource` stamp for Bajutsu's own code, or None when nothing identifies it.

    A checkout counts only when it tracks the package itself. Without that test, `git` walks up
    from an installed copy and reports the surrounding repository (an app's tree holding a
    virtualenv, for one) as Bajutsu's.
    """
    tracked = run_git(package_dir, "ls-files", "--error-unmatch", "__init__.py")
    if tracked is not None and tracked.returncode == 0:
        commit = _head(package_dir)
        if not isinstance(commit, Gap):
            return with_gaps(
                {
                    "commit": commit,
                    "uncommitted": _uncommitted(package_dir, trusted=True),
                    "source": "checkout",
                }
            )
    embedded = build_commit()
    return {"commit": embedded, "source": "build-arg"} if embedded else None
