"""SHA-256 identity of an app binary: a single file, or a bundle directory hashed as a tree."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

from bajutsu.common.run_identity.gap import Gap, Reason


def _file_digest(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _raise(error: OSError) -> NoReturn:
    raise error


def _tree_entries(root: Path) -> Iterator[tuple[str, str, str]]:
    """Every regular file and symbolic link under *root*, as (relative path, kind, digest).

    A link contributes its target string and is never followed.
    """
    # `os.walk` swallows listing errors by default, which would hash a partial tree as the whole.
    for directory, dirs, files in os.walk(root, onerror=_raise):
        # A link to a directory is listed among `dirs`; `os.walk` does not descend into it.
        for name in dirs + files:
            path = Path(directory, name)
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                # Not `Path.readlink`, which normalizes the target string it returns.
                target = os.fsencode(os.readlink(path))  # noqa: PTH115
                yield relative, "link", hashlib.sha256(target).hexdigest()
            elif path.is_file():
                yield relative, "file", _file_digest(path)


def _tree_digest(entries: list[tuple[str, str, str]]) -> str:
    digest = hashlib.sha256()
    for relative, kind, entry in entries:
        digest.update(os.fsencode(relative) + f"\0{kind}\0{entry}\n".encode())
    return digest.hexdigest()


def hash_binary(path: Path) -> tuple[str, str] | Gap:
    """The binary's `sha256:<hex>` digest and its kind (`file` or `tree`)."""
    try:
        if path.is_dir():
            # An empty bundle (a build that stopped early) is no binary, not the hash of nothing.
            if not (entries := sorted(_tree_entries(path))):
                return Gap(Reason.PATH_MISSING)
            return f"sha256:{_tree_digest(entries)}", "tree"
        if path.is_file():
            return f"sha256:{_file_digest(path)}", "file"
    except OSError:
        return Gap(Reason.READ_ERROR)
    return Gap(Reason.PATH_MISSING)
