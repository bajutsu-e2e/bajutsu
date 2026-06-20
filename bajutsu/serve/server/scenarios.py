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

from collections.abc import Callable, Collection
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from bajutsu.serve.helpers import summarize_scenario, valid_scenario_ref
from bajutsu.serve.scenarios import Runnable
from bajutsu.serve.server.object_store import ObjectStore

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
        # Honour only the basename. Normalize backslashes first so "a\\b.yaml" reduces to "b.yaml"
        # too (PurePosixPath alone wouldn't split a backslash, leaking the prefix into the key).
        name = PurePosixPath(scenario.replace("\\", "/")).name
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


class ObjectScenarioStorage:
    """`ScenarioStorage` backed by S3-compatible object storage (the roadmap's R2).

    Scenarios live at ``<prefix>scenarios/<app>/<name>.yaml`` in one bucket; *prefix* is prepended
    so a tenant prefix (``<org>/``) can scope a shared bucket later — multi-tenant slots in without
    a contract change. The set of known projects comes from *apps* (the control plane's configured
    apps), keeping a Postgres registry out of the single-tenant path. The object-store client is
    injected (the `ObjectStore` slice), so a fake drives the gate."""

    def __init__(
        self, store: ObjectStore, apps: Callable[[], Collection[str]], *, prefix: str = ""
    ) -> None:
        self._store = store
        self._apps = apps
        self._prefix = prefix

    def _dir(self, app: str) -> str:
        return f"{self._prefix}{_WORKSPACE_SCENARIOS}/{app}/"

    def has_app(self, app: str) -> bool:
        return app in self._apps()

    def list(self, app: str) -> list[dict[str, Any]]:
        base = self._dir(app)
        out: list[dict[str, Any]] = []
        for key in sorted(self._store.list_keys(base)):
            name = key[len(base) :]
            # Only direct children that read/save would accept, so list never shows an entry that
            # can't then be read or run. valid_scenario_ref enforces a safe *.yaml ref.
            if "/" in name or not valid_scenario_ref(name):
                continue
            data = self._store.get_bytes(key)
            # Decode leniently: a non-UTF-8 object degrades to a bare entry, never 500s the listing.
            text = data.decode("utf-8", errors="replace") if data else ""
            out.append(summarize_scenario(name, name, text))
        return out

    def read(self, app: str, ref: str | None) -> str | None:
        if not ref:
            return None
        data = self._store.get_bytes(f"{self._dir(app)}{ref}")
        # Lenient decode: user-authored text shouldn't 500 the UI if it isn't valid UTF-8.
        return data.decode("utf-8", errors="replace") if data is not None else None

    def save(self, app: str, ref: str | None, text: str) -> str | None:
        if not ref:
            return None
        self._store.put_bytes(f"{self._dir(app)}{ref}", text.encode("utf-8"))
        return ref
