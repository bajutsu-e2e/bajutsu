"""The ScenarioStore seam: how a scenario is resolved, listed, read, and written (BE-0015).

Scenario resolution is the most security-sensitive serve surface: a client must never be able to
run or read an arbitrary file path on the host (BE-0051). `ScenarioStore` is the one point where
this diverges between local and server hosting: the local store confines everything to the app's
scenarios dir on disk (`LocalScenarioStore`), while a server store would fetch by id from
per-project storage — where a path never exists, so arbitrary-path execution is impossible by
construction. The containment and the `resolve_runnable` guard live here, in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from bajutsu.serve.helpers import (
    _scenario_path,
    list_scenarios,
    scenario_out_path,
    unique_scenario_path,
)


class ScenarioScope(Protocol):
    """Scenario operations confined to one app's scenarios."""

    def list(self) -> list[dict[str, Any]]:
        """Every scenario, summarized for the UI."""

    def resolve_runnable(self, scenario: str) -> Path | None:
        """Match *scenario* (by basename) against the app's existing ``*.yaml`` files, returning
        the trusted path from the dir listing — never the client string — or None if no such file.
        """

    def read(self, ref: str | None) -> str | None:
        """The YAML text of the scenario at *ref*, or None if it's missing or escapes the dir."""

    def resolve_writable(self, ref: str | None) -> Path | None:
        """A confined ``*.yaml`` path to save *ref* to (it need not exist yet), or None if *ref*
        would escape the dir or isn't a scenario file."""

    def out_path(self, name: str) -> Path:
        """A fresh, unique ``*.yaml`` path for an authored scenario named *name* (creating the
        scenarios dir if needed)."""


class ScenarioStore(Protocol):
    """Maps an app to its scenario scope."""

    def scope(self, app: str | None) -> ScenarioScope | None:
        """The scope for *app*, or None when the app has no scenarios dir."""


class LocalScenarioScope:
    """Scenario operations confined to a single on-disk scenarios dir — the default for serve."""

    def __init__(self, scenarios_dir: Path) -> None:
        self._dir = scenarios_dir

    def list(self) -> list[dict[str, Any]]:
        return list_scenarios(self._dir)

    def resolve_runnable(self, scenario: str) -> Path | None:
        name = Path(scenario).name  # honour only the basename, then match the trusted dir listing
        return next((p for p in self._dir.glob("*.yaml") if p.name == name and p.is_file()), None)

    def read(self, ref: str | None) -> str | None:
        target = _scenario_path(self._dir, ref)
        if target is None or not target.is_file():
            return None
        return target.read_text(encoding="utf-8")

    def resolve_writable(self, ref: str | None) -> Path | None:
        return _scenario_path(self._dir, ref)

    def out_path(self, name: str) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return unique_scenario_path(scenario_out_path(self._dir, name))


class LocalScenarioStore:
    """Resolves an app to its on-disk scenarios dir, then scopes operations to it.

    The dir is resolved through a callable rather than captured, so a config opened from the UI
    after construction is reflected (the resolver reads the live serve state).
    """

    def __init__(self, resolve_dir: Callable[[str | None], Path | None]) -> None:
        self._resolve_dir = resolve_dir

    def scope(self, app: str | None) -> ScenarioScope | None:
        scn_dir = self._resolve_dir(app)
        return LocalScenarioScope(scn_dir) if scn_dir is not None else None
