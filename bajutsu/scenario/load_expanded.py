"""The device-free scenario loader: parse a file and expand its components + data rows.

Lives in the scenario package (not a frontend module) so every device-free reader can share it
without pulling a frontend's stack: the CLI's `trace --explain` / `audit` / `coverage` and the serve
Web UI's coverage view (BE-0146) both load a suite the same way. `run` keeps its own setup-prefixing
loader.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from bajutsu.scenario.expand import expand_components, expand_data, read_csv
from bajutsu.scenario.load import load_component, load_scenario_file
from bajutsu.scenario.models import Scenario


def _parse_yaml_named[T](file: Path, parse: Callable[[str], T]) -> T:
    """Read and *parse* a YAML file, re-raising a syntax error as a `ValueError` naming *file*.

    A `yaml.YAMLError` is not a `ValueError` subclass, so a caller's `except (OSError, ValueError)`
    would leak it as a traceback. Normalizing per file — so a malformed referenced component is
    attributed to the component, not the top-level scenario — and collapsing PyYAML's multi-line
    text keeps the one-line error clean and actionable (BE-0150). An `OSError` (unreadable file) is
    left to propagate unchanged.
    """
    try:
        return parse(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML in {file}: {' '.join(str(e).split())}") from e


def load_expanded_scenarios(path: Path) -> list[Scenario]:
    """Load a scenario file and expand its components + data rows, resolving refs relative to the file.

    Raises:
        OSError: The scenario file or a referenced component / CSV cannot be read.
        ValueError: The content is invalid, or the YAML does not parse — `_parse_yaml_named`
            normalizes a `yaml.YAMLError` into a `ValueError` naming the offending file (the scenario
            or a referenced component), so its callers' `except (OSError, ValueError)` guard a
            malformed file as cleanly as a structurally-invalid one (BE-0150).
    """
    base = path.parent
    scenarios = _parse_yaml_named(path, load_scenario_file).scenarios
    expand_components(scenarios, lambda ref: _parse_yaml_named(base / ref, load_component))
    return expand_data(scenarios, lambda ref: read_csv((base / ref).read_text(encoding="utf-8")))


def load_scenarios_dir(scenarios_dir: Path) -> list[Scenario]:
    """Every expanded scenario in *scenarios_dir*'s ``*.yaml`` files, sorted by filename.

    The device-free suite loader shared by `coverage` on the CLI and in the serve Web UI, so both read
    a target's suite identically.

    Raises:
        OSError, ValueError: as `load_expanded_scenarios` — an unreadable or invalid file.
    """
    return [s for f in sorted(scenarios_dir.glob("*.yaml")) for s in load_expanded_scenarios(f)]
