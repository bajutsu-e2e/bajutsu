"""Shared BE-item directory helpers: id-shape predicates + the flat-tree walk (BE-0149, BE-0159).

The ``BE-NNNN-<slug>`` / ``BE-XXXX-<slug>`` id shape was independently hardcoded in four scripts and
the format test, and the copies had already drifted — only ``build_roadmap_index``'s ``ITEM_DIR_RE``
accepted the ``BE-XXXX`` placeholder. This is the single, stdlib-only home for that shape, so the
format check, the index build, and id allocation all agree, and a placeholder is checked the same
way a numbered item is (closing the gap that let a malformed placeholder reach ``main``).

Since BE-0159 every item lives directly under ``roadmaps/`` (the status folders are retired), so the
"which directories are items" walk is a single flat scan — :func:`iter_item_dirs` — shared here too,
replacing the per-script four-folder ``CATEGORIES`` loops the flat layout collapsed.

Stdlib-only on purpose: the merge-time allocator (``roadmap-id.yml``) reaches the format check
through here and must not pull in third-party code alongside its bypass token (BE-0089).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

# The literal placeholder an unallocated item carries until CI numbers it on ``main`` (BE-0089).
PLACEHOLDER = "BE-XXXX"

# A numbered item directory ``BE-NNNN-<slug>``: group(1) is the 4-digit id, group(2) the slug.
_NUMBERED_DIR_RE = re.compile(r"^BE-(\d{4})-(.+)$")
# A placeholder directory ``BE-XXXX-<slug>``: group(1) is the slug.
_PLACEHOLDER_DIR_RE = re.compile(r"^BE-XXXX-(.+)$")


def numbered_match(name: str) -> re.Match[str] | None:
    """The match for a numbered ``BE-NNNN-<slug>`` directory name, else ``None``.

    group(1) is the 4-digit id, group(2) the slug — callers needing the parts read them off the
    returned match.
    """
    return _NUMBERED_DIR_RE.match(name)


def is_numbered_dir(name: str) -> bool:
    """Whether ``name`` is a numbered ``BE-NNNN-<slug>`` item directory."""
    return _NUMBERED_DIR_RE.match(name) is not None


def is_placeholder_dir(name: str) -> bool:
    """Whether ``name`` is an unallocated ``BE-XXXX-<slug>`` placeholder directory."""
    return _PLACEHOLDER_DIR_RE.match(name) is not None


def is_item_dir(name: str) -> bool:
    """Whether ``name`` is any BE item directory — numbered or still a placeholder."""
    return is_numbered_dir(name) or is_placeholder_dir(name)


def iter_item_dirs(roadmap: Path) -> Iterator[Path]:
    """Yield every BE item directory directly under ``roadmap``, sorted by name (BE-0159).

    Since the status folders were retired, an item lives at ``roadmaps/BE-NNNN-<slug>/`` — a single
    flat level — so this is one scan rather than the old walk over four category folders. Yields both
    numbered and placeholder directories (callers narrow with :func:`numbered_match` /
    :func:`is_placeholder_dir` as they need); non-item entries (the index pages, stray files) are
    skipped. Empty when ``roadmap`` is not a directory, so callers need no separate existence guard.
    """
    if not roadmap.is_dir():
        return
    for d in sorted(roadmap.iterdir()):
        if d.is_dir() and is_item_dir(d.name):
            yield d
