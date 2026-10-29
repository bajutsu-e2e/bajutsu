"""Scenario operations confined to one on-disk directory — serve's default."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bajutsu.serve.helpers import (
    _scenario_path,
    list_scenarios,
    scenario_out_path,
    unique_scenario_path,
)

from .authored import Authored
from .runnable import Runnable


class LocalScenarioScope:
    """Scenario operations confined to a single on-disk scenarios dir — the default for serve."""

    def __init__(self, scenarios_dir: Path) -> None:
        self._dir = scenarios_dir

    def list(self) -> list[dict[str, Any]]:
        return list_scenarios(self._dir)

    def directory(self) -> Path | None:
        return self._dir

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

    def authored(self, name: str) -> Authored:
        self._dir.mkdir(parents=True, exist_ok=True)
        out = unique_scenario_path(scenario_out_path(self._dir, name))
        # The file lands on the local run host directly, so there's nothing to persist afterward.
        return Authored(out=str(out))
