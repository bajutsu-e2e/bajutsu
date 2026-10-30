"""Small helpers the scenario-level checks share: YAML shorthand coercions and the step walk."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from bajutsu.common.scenario.models.steps._functions import screen_interrupts

if TYPE_CHECKING:
    from bajutsu.common.scenario.models.steps import Step

    from .scenario import Scenario


def _iter_steps(steps: list[Step]) -> Iterator[Step]:
    """Every step reachable from *steps*, recursing into every nested step list."""
    for step in steps:
        yield step
        if step.if_ is not None:
            yield from _iter_steps(step.if_.then)
            if step.if_.else_ is not None:
                yield from _iter_steps(step.if_.else_)
        elif step.for_each is not None:
            yield from _iter_steps(step.for_each.steps)
        elif step.web is not None:
            yield from _iter_steps(step.web.steps)
        elif step.app is not None:
            yield from _iter_steps(step.app.steps)
        elif step.group is not None:
            yield from _iter_steps(step.group.steps)
        elif step.steps is not None:
            yield from _iter_steps(step.steps)


def _scenario_step_phases(scenario: Scenario) -> list[list[Step]]:
    """The top-level step lists of *scenario*: main steps, setup, each cleanup rule, each interrupt."""
    phases = [scenario.steps, scenario.setup]
    phases.extend(rule.steps for rule in scenario.cleanup)
    phases.extend(entry.steps for entry in screen_interrupts(scenario.interrupts))
    return phases
