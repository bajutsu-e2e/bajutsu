"""Compile-time expansion: components (`use`), data-driven rows, and reusable setups.

All of this runs before the deterministic run loop, so after expansion no `use` steps remain
and each data row is its own scenario — the runner is unaffected.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from bajutsu import interp
from bajutsu.scenario.models import Component, Scenario, Step


def _interp_steps(steps: list[Step], bindings: dict[str, str]) -> list[Step]:
    """Substitute `bindings` into each step (via a model_dump round-trip) and re-validate.

    Aliases are preserved (by_alias) so the dump re-parses cleanly.
    """
    out: list[Step] = []
    for st in steps:
        dumped = st.model_dump(by_alias=True, exclude_none=True)
        out.append(Step.model_validate(interp.interpolate(dumped, bindings)))
    return out


def expand_components(
    scenarios: list[Scenario],
    resolve: Callable[[str], Component],
    max_depth: int = 25,
) -> None:
    """Replace every `use` step with the referenced component's steps, recursively and in place.

    Pure compile-time expansion: a component may itself `use` another, and after this no `use`
    steps remain, so the run loop is unaffected.

    Args:
        scenarios: The scenarios to expand; their `steps` are rewritten in place.
        resolve: Maps a component name to its `Component` (e.g. by loading a shared file).
        max_depth: The deepest `use` nesting allowed before giving up on a runaway chain.

    Raises:
        ValueError: A required param is missing, an unknown param is passed, a `${params.*}` token
            references an undeclared param, a reference cycle is detected, or nesting exceeds
            `max_depth`.
    """
    cache: dict[str, Component] = {}

    def expand(steps: list[Step], stack: list[str]) -> list[Step]:
        if len(stack) > max_depth:
            raise ValueError(f"component nesting too deep (>{max_depth}): {' -> '.join(stack)}")
        out: list[Step] = []
        for st in steps:
            if st.use is None:
                out.append(st)
                continue
            ref = st.use.component
            if ref in stack:
                raise ValueError(f"component cycle detected: {' -> '.join([*stack, ref])}")
            if ref not in cache:
                cache[ref] = resolve(ref)
            comp = cache[ref]
            args = st.use.with_
            missing = sorted(set(comp.params) - set(args))
            unknown = sorted(set(args) - set(comp.params))
            if missing:
                raise ValueError(f"component {ref!r} missing required params: {missing}")
            if unknown:
                raise ValueError(f"component {ref!r} has unknown params: {unknown}")
            substituted = _interp_steps(comp.steps, {f"params.{k}": v for k, v in args.items()})
            dumps = [s.model_dump(by_alias=True, exclude_none=True) for s in substituted]
            residual = sorted(t for t in interp.find_tokens(dumps) if t.startswith("params."))
            if residual:
                raise ValueError(f"component {ref!r} references undeclared params: {residual}")
            out.extend(expand(substituted, [*stack, ref]))
        return out

    for scenario in scenarios:
        scenario.steps = expand(scenario.steps, [])


def read_csv(text: str) -> list[dict[str, str]]:
    """Parse CSV text into a list of {column: value} row dicts (header row required)."""
    import csv
    import io

    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def _instantiate(scenario: Scenario, row: dict[str, str], index: int) -> Scenario:
    dumped = scenario.model_dump(by_alias=True, exclude_none=True)
    dumped.pop("data", None)
    dumped.pop("dataFile", None)
    out = cast(
        "dict[str, Any]", interp.interpolate(dumped, {f"row.{k}": v for k, v in row.items()})
    )
    kv = ", ".join(f"{k}={v}" for k, v in row.items())
    out["name"] = (
        f"{scenario.name} [row {index + 1}: {kv}]" if kv else f"{scenario.name} [row {index + 1}]"
    )
    return Scenario.model_validate(out)


def expand_data(
    scenarios: list[Scenario],
    resolve_csv: Callable[[str], list[dict[str, str]]],
) -> list[Scenario]:
    """Expand each data-driven scenario into one scenario per data row.

    `${row.<col>}` tokens are substituted per row. A scenario with neither `data` nor `dataFile`
    passes through unchanged. Each derived scenario keeps the original's preconditions (erase
    default intact), so every row runs in its own clean environment — isolation is preserved.

    Args:
        scenarios: The scenarios to expand.
        resolve_csv: Loads a `dataFile` reference into a list of `{column: value}` rows.

    Returns:
        The scenarios with every data-driven one replaced by its per-row instances, in order.
    """
    out: list[Scenario] = []
    for s in scenarios:
        if s.data is not None:
            rows: list[dict[str, str]] | None = s.data
        elif s.data_file is not None:
            rows = resolve_csv(s.data_file)
        else:
            rows = None
        if rows is None:
            out.append(s)
            continue
        out.extend(_instantiate(s, row, i) for i, row in enumerate(rows))
    return out


def apply_setups(
    scenarios: list[Scenario],
    default_setup: str | None,
    resolve: Callable[[str], list[Step]],
) -> None:
    """Prepend each scenario's reusable setup prelude, in place.

    A scenario's `setup` precondition (falling back to the app/config default) names a reusable
    prelude; those steps run before the scenario's own, so a shared login / navigation flow is
    written once and reused. The same reference is resolved at most once.

    Args:
        scenarios: The scenarios to prepend setups to; their `steps` are rewritten in place.
        default_setup: The setup reference used when a scenario declares none. None means none.
        resolve: Maps a setup reference to its list of steps (e.g. by loading a shared file).
    """
    cache: dict[str, list[Step]] = {}
    for scenario in scenarios:
        ref = scenario.preconditions.setup or default_setup
        if not ref:
            continue
        if ref not in cache:
            cache[ref] = resolve(ref)
        scenario.steps = [*cache[ref], *scenario.steps]
