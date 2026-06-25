"""Helpers shared across CLI command modules (config loading, backend parsing).

Command-specific helpers live with their command in `commands/<name>.py`; only the
genuinely cross-command pieces belong here, so adding a command rarely edits this file.
"""

from __future__ import annotations

from pathlib import Path

import typer

from bajutsu.config import Effective, load_config, resolve
from bajutsu.scenario import (
    Scenario,
    expand_components,
    expand_data,
    load_component,
    load_scenario_file,
    read_csv,
)

DEFAULT_CONFIG = "bajutsu.config.yaml"


def load_expanded_scenarios(path: Path) -> list[Scenario]:
    """Load a scenario file and expand its components + data rows, resolving refs relative to the file.

    The shared device-free loader behind `trace --explain` and `audit` (setup-prefixing
    `run` keeps its own loader).

    Raises:
        OSError: The scenario file or a referenced component / CSV cannot be read.
        ValueError: The file parses but its content is invalid.
    """
    base = path.parent
    scenarios = load_scenario_file(path.read_text(encoding="utf-8")).scenarios
    expand_components(
        scenarios, lambda ref: load_component((base / ref).read_text(encoding="utf-8"))
    )
    return expand_data(scenarios, lambda ref: read_csv((base / ref).read_text(encoding="utf-8")))


def resolve_run_dir(run: str, runs_root: str) -> Path:
    """Resolve a run id or path to its directory.

    A bare id (``r1``) resolves under *runs_root*; an absolute or multi-segment value
    (``/abs/run``, ``runs/r1``) is taken verbatim — so a mistyped path is never silently
    re-rooted under the runs dir. Shared by ``export`` and ``report``.

    Returns:
        The resolved run directory path (not checked for existence).
    """
    p = Path(run)
    return p if p.is_absolute() or len(p.parts) > 1 else Path(runs_root) / run


def _load_effective(config: str, target_name: str) -> Effective:
    """Load and resolve the effective config for *target_name*.

    Exits 2 (via ``typer.Exit``) for two specific failures that produce a user-friendly
    message: the config file not existing, and an unknown target name.  Other errors —
    YAML parse failures and schema validation errors from ``load_config`` — are *not*
    caught and propagate as exceptions to the caller.
    """
    cfg_path = Path(config)
    if not cfg_path.exists():
        typer.echo(f"config not found: {config}")
        raise typer.Exit(2)
    cfg = load_config(cfg_path.read_text(encoding="utf-8"))
    try:
        return resolve(cfg, target_name)
    except KeyError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None


def _backends(backend: str, fallback: list[str]) -> list[str]:
    """Parse a comma-separated backend string into a list, or return *fallback* when the string is empty."""
    return [b.strip() for b in backend.split(",") if b.strip()] if backend else fallback
