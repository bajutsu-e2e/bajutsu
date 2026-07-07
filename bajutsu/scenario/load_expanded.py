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


def contained_ref(root: Path, base: Path, ref: str) -> Path:
    """Resolve a scenario ref (`base / ref`) and require its real path to stay within `root`.

    The one containment choke point for a scenario's component / data refs (BE-0174): every
    device-free resolver routes ref resolution through here, so a scenario cannot make the loader
    read a file outside its suite. `resolve` follows symlinks, so an out-of-root link is caught the
    same as a `..` chain or an absolute path — the three ways a ref leaves the tree. On rejection the
    error names only the offending ref, never the target's contents, so the check happens *before*
    the read and closes the leak as well as the read.

    Args:
        root: The suite root the ref must stay within (the scenarios dir the load started from).
        base: The directory refs resolve against (the referring scenario file's directory).
        ref: The `use:` component or `dataFile` reference to resolve.

    Returns:
        The resolved real path, ready to read.

    Raises:
        ValueError: The ref is absolute, escapes the root via `..`, or symlinks outside it.
    """
    target = (base / ref).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f"reference {ref!r} resolves outside the suite root")
    return target


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


def load_expanded_scenarios(path: Path, root: Path | None = None) -> list[Scenario]:
    """Load a scenario file and expand its components + data rows, resolving refs relative to the file.

    Args:
        path: The scenario file to load.
        root: The suite root every component / data ref must stay within (BE-0174). Defaults to the
            scenario file's own directory, so a single-file load confines refs to that directory; a
            suite loader passes the shared scenarios dir instead.

    Raises:
        OSError: The scenario file or a referenced component / CSV cannot be read.
        ValueError: The content is invalid, the YAML does not parse — `_parse_yaml_named`
            normalizes a `yaml.YAMLError` into a `ValueError` naming the offending file (the scenario
            or a referenced component), so its callers' `except (OSError, ValueError)` guard a
            malformed file as cleanly as a structurally-invalid one (BE-0150) — or a ref resolves
            outside `root` (`contained_ref`).
    """
    base = path.parent
    root = base if root is None else root
    scenarios = _parse_yaml_named(path, load_scenario_file).scenarios
    expand_components(
        scenarios, lambda ref: _parse_yaml_named(contained_ref(root, base, ref), load_component)
    )
    return expand_data(
        scenarios,
        lambda ref: read_csv(contained_ref(root, base, ref).read_text(encoding="utf-8")),
    )


def load_scenarios_dir(scenarios_dir: Path) -> list[Scenario]:
    """Every expanded scenario in *scenarios_dir*'s ``*.yaml`` files, sorted by filename.

    The device-free suite loader shared by `coverage` on the CLI and in the serve Web UI, so both read
    a target's suite identically. The scenarios dir is the containment root passed to each file's
    load, so a scenario's refs stay inside the suite (BE-0174).

    Raises:
        OSError, ValueError: as `load_expanded_scenarios` — an unreadable or invalid file, or a ref
            that escapes the suite root.
    """
    return [
        s
        for f in sorted(scenarios_dir.glob("*.yaml"))
        for s in load_expanded_scenarios(f, root=scenarios_dir)
    ]
