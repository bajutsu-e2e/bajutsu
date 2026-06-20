"""The ScenarioStore seam: how a scenario is resolved, listed, read, and written (BE-0015).

Scenario resolution is the most security-sensitive serve surface: a client must never be able to
run or read an arbitrary file path on the host (BE-0051). `ScenarioStore` is the one point where
this diverges between local and server hosting: the local store confines everything to the app's
scenarios dir on disk (`LocalScenarioStore`), while a server store would fetch by id from
per-project storage — where a path never exists, so arbitrary-path execution is impossible by
construction. The containment and the `runnable` guard live here, in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from bajutsu.serve.helpers import (
    _scenario_path,
    list_scenarios,
    scenario_out_path,
    unique_scenario_path,
)


@dataclass(frozen=True)
class Runnable:
    """A scenario ready to run via ``bajutsu run --scenario <arg>``.

    `arg` is the value to pass — a trusted absolute path on the local backend, or a
    workspace-relative path on the server backend. `materials` maps workspace-relative paths to
    file contents the run host must write **before** running (empty locally, where the files
    already exist on disk; on the server it carries the scenario text so a remote worker can
    materialize it — never a path a client controls)."""

    arg: str
    materials: dict[str, str] = field(default_factory=dict)


class ScenarioScope(Protocol):
    """Scenario operations confined to one app's scenarios."""

    def list(self) -> list[dict[str, Any]]:
        """Every scenario, summarized for the UI."""

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

    def runnable(self, scenario: str) -> Runnable | None:
        name = Path(scenario).name  # honour only the basename, then match the trusted dir listing
        base = self._dir.resolve()
        # Basename match plus a resolved-containment check: a `*.yaml` that is a symlink out of the
        # dir is rejected, so a runnable path can never escape the confinement (BE-0051).
        path = next(
            (
                p
                for p in self._dir.glob("*.yaml")
                if p.name == name and p.is_file() and base in p.resolve().parents
            ),
            None,
        )
        # The file is already on the local run host, so no materials travel — the run uses the
        # trusted absolute path directly.
        return Runnable(arg=str(path)) if path is not None else None

    def read(self, ref: str | None) -> str | None:
        target = _scenario_path(self._dir, ref)
        if target is None or not target.is_file():
            return None
        return target.read_text(encoding="utf-8")

    def save(self, ref: str | None, text: str) -> str | None:
        target = _scenario_path(self._dir, ref)
        if target is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)  # a fresh project's dir may not exist yet
        target.write_text(text, encoding="utf-8")
        return str(target)

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
