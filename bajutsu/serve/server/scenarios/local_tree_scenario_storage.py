"""Scenario storage over the bound config's already-extracted local tree."""

from __future__ import annotations

import logging
from typing import Any

from bajutsu.serve.helpers import valid_scenario_ref
from bajutsu.serve.scenarios import LocalScenarioStore
from bajutsu.serve.state import ServeState, _scenarios_dir_for

from .scenario_storage import ScenarioStorage

_logger = logging.getLogger(__name__)


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

    config_tree = True

    def __init__(self, state: ServeState, save_storage: ScenarioStorage) -> None:
        # The read side resolves against the configuration the *requesting session* is bound to
        # (BE-0393 unit 2); the local store's `scope` hands the resolver that session per call.
        self._local = LocalScenarioStore(
            lambda app, session, org: _scenarios_dir_for(state, app, session, org)
        )
        self._save_storage = save_storage

    def has_app(self, app: str, *, session: str | None = None, org: str = "") -> bool:
        return self._save_storage.has_app(app, session=session, org=org)

    def list(self, app: str, *, session: str | None = None, org: str = "") -> list[dict[str, Any]]:
        scope = self._local.scope(app, session=session, org=org)
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

    def read(
        self, app: str, ref: str | None, *, session: str | None = None, org: str = ""
    ) -> str | None:
        scope = self._local.scope(app, session=session, org=org)
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
