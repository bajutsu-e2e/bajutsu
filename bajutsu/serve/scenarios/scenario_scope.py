"""The seam scenario operations are confined to one app's scenarios through."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .authored import Authored
from .runnable import Runnable


class ScenarioScope(Protocol):
    """Scenario operations confined to one app's scenarios."""

    def list(self) -> list[dict[str, Any]]:
        """Every scenario, summarized for the UI."""

    def directory(self) -> Path | None:
        """The on-disk dir a scenario's file refs resolve in, or None for a store with no files."""

    def runnable(self, scenario: str) -> Runnable | None:
        """Resolve *scenario* (by basename) to a `Runnable` — the trusted ``--scenario`` arg plus
        any materials a remote worker must write first — or None if no such scenario. Never lets a
        client string reach a host path: the local store matches the dir listing; the server store
        reads from per-project storage and ships the text as materials (BE-0051 / BE-0015)."""

    def read(self, ref: str | None) -> str | None:
        """The YAML text of the scenario at *ref*, or None if it's missing or escapes the dir."""

    def save(self, ref: str | None, text: str) -> str | None:
        """Save *text* as the scenario at *ref* (it need not exist yet), returning a reference to
        the saved scenario, or None if *ref* would escape the scope or isn't a scenario file. The
        scope owns the write, so a server scope persists to storage instead of the filesystem."""

    def authored(self, name: str) -> Authored:
        """Where a `record` run for *name* should write its authored scenario — the ``--out`` value
        and, on the server, the ``(app, ref)`` a worker persists it to afterward. The local scope
        returns a fresh, unique on-disk path (no save); the server scope returns a workspace-relative
        path plus the storage destination (BE-0015)."""
