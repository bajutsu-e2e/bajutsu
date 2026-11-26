"""A value the runner tried to read and could not, with the reason as a code from a fixed set.

A code rather than the error message, because a raw message can carry a local path or a token
that the run directory does not otherwise expose (BE-0506).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class Reason(StrEnum):
    """Why a stamp value is a gap."""

    PATH_MISSING = "path-missing"
    READ_ERROR = "read-error"
    PLIST_UNREADABLE = "plist-unreadable"
    APK_UNREADABLE = "apk-unreadable"
    MANIFEST_UNPARSABLE = "manifest-unparsable"
    NOT_A_GIT_REPO = "not-a-git-repo"
    GIT_UNAVAILABLE = "git-unavailable"
    STATUS_READ_FAILED = "status-read-failed"


@dataclass(frozen=True)
class Gap:
    """The result of a read that did not yield a value."""

    reason: Reason


def with_gaps(values: Mapping[str, object]) -> dict[str, object]:
    """Render read results as stamp keys: each gap stays as null, named under `unavailable`."""
    out: dict[str, object] = {k: None if isinstance(v, Gap) else v for k, v in values.items()}
    gaps = {k: v.reason.value for k, v in values.items() if isinstance(v, Gap)}
    if gaps:
        out["unavailable"] = gaps
    return out
