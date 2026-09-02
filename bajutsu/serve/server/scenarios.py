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
fake and the default path stays server-free (the import guard).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Protocol

from bajutsu.serve.helpers import scenario_out_name, summarize_scenario, valid_scenario_ref
from bajutsu.serve.scenarios import Authored, LocalScenarioStore, Runnable
from bajutsu.serve.server.object_store import ObjectStore, scenario_prefix
from bajutsu.serve.state import ServeState, _scenarios_dir_for

_logger = logging.getLogger(__name__)

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

    def authored(self, name: str) -> Authored:
        # No filesystem here: pick a safe ref, stamp it if taken (don't clobber), and tell the
        # worker to write to a workspace-relative path then persist it to storage as (app, ref).
        ref = scenario_out_name(name)
        if self._storage.read(self._app, ref) is not None:
            # Microsecond precision so two records in the same second don't pick the same ref.
            stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S-%f")
            ref = f"{ref[: -len('.yaml')]}-{stamp}.yaml"
        return Authored(out=f"{_WORKSPACE_SCENARIOS}/{ref}", save=(self._app, ref))


class StorageScenarioStore:
    """Resolves a project (app) to its storage-backed scenario scope (the ScenarioStore seam)."""

    def __init__(self, storage: ScenarioStorage) -> None:
        self._storage = storage

    def scope(
        self,
        app: str | None,
        *,
        session: str | None = None,  # noqa: ARG002  # ScenarioStore shape
        org: str = "",  # noqa: ARG002  # ScenarioStore shape
    ) -> StorageScenarioScope | None:
        # Storage-backed scenarios are addressed by app name within the org's own prefix, never by a
        # path resolved from the bound configuration, so the requesting session decides nothing here.
        # The parameters exist to satisfy the seam its local sibling needs (BE-0393 unit 2).
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
        return f"{scenario_prefix(self._prefix)}{app}/"

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


class LocalTreeScenarioStorage:
    """`ScenarioStorage` that reads from the bound config's already-extracted local-tree dir
    (BE-0324), instead of the object-storage bucket `ObjectScenarioStorage` reads.

    A Git checkout, an uploaded zip, and a composed-artifact bind (BE-0063/BE-0073/BE-0268) all
    extract their scenario tree onto the control plane's own disk before serving a single request —
    the same tree `_scenarios_dir_for` (`bajutsu.serve.state`) already resolves for the local
    backend. `list` and `read` reuse `LocalScenarioStore` (`bajutsu.serve.scenarios`) — the same
    resolver the local backend wires onto `ServeState` — rather than re-deriving dir resolution and
    reconstructing its scope, so the BE-0051 path-containment guard stays in the one place that
    already implements and tests it. `has_app` delegates to `save_storage`, which already answers it
    from the same *apps* lookup. `save` has no local-tree counterpart yet (a follow-up question, see
    BE-0324's Alternatives), so it delegates to an injected write-side `ScenarioStorage` — the sole
    reason this class holds one (in practice an `ObjectScenarioStorage`, but nothing here depends on
    more than the `ScenarioStorage` protocol it satisfies)."""

    def __init__(self, state: ServeState, save_storage: ScenarioStorage) -> None:
        # The read side resolves against the configuration the *requesting session* is bound to
        # (BE-0393 unit 2); the local store's `scope` hands the resolver that session per call.
        self._local = LocalScenarioStore(
            lambda app, session, org: _scenarios_dir_for(state, app, session, org)
        )
        self._save_storage = save_storage

    def has_app(self, app: str) -> bool:
        return self._save_storage.has_app(app)

    def list(self, app: str) -> list[dict[str, Any]]:
        scope = self._local.scope(app)
        if scope is None:
            return []
        return [
            # Normalize `path` to the ref (the `file` name): `LocalScenarioScope.list` sets it to
            # the resolved on-disk path, but `read`/`save` — and the UI's read-back-by-path round
            # trip — take a ref, never a control-plane filesystem path (matching
            # ObjectScenarioStorage.list's contract, and BE-0051's "no path ever exists on the
            # control plane").
            {**s, "path": s["file"]}
            for s in scope.list()
            # Only entries `read` would also accept: `list_scenarios` (behind `LocalScenarioScope`)
            # globs every `*.yaml`, unlike `ObjectScenarioStorage.list`, which already screens through
            # `valid_scenario_ref`. Without this filter, an unusual filename could list but then 404
            # on every read/run.
            if valid_scenario_ref(s["file"])
        ]

    def read(self, app: str, ref: str | None) -> str | None:
        scope = self._local.scope(app)
        if scope is None:
            return None
        try:
            return scope.read(ref)
        except (OSError, ValueError):
            # Lenient like ObjectScenarioStorage.read: a file that exists but can't be decoded (or
            # vanished mid-read) must not 500 the UI or a dispatch — degrade to "not found" instead.
            # Logged (not silent): an operator seeing scenarios go missing needs a signal pointing at
            # a bad file rather than a genuinely absent one.
            _logger.warning("scenario %s/%s exists but could not be read", app, ref, exc_info=True)
            return None

    def save(self, app: str, ref: str | None, text: str) -> str | None:
        return self._save_storage.save(app, ref, text)
