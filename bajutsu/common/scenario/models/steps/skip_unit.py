"""The skip units `skipIfUnsupported` forms, assigned while a scenario expands (BE-0457).

A unit is what the run skips as one: the step carrying the modifier, or every step a covered
`group:`, `use:`, or target group expanded into. Each covered step records its unit's id
(`Step.skip_unit_id`) and reason, so the run can skip a `setLocation` together with the `assert`
that reads the place it set.
"""

from __future__ import annotations

import itertools
from typing import NamedTuple

from .step import Step, _descendants


class SkipUnit(NamedTuple):
    """One skip unit: the author's reason and the id every step in the unit shares."""

    reason: str
    unit_id: int


# Provisional skip-unit ids, handed out while a scenario expands. Module-wide for the same reason as
# `expand.py`'s `_group_id_counter`: a scenario expands across several calls (its own steps, then
# each component it pulls in), and a per-call counter would hand two units the same id. An
# `itertools.count`, not a hand-rolled counter, because its `next()` is atomic under the GIL and
# `bajutsu serve` loads scenarios on several threads at once; a repeated id would merge two units.
# Negative, so a provisional id never meets a final one, which `number_skip_units` makes positive —
# a reloaded `scenario.yaml` carries only final ids.
_provisional = itertools.count(-1, -1)


def opened_unit(step: Step, outer: SkipUnit | None, where: str) -> SkipUnit | None:
    """The unit a `group:`, `use:`, or target group *step* covers its expanded steps with.

    An enclosing unit wins, so a covered `use:` inside a covered `group:` stays one unit; *step*'s
    own reason must then agree with it.
    """
    if outer is not None:
        _check_reason(step, outer, where)
        return outer
    if step.skip_if_unsupported is None:
        return None
    unit_id = step.skip_unit_id if step.skip_unit_id is not None else next(_provisional)
    return SkipUnit(step.skip_if_unsupported, unit_id)


def stamp_unit(step: Step, unit: SkipUnit | None, where: str) -> Step:
    """*step* with *unit*'s reason and id stamped on, refusing a step no unit may cover.

    The load-time validator on `Step` cannot see a step a `use:` pulls in from a component, so the
    `manual` / `setPrimaryTarget` refusal is repeated here, where expansion reaches it.
    """
    if unit is None:
        return step
    _check_reason(step, unit, where)
    if step.set_primary_target is not None:
        raise ValueError(f"{where}: skipIfUnsupported cannot cover a setPrimaryTarget step")
    if step.manual is not None or any(child.manual is not None for child in _descendants(step)):
        raise ValueError(
            f"{where}: skipIfUnsupported cannot cover a manual step — skipping it would hide the "
            "manual stop"
        )
    reason, unit_id = unit
    if step.skip_if_unsupported == reason and step.skip_unit_id == unit_id:
        return step
    return step.model_copy(update={"skip_if_unsupported": reason, "skip_unit_id": unit_id})


def number_skip_units(lists: list[list[Step]]) -> list[list[Step]]:
    """*lists* with every skip unit numbered 1, 2, … in walk order, at any depth.

    A covered step with no unit yet forms its own. The numbering depends on the scenario alone —
    never on what else loaded first — so a dumped scenario hashes the same on every load (BE-0049)
    and two units never share an id. Each list is a phase (`setup`, `steps`, a `cleanup` rule's or an
    `interrupts` entry's steps), numbered in the order given, so a unit stays one across them.

    Raises:
        ValueError: A covered step holds a `manual` step anywhere below it. The check runs here,
            after expansion, because a `use:` inside a covered block's body reaches the component's
            steps only once that body has expanded.
    """
    final: dict[int, int] = {}

    def renumber(step: Step) -> Step:
        updates: dict[str, object] = {}
        if step.skip_if_unsupported is not None:
            if any(child.manual is not None for child in _descendants(step)):
                where = f"step {step.name!r}" if step.name is not None else "a covered step"
                raise ValueError(
                    f"{where}: skipIfUnsupported cannot cover a block holding a manual step — "
                    "skipping the block would hide the manual stop"
                )
            old = step.skip_unit_id if step.skip_unit_id is not None else next(_provisional)
            new = final.setdefault(old, len(final) + 1)
            if new != step.skip_unit_id:
                updates["skip_unit_id"] = new
        for name, block in (("for_each", step.for_each), ("web", step.web), ("app", step.app)):
            if block is not None:
                updates[name] = block.model_copy(
                    update={"steps": [renumber(c) for c in block.steps]}
                )
        if step.group is not None:
            updates["group"] = step.group.model_copy(
                update={"steps": [renumber(c) for c in step.group.steps]}
            )
        if step.if_ is not None:
            else_ = step.if_.else_
            updates["if_"] = step.if_.model_copy(
                update={
                    "then": [renumber(c) for c in step.if_.then],
                    "else_": [renumber(c) for c in else_] if else_ is not None else None,
                }
            )
        return step.model_copy(update=updates) if updates else step

    return [[renumber(step) for step in steps] for steps in lists]


def _check_reason(step: Step, unit: SkipUnit, where: str) -> None:
    own = step.skip_if_unsupported
    if own is not None and own != unit.reason:
        raise ValueError(
            f"{where}: skipIfUnsupported {own!r} conflicts with the enclosing block's "
            f"{unit.reason!r}"
        )
