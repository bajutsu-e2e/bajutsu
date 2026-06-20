"""The BaselineStore seam: how visual-regression baselines are read and written (BE-0015).

A `visual` assertion compares a run's captured screenshot against a stored baseline image. Approve
promotes a screenshot to a baseline; a run reads baselines to compare against. This is the one
point where that storage diverges between local and server hosting: locally baselines are files
**confined to the baselines dir** (`LocalBaselineStore`), while a server store keeps them in object
storage. Baselines are a flat name space (the same single dir `serve` uses today); per-tenant
scoping comes from the object-store prefix.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Protocol


def _safe_baseline_name(name: str) -> bool:
    """Whether *name* is an obviously safe baseline name: non-empty, NUL-free, relative, no ``..``
    traversal. The store still does full containment on top; this is the shared first guard."""
    if not name or "\x00" in name:
        return False
    pure = PurePosixPath(name.replace("\\", "/"))
    return not pure.is_absolute() and ".." not in pure.parts


class BaselineStore(Protocol):
    """Reads and writes visual-regression baseline images."""

    def open_bytes(self, name: str) -> bytes | None:
        """The baseline image bytes for *name*, or None if absent (or *name* escapes the store)."""

    def write(self, name: str, data: bytes) -> str | None:
        """Persist *data* as baseline *name*, returning the saved name, or None if *name* is unsafe."""

    def names(self) -> list[str]:
        """Every stored baseline name (so a run host can materialize them all)."""


class LocalBaselineStore:
    """Baselines confined to a single on-disk dir — the default for `serve`."""

    def __init__(self, baselines_dir: Path) -> None:
        self._dir = baselines_dir

    def _target(self, name: str) -> Path | None:
        if not _safe_baseline_name(name):
            return None
        target = (self._dir / name).resolve()
        base = self._dir.resolve()
        return target if (target == base or base in target.parents) else None

    def open_bytes(self, name: str) -> bytes | None:
        target = self._target(name)
        return target.read_bytes() if target is not None and target.is_file() else None

    def write(self, name: str, data: bytes) -> str | None:
        target = self._target(name)
        if target is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return name

    def names(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return [
            p.relative_to(self._dir).as_posix() for p in sorted(self._dir.rglob("*")) if p.is_file()
        ]
