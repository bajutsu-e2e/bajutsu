"""Walk a step list into every body a step nests, for checks that must see each step (BE-0452)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from bajutsu.common.scenario.models import Step

# Where a step nests further steps, as (model attribute path, YAML key path). `nested_steps` walks
# the models by the first and a template file's `use` rewrite (BE-0471) walks raw YAML by the second,
# so a container added here reaches both. A target group (BE-0437) is never flattened in a config, so
# its own `steps` count too.
STEP_CONTAINERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("steps",), ("steps",)),
    (("if_", "then"), ("if", "then")),
    (("if_", "else_"), ("if", "else")),
    (("for_each", "steps"), ("forEach", "steps")),
    (("web", "steps"), ("web", "steps")),
    (("app", "steps"), ("app", "steps")),
)


def nested_steps(steps: list[Step]) -> Iterator[Step]:
    """Every step of *steps*, each followed by the steps its bodies hold, depth first."""
    for step in steps:
        yield step
        for attrs, _ in STEP_CONTAINERS:
            nested: Any = step
            for attr in attrs:
                nested = getattr(nested, attr, None)
            yield from nested_steps(nested or [])


def reject_scenario_only(steps: list[Step], where: str, how: str = "") -> None:
    """Fail a step that has a meaning only inside one scenario's own steps.

    `group:` folds steps under a report heading of one scenario, and `setMocks` arms the control
    channel `run` sets up per scenario (BE-0365 unit 4); neither has a meaning for steps that wrap
    many scenarios, so both fail at load rather than reach the step loop unarmed.

    Raises:
        ValueError: A step of *steps* is a group or sets mocks.
    """
    if any(s.group is not None or s.report_group is not None for s in steps):
        raise ValueError(
            f"{where} cannot use a group (`group`){how}: a group belongs to one scenario's report"
        )
    if any(s.set_mocks is not None for s in steps):
        raise ValueError(
            f"{where} cannot use setMocks{how}: a stub table belongs to one scenario, so "
            "replace it from that scenario's own steps"
        )
