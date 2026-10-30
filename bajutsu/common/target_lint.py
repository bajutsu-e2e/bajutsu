"""Lint a scenario file against the targets it will run on, before any device work (BE-0467).

`bajutsu lint` alone validates the schema and reads no config, so it cannot see that a target's
route lacks a step's capability or cannot apply a setting. Given the config, this resolves each
scenario's targets the way `run` does and asks the same three questions the run asks before it
leases a device: the BE-0082 step check over the steps routed to each target, the BE-0467 settings
check, and the BE-0452 precondition check. One answer for the lint and the run means a lint that
passes predicts a preflight that passes.

Pure: no device, no clock, no network. The actuator is the first implemented one the target's
backend names, the one `run` selects when every actuator is available.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from bajutsu.common.backends import IMPLEMENTED, capabilities_for_run, resolve_actuators
from bajutsu.common.capability import capability_preflight
from bajutsu.common.capability.settings_support import check_settings
from bajutsu.common.config.preconditions import check_preconditions, resolve_preconditions
from bajutsu.common.drivers import base
from bajutsu.common.scenario import (
    ComponentResolver,
    Scenario,
    declared_name,
    expand_components,
    load_expanded_scenarios,
    load_scenario_file,
    scenario_sources,
)

if TYPE_CHECKING:
    from bajutsu.common.config import Effective

# A finding on a main `steps` entry starts with its 1-based index (`_walk_steps`); the lifecycle
# phases carry a `setup ` / `cleanup[i] ` prefix and keep no line.
_STEP_PATH = re.compile(r"^step (\d+)\b")


class TargetRequired(ValueError):
    """A scenario declares no `targets` and no `--target` names one to check it against."""


@dataclass(frozen=True)
class TargetFinding:
    """One reason a scenario cannot run on a target, anchored to its source line when known.

    Attributes:
        scenario: The (expanded) scenario's name.
        target: The target the finding is about; every driven target for a precondition finding.
        message: What the target cannot do, naming the step path or the setting.
        line: The 1-based line of the authored step (a component's `use:` line for a step it
            produced), or None for a finding on the scenario as a whole.
    """

    scenario: str
    target: str
    message: str
    line: int | None = None


def offline_actuator(eff: Effective, backends: Sequence[str] | None = None) -> str | None:
    """The actuator a run of *eff* selects when every one is available, or None for none yet.

    Args:
        eff: The target's effective config.
        backends: The backend list in force (a checked `--backend`); the target's own if None.
    """
    tokens = list(eff.backend if backends is None else backends)
    return next((a for a in resolve_actuators(tokens) if a in IMPLEMENTED), None)


def lint_against_targets(
    text: str,
    path: Path,
    effective: Callable[[str], Effective],
    target: str | None,
    *,
    root: Path | None = None,
) -> list[TargetFinding]:
    """Every reason a scenario in *path* cannot run on the targets it would run on.

    Args:
        text: The scenario file's content, already schema-valid.
        path: Where the file lives (or would): its component and data refs resolve beside it.
        effective: Resolves a target name to its effective config; raises for an unknown name.
        target: The target a scenario declaring no `targets` runs on; None when none was named.
        root: The containment root for component and data refs; the file's own directory if None.

    Raises:
        TargetRequired: A scenario declares no `targets` and *target* is None.
        OSError: A referenced component or CSV cannot be read.
        ValueError: A component or data ref does not expand.
    """
    expanded = load_expanded_scenarios(path, root, text=text)
    lines = _step_lines(text, path, root, expanded)
    findings: list[TargetFinding] = []
    for plan in _plans(expanded, effective, target):
        s = plan.scenario
        findings += [
            TargetFinding(scenario=s.name, target=", ".join(plan.effs), message=problem)
            for problem in check_preconditions(
                s.preconditions, list(plan.effs.values()), interrupts=s.interrupts
            )
        ]
        step_lines = lines.get(declared_name(s.name))
        for name, eff in plan.effs.items():
            findings += _target_findings(
                plan.folded,
                name,
                eff,
                routed=plan.declared,
                grouped=name in plan.grouped,
                step_lines=step_lines,
            )
    return findings


def required_route_tokens(
    text: str,
    path: Path,
    effective: Callable[[str], Effective],
    target: str,
    *,
    root: Path | None = None,
    backend: str = "",
    tags: Sequence[str] = (),
    exclude: Sequence[str] = (),
    grid: bool = False,
) -> dict[str, list[str]]:
    """The `cap:` and `setting:` tokens a job running *path* needs, each with what needs it (BE-0467).

    serve routes the job by these, so it reaches only a worker whose routes can run every step and
    apply every setting it puts in effect — the same two checks the run preflight makes.

    Args:
        text: The scenario file's content.
        path: Where the file lives (or would): its component and data refs resolve beside it.
        effective: Resolves a target name to its effective config.
        target: The job's `--target`, which a scenario declaring no `targets` runs on.
        root: The containment root for component and data refs; the file's own directory if None.
        backend: The job's checked `--backend`; a fake backend has no route and needs no token.
        tags: The job's `--tag` selection; empty runs every scenario.
        exclude: The job's `--exclude` selection.
        grid: Whether the job runs on an Appium grid, which puts its iOS targets on the live route.

    Raises:
        OSError: A referenced component or CSV cannot be read.
        ValueError: The file does not load or expand.
    """
    from bajutsu.common.backends import backend_override
    from bajutsu.common.capability.route_tokens import target_route_needs
    from bajutsu.common.scenario import select_scenarios

    expanded = load_expanded_scenarios(path, root, text=text)
    if tags or exclude:
        expanded = select_scenarios(expanded, list(tags), list(exclude))
    needs: dict[str, list[str]] = {}
    for plan in _plans(expanded, effective, target):
        for name, eff in plan.effs.items():
            actuator = offline_actuator(eff, backend_override(backend, eff.backend))
            for token, why in target_route_needs(
                plan.folded,
                name,
                actuator,
                eff,
                routed=plan.declared,
                grouped=name in plan.grouped,
                grid=grid,
            ).items():
                needs.setdefault(token, []).extend(f"{plan.scenario.name}: {w}" for w in why)
    return needs


@dataclass(frozen=True)
class _Plan:
    """One expanded scenario with the targets it runs on, as the run resolves them."""

    scenario: Scenario
    folded: Scenario  # with the targets' lifecycle hooks folded in
    effs: dict[str, Effective]
    declared: bool
    grouped: frozenset[str]


def _plans(
    expanded: list[Scenario], effective: Callable[[str], Effective], target: str | None
) -> list[_Plan]:
    """Each scenario paired with its declared targets, or *target* for one declaring none."""
    from bajutsu.common.runner.pipeline import with_lifecycle_phases

    plans: list[_Plan] = []
    for s in expanded:
        names = list(s.target_names) or ([target] if target else [])
        if not names:
            raise TargetRequired(
                f"{s.name}: declares no targets; pass --target <name> to check it against one"
            )
        effs = {name: effective(name) for name in names}
        declared = bool(s.target_names)
        folded = with_lifecycle_phases(effs[names[0]], [s], effs if declared else None)[0]
        grouped = frozenset(n for g in s.device_groups if len(g) >= 2 for n in g)
        plans.append(_Plan(s, folded, effs, declared, grouped))
    return plans


def _target_findings(
    s: Scenario,
    name: str,
    eff: Effective,
    *,
    routed: bool,
    grouped: bool,
    step_lines: list[int] | None,
) -> list[TargetFinding]:
    """The step and settings findings for one target, mirroring the run preflight's per-target check."""
    actuator = offline_actuator(eff)
    out: list[TargetFinding] = []
    if actuator is not None:
        caps = capabilities_for_run(actuator, eff)
        if grouped and base.Capability.DEVICE_GROUP not in caps:
            out.append(
                TargetFinding(
                    scenario=s.name,
                    target=name,
                    message=f"a device group needs '{base.Capability.DEVICE_GROUP}', which "
                    f"backend '{actuator}' lacks",
                )
            )
        narrowed = capability_preflight.steps_for_target(s, name) if routed else s
        # The narrowed scenario numbers only the steps routed here, so map each back to its place
        # among all the scenario's steps before reading the authored line.
        kept = [
            i for i, st in enumerate(s.steps) if not routed or st.resolved_target in {name, None}
        ]
        out += [
            TargetFinding(
                scenario=s.name,
                target=name,
                message=reason,
                line=_line_of(reason, kept, step_lines),
            )
            for reason in capability_preflight.unsupported(narrowed, caps)
        ]
    check = check_settings(
        actuator, eff, resolve_preconditions(eff, s.preconditions), s.permissions
    )
    out += [
        TargetFinding(scenario=s.name, target=name, message=problem) for problem in check.problems()
    ]
    return out


