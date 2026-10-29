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
from collections.abc import Callable
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


def offline_actuator(eff: Effective) -> str | None:
    """The actuator a run of *eff* selects when every one is available, or None for none yet."""
    return next((a for a in resolve_actuators(eff.backend) if a in IMPLEMENTED), None)


def lint_against_targets(
    path: Path,
    effective: Callable[[str], Effective],
    target: str | None,
    *,
    root: Path | None = None,
) -> list[TargetFinding]:
    """Every reason a scenario in *path* cannot run on the targets it would run on.

    Args:
        path: The scenario file, already schema-valid.
        effective: Resolves a target name to its effective config; raises for an unknown name.
        target: The target a scenario declaring no `targets` runs on; None when none was named.
        root: The containment root for component and data refs; the file's own directory if None.

    Raises:
        TargetRequired: A scenario declares no `targets` and *target* is None.
        OSError: A referenced component or CSV cannot be read.
        ValueError: A component or data ref does not expand.
    """
    from bajutsu.common.runner.pipeline import with_lifecycle_phases

    expanded = load_expanded_scenarios(path, root)
    lines = _step_lines(path, root, expanded)
    findings: list[TargetFinding] = []
    for s in expanded:
        names = list(s.target_names) or ([target] if target else [])
        if not names:
            raise TargetRequired(
                f"{s.name}: declares no targets; pass --target <name> to check it against one"
            )
        effs = {name: effective(name) for name in names}
        declared = bool(s.target_names)
        folded = with_lifecycle_phases(effs[names[0]], [s], effs if declared else None)[0]
        findings += [
            TargetFinding(scenario=s.name, target=", ".join(names), message=problem)
            for problem in check_preconditions(s.preconditions, list(effs.values()))
        ]
        grouped = {n for g in s.device_groups if len(g) >= 2 for n in g}
        step_lines = lines.get(declared_name(s.name))
        for name, eff in effs.items():
            findings += _target_findings(
                folded, name, eff, routed=declared, grouped=name in grouped, step_lines=step_lines
            )
    return findings


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


def _step_lines(path: Path, root: Path | None, expanded: list[Scenario]) -> dict[str, list[int]]:
    """Each scenario's authored line per *expanded* main step, keyed by its declared name.

    A step a component produced reports the line of the `use:` that pulled it in. Each authored step
    is expanded on its own to count what it becomes; a scenario whose counts do not add up to its
    expanded step list, or whose single-step expansion fails, keeps no lines, since a wrong line
    would be worse than none.
    """
    text = path.read_text(encoding="utf-8")
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
