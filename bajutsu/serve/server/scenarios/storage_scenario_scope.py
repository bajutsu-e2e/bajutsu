from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from bajutsu.serve.helpers import scenario_out_name, valid_scenario_ref
from bajutsu.serve.scenarios import Authored, Runnable

from .scenario_storage import ScenarioStorage

# Where a materialized scenario lands in the worker's workspace (and the `--scenario` arg used).
_WORKSPACE_SCENARIOS = "scenarios"


class StorageScenarioScope:
    """Authoring operations for one project's scenarios, backed by `ScenarioStorage`."""

    def __init__(
        self, storage: ScenarioStorage, app: str, *, session: str | None = None, org: str = ""
    ) -> None:
        self._storage = storage
        self._app = app
        self._session = session
        self._org = org

    def list(self) -> list[dict[str, Any]]:
        return self._storage.list(self._app, session=self._session, org=self._org)

    def directory(self) -> Path | None:
        # Scenarios live in storage, never on this host's disk, so no file ref can be followed here.
        return None

    def read(self, ref: str | None) -> str | None:
        # A ref is a trust boundary (an object-store key / DB id) even with no filesystem here:
        # reject an obviously unsafe ref before it reaches the backing store.
        if not valid_scenario_ref(ref):
            return None
        return self._storage.read(self._app, ref, session=self._session, org=self._org)

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
        text = self._storage.read(self._app, name, session=self._session, org=self._org)
        if text is None:
            return None
        rel = f"{_WORKSPACE_SCENARIOS}/{name}"
        return Runnable(arg=rel, materials={rel: text})

    def authored(self, name: str) -> Authored:
        # No filesystem here: pick a safe ref, stamp it if taken (don't clobber), and tell the
        # worker to write to a workspace-relative path then persist it to storage as (app, ref).
        ref = scenario_out_name(name)
        if self._storage.read(self._app, ref, session=self._session, org=self._org) is not None:
            # Microsecond precision so two records in the same second don't pick the same ref.
            stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S-%f")
            ref = f"{ref[: -len('.yaml')]}-{stamp}.yaml"
        return Authored(out=f"{_WORKSPACE_SCENARIOS}/{ref}", save=(self._app, ref))
