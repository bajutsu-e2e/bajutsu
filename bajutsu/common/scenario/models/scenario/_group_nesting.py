"""Reject a `group` step nested where it can never expand.

`Scenario`'s own `model_validator` calls `_check_no_nested_group` once at load time, walking the
as-loaded step tree — the only place a directly-written nesting is visible, since a `use:` step's
own component body isn't resolved yet. A `group` that arrives at a nested position only through a
`use:` call (the component's steps aren't visible here) is instead caught at expansion time, by
`expand()`'s own `group_ctx` check in `bajutsu/common/scenario/expand.py` — the two together cover
every route `expand_components` itself never recurses into (`if.then` / `if.else_` /
`for_each.steps` / `web.steps` / `app.steps`), plus a `group` written directly inside another
`group.steps`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bajutsu.common.scenario.models.steps import Step

    from .scenario import Scenario


def _walk_no_nested_group(steps: list[Step], container: str | None) -> None:
    for step in steps:
        if step.group is not None:
            if container is not None:
                raise ValueError(f"group {step.group.name!r} must not nest inside {container}")
            _walk_no_nested_group(step.group.steps, "a group")
            continue
        if step.if_ is not None:
            _walk_no_nested_group(step.if_.then, "if")
            if step.if_.else_ is not None:
                _walk_no_nested_group(step.if_.else_, "if")
        elif step.for_each is not None:
            _walk_no_nested_group(step.for_each.steps, "forEach")
        elif step.web is not None:
            _walk_no_nested_group(step.web.steps, "web")
        elif step.app is not None:
            _walk_no_nested_group(step.app.steps, "app")


def _check_no_nested_group(scenario: Scenario) -> None:
    """Reject a `group` written directly inside another `group`, `if`, `forEach`, `web`, or `app`.

    Neither shape can ever expand: `expand()` only recurses into `scenario.steps` /
    `scenario.before` / each `after` rule's `steps` / each `interrupts` entry's `steps`, never into
    a control-flow step's own nested list.
    """
    _walk_no_nested_group(scenario.steps, None)
    _walk_no_nested_group(scenario.before, None)
    for rule in scenario.after:
        _walk_no_nested_group(rule.steps, None)
    for entry in scenario.interrupts:
        _walk_no_nested_group(entry.steps, None)
