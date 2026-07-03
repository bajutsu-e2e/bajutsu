"""Shared BE-item directory-name predicates (BE-0149).

The ``BE-NNNN-<slug>`` / ``BE-XXXX-<slug>`` id shape was independently hardcoded in four scripts and
the format test, and the copies had already drifted — only ``build_roadmap_index``'s ``ITEM_DIR_RE``
accepted the ``BE-XXXX`` placeholder. This is the single, stdlib-only home for that shape, so the
format check, the index build, id allocation, and promotion all agree, and a placeholder is checked
the same way a numbered item is (closing the gap that let a malformed placeholder reach ``main``).

Stdlib-only on purpose: the merge-time allocator (``roadmap-id.yml``) reaches the format check
through here and must not pull in third-party code alongside its bypass token (BE-0089).
"""

from __future__ import annotations

import re

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
