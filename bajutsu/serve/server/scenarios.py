"""A storage-backed ScenarioStore for the hosted backend (BE-0015 server phase).

`LocalScenarioStore` resolves an app to a scenarios dir on disk. `StorageScenarioStore` keeps the
same `ScenarioStore` seam but resolves a scenario **by name within a project, from per-project
storage** — never from a client-chosen filesystem path, which is the arbitrary-path-execution
guard (BE-0051) made structural: no path ever exists on the control plane.

It serves the authoring operations the UI needs — ``list`` / ``read`` / ``save`` — by delegating to
an injected `ScenarioStorage` (a DB / object store; real backing arrives with the persistence
slice). ``runnable`` returns the scenario as **materials** (the text plus a workspace-relative
path) so a remote worker writes it before running — no path ever exists on the control plane.
``out_path`` (record's authoring output) is still worker-side and not served yet.

This module imports no storage SDK — `ScenarioStorage` is injected — so it's unit-tested with a
fake and the default path stays server-free (#117 import guard).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from bajutsu.serve.helpers import valid_scenario_ref
from bajutsu.serve.scenarios import Runnable

# Where a materialized scenario lands in the worker's workspace (and the `--scenario` arg used).
_WORKSPACE_SCENARIOS = "scenarios"


class ScenarioStorage(Protocol):
    """Per-project scenario storage the control plane reads and writes (a DB / object store)."""

    def has_app(self, app: str) -> bool:
        """Whether *app* is a known project."""

    def list(self, app: str) -> list[dict[str, Any]]:
        """Every scenario in *app*, summarized for the UI."""

    def read(self, app: str, ref: str | None) -> str | None:
        """The YAML text of scenario *ref* in *app*, or None if absent."""

    def save(self, app: str, ref: str | None, text: str) -> str | None:
        """Persist *text* as scenario *ref* in *app*, returning the saved ref, or None if rejected."""


class StorageScenarioScope:
    """Authoring operations for one project's scenarios, backed by `ScenarioStorage`."""

    def __init__(self, storage: ScenarioStorage, app: str) -> None:
        self._storage = storage
        self._app = app

    def list(self) -> list[dict[str, Any]]:
        return self._storage.list(self._app)

    def read(self, ref: str | None) -> str | None:
        # A ref is a trust boundary (an object-store key / DB id) even with no filesystem here:
        # reject an obviously unsafe ref before it reaches the backing store.
        if not valid_scenario_ref(ref):
            return None
        return self._storage.read(self._app, ref)

    def save(self, ref: str | None, text: str) -> str | None:
        if not valid_scenario_ref(ref):
            return None
        return self._storage.save(self._app, ref, text)

    def runnable(self, scenario: str) -> Runnable | None:
        # Resolve by name from storage (no path on the control plane); ship the text as a material
        # the worker writes under its workspace, and point `--scenario` at that relative path.
        name = PurePosixPath(scenario).name  # honour only the basename
        if not valid_scenario_ref(name):
            return None
        text = self._storage.read(self._app, name)
        if text is None:
            return None
        rel = f"{_WORKSPACE_SCENARIOS}/{name}"
        return Runnable(arg=rel, materials={rel: text})

    def out_path(self, name: str) -> Path:
        raise NotImplementedError("record output is written on the worker (BE-0015)")


class StorageScenarioStore:
    """Resolves a project (app) to its storage-backed scenario scope (the ScenarioStore seam)."""

    def __init__(self, storage: ScenarioStorage) -> None:
        self._storage = storage

    def scope(self, app: str | None) -> StorageScenarioScope | None:
        if not app or not self._storage.has_app(app):
            return None
        return StorageScenarioScope(self._storage, app)
