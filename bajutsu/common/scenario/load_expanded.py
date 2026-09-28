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

from bajutsu.common.scenario.expand import ScopedResolve, expand_components, expand_data, read_csv
from bajutsu.common.scenario.load import load_component, load_scenario_file
from bajutsu.common.scenario.models import Component, Scenario
from bajutsu.common.scenario.models.scenario.component import is_component_file_ref


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


def parse_yaml_named[T](file: Path, parse: Callable[[str], T]) -> T:
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


class ComponentResolver(ScopedResolve):
    """Resolve a `use: { component: <ref> }` ref by the ref's own shape (BE-0422).

    Binding a file's own `components:` map, the suite root, and the directory refs resolve against
    into one object is what lets the deterministic `run` gate and the device-free readers expand the
    same scenario file identically — the two built separate resolvers before, and had already
    drifted on how a malformed component file reported (BE-0150).
    """

    def __init__(
        self,
        components: dict[str, Component],
        *,
        root: Path,
        base: Path,
        source: Path | None,
    ) -> None:
        """Bind the resolver to one file's scope.

        The three paths are keyword-only: they are interchangeable to the type checker, so a
        transposed `root` and `base` would otherwise pass every check and quietly move the
        containment boundary.

        Args:
            components: The `components:` map a bare name resolves against.
            root: The suite root a path ref must stay within (BE-0174).
            base: The directory a path ref resolves against.
            source: The scenario file whose `components:` map this is, named in the error when a
                bare name misses. None marks a component file's scope, which declares no map at
                all — a different mistake, and reported as one.

        Raises:
            ValueError: `source` is None alongside a non-empty map. A component-file scope exists
                precisely to hold no names, so accepting one would silently reopen the leak the
                scope swap closes.
        """
        if source is None and components:
            raise ValueError("a component-file scope declares no `components:` of its own")
        self._components = components
        self._root = root
        self._base = base
        self._source = source
        self._cache: dict[str, Component] = {}
        self._file_scope: ComponentResolver | None = None

    def __call__(self, ref: str) -> Component:
        """The component *ref* names, resolved once per resolver and cached.

        Raises:
            OSError: A path ref names a file that cannot be read.
            ValueError: A bare name no bound `components:` entry defines, or a path ref that
                escapes the suite root or holds invalid YAML.
        """
        if ref not in self._cache:
            self._cache[ref] = self._resolve(ref)
        return self._cache[ref]

    def _resolve(self, ref: str) -> Component:
        """Read a path ref off disk, or look a bare name up in the bound map."""
        if is_component_file_ref(ref):
            return parse_yaml_named(contained_ref(self._root, self._base, ref), load_component)
        component = self._components.get(ref)
        if component is None:
            raise ValueError(f"component {ref!r} is not defined: {self._undefined_hint()}")
        return component

    def _undefined_hint(self) -> str:
        """Why a bare name missed here, which differs between a scenario file and a component file."""
        if self._source is None:
            return (
                "a component file declares no `components:` of its own, so only a ref holding a "
                "'/' or ending in .yaml / .yml resolves inside one"
            )
        return (
            f"no such entry in the `components:` of {self._source} (a ref holding a '/' or "
            "ending in .yaml / .yml resolves as a file instead)"
        )

    def scope_for(self, ref: str) -> ComponentResolver:
        """The resolver *ref*'s own steps expand under: the file scope for a path ref, else self."""
        return self.for_component_file() if is_component_file_ref(ref) else self

    def for_component_file(self) -> ComponentResolver:
        """A sibling bound to no local names, for expanding a component file's own steps.

        A component file is a bare `params` + `steps` mapping and declares no `components:`, so every
        bare ref inside one is undefined however the including scenario file spelled its own map.
        A component-file scope is already that, so it answers with itself; every other resolver
        builds its sibling once and reuses it, keeping one ref cache across the whole expansion.
        """
        if self._source is None:
            return self
        if self._file_scope is None:
            self._file_scope = ComponentResolver({}, root=self._root, base=self._base, source=None)
        return self._file_scope


def load_expanded_scenarios(path: Path, root: Path | None = None) -> list[Scenario]:
    """Load a scenario file and expand its components + data rows, resolving refs relative to the file.

    Args:
        path: The scenario file to load.
        root: The suite root every component / data ref must stay within (BE-0174). Defaults to the
            scenario file's own directory, so a single-file load confines refs to that directory; a
            suite loader passes the shared scenarios dir instead.

    Raises:
        OSError: The scenario file or a referenced component / CSV cannot be read.
        ValueError: The content is invalid, the YAML does not parse — `parse_yaml_named`
            normalizes a `yaml.YAMLError` into a `ValueError` naming the offending file (the scenario
            or a referenced component), so its callers' `except (OSError, ValueError)` guard a
            malformed file as cleanly as a structurally-invalid one (BE-0150) — a ref resolves
            outside `root` (`contained_ref`), or a bare `use` ref names no entry in this file's own
            `components:` (`ComponentResolver`).
    """
    base = path.parent
    root = base if root is None else root
    scenario_file = parse_yaml_named(path, load_scenario_file)
    scenarios = scenario_file.scenarios
    expand_components(
        scenarios, ComponentResolver(scenario_file.components, root=root, base=base, source=path)
    )
    expanded = expand_data(
        scenarios,
        lambda ref: read_csv(contained_ref(root, base, ref).read_text(encoding="utf-8")),
    )
    for s in expanded:
        s.set_source_stem(path.stem)
        if s.preconditions.seed_photos:
            s.preconditions.seed_photos = [
                str(contained_ref(root, base, ref)) for ref in s.preconditions.seed_photos
            ]
    return expanded


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