def _line_of(reason: str, kept: list[int], step_lines: list[int] | None) -> int | None:
    """The authored line of the main step *reason* names, or None when it names none."""
    match = _STEP_PATH.match(reason)
    if match is None or step_lines is None:
        return None
    index = int(match.group(1)) - 1
    if index >= len(kept) or kept[index] >= len(step_lines):
        return None
    return step_lines[kept[index]]


def _step_lines(
    text: str, path: Path, root: Path | None, expanded: list[Scenario]
) -> dict[str, list[int]]:
    """Each scenario's authored line per *expanded* main step, keyed by its declared name.

    A step a component produced reports the line of the `use:` that pulled it in. Each authored step
    is expanded on its own to count what it becomes; a scenario whose counts do not add up to its
    expanded step list, or whose single-step expansion fails, keeps no lines, since a wrong line
    would be worse than none.
    """
    raw_file = load_scenario_file(text)
    base_dir = path.parent
    resolver = ComponentResolver(
        raw_file.components, root=root or base_dir, base=base_dir, source=path
    )
    counts_by_name = {s.name: len(s.steps) for s in expanded}
    out: dict[str, list[int]] = {}
    for s, raw in zip(raw_file.scenarios, scenario_sources(text), strict=True):
        lines: list[int] = []
        try:
            for step, line in zip(s.steps, raw.step_lines, strict=True):
                probe = s.model_copy(
                    update={"steps": [step], "setup": [], "cleanup": [], "interrupts": []},
                    deep=True,
                )
                expand_components([probe], resolver)
                lines += [line] * len(probe.steps)
        except (OSError, ValueError):
            continue
        counts = {n for name, n in counts_by_name.items() if declared_name(name) == s.name}
        if counts == {len(lines)}:
            out[s.name] = lines
    return out
