"""Shared BE-item directory helpers: id-shape predicates + the item-tree walk (BE-0149, BE-0159).

The ``BE-NNNN-<slug>`` / ``BE-XXXX-<slug>`` id shape was independently hardcoded in four scripts and
the format test, and the copies had already drifted — only ``build_roadmap_index``'s ``ITEM_DIR_RE``
accepted the ``BE-XXXX`` placeholder. This is the single, stdlib-only home for that shape, so the
format check, the index build, id allocation, and promotion all agree, and a placeholder is checked
the same way a numbered item is (closing the gap that let a malformed placeholder reach ``main``).

BE-0159 is flattening every item out of the per-``Status`` folders into a single ``roadmaps/``
directory, migrated in two batches. During the migration the tree is **mixed** — some items already
flat, some still under a status folder — so :func:`iter_item_dirs` walks *both* the flat root and the
legacy folders, and callers read each item's actual location off ``dir.parent`` to render its link.
When the second batch lands and the folders are gone this collapses to a single flat scan.

Stdlib-only on purpose: the merge-time allocator (``roadmap-id.yml``) reaches the format check
through here and must not pull in third-party code alongside its bypass token (BE-0089).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

# The literal placeholder an unallocated item carries until CI numbers it on ``main`` (BE-0089).
PLACEHOLDER = "BE-XXXX"

# The per-``Status`` folders BE-0078 introduced, being retired by BE-0159. Kept only so the walk can
# still find items that have not been moved to the flat root yet; empties out when the migration
# completes and every item lives directly under ``roadmaps/``.
LEGACY_CATEGORIES = ("implemented", "in-progress", "proposals", "deferred")

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
    """Yield every BE item directory, from the flat root and the legacy status folders (BE-0159).

    During the two-batch flatten the tree is mixed, so this scans ``roadmaps/`` itself **and** each
    surviving ``roadmaps/<category>/`` folder, yielding both numbered and placeholder directories in a
    stable order (flat items first, then folder items, each group sorted by name). Callers narrow with
    :func:`numbered_match` / :func:`is_placeholder_dir` and read an item's current location off
    ``dir.parent`` (its name is one of :data:`LEGACY_CATEGORIES`, or ``roadmaps`` for a flattened
    item). Once the migration completes and the folders are gone, only the flat root is scanned.
    """
    for parent in (roadmap, *(roadmap / c for c in LEGACY_CATEGORIES)):
        if not parent.is_dir():
            continue
        for d in sorted(parent.iterdir()):
            if d.is_dir() and is_item_dir(d.name):
                yield d
