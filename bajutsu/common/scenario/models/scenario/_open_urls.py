"""Reject an `openUrl` the step could never open, when the scenario loads (BE-0487).

Two rules, both run from `Scenario`'s own `model_validator` for the reason `_navigate_urls.py`
gives. A token-free value must pass `open_url_problem`; a value holding a `${...}` token is left to
the runner's `openUrl` handler, which applies the same rule once substituted. And no `openUrl` may
sit inside a `web:` block: a step there runs on the web-context driver, which has no OS to hand a
URL to on any backend, so the rule belongs to the model rather than to a backend's preflight (as
`installApp`'s does, in `_targets.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bajutsu.common.scenario.models.actions.open_url import open_url_problem

from ._functions import _scenario_step_phases

if TYPE_CHECKING:
    from bajutsu.common.scenario.models.steps import Step

    from .scenario import Scenario


def _check_open_urls(scenario: Scenario) -> None:
    """Raise on the first `openUrl` anywhere in *scenario* that breaks either rule."""
    for steps in _scenario_step_phases(scenario):
        _check_steps(steps, in_web=False)


def _check_steps(steps: list[Step], *, in_web: bool) -> None:
    # `_iter_steps` flattens the tree, so this walk carries the one fact it loses: whether a `web:`
    # block encloses the step.
    for step in steps:
        if step.open_url is not None:
            if in_web:
                raise ValueError(
                    "openUrl is not allowed inside a web: block — it hands the URL to the operating "
                    "system, which a web: block's steps never reach"
                )
            if "${" not in step.open_url.url:
                problem = open_url_problem(step.open_url.url)
                if problem is not None:
                    raise ValueError(problem)
        for nested, web in _children(step):
            _check_steps(nested, in_web=in_web or web)


def _children(step: Step) -> list[tuple[list[Step], bool]]:
    """Each nested step list of *step*, paired with whether it is a `web:` block's."""
    if step.if_ is not None:
        return [(step.if_.then, False)] + (
            [(step.if_.else_, False)] if step.if_.else_ is not None else []
        )
    if step.for_each is not None:
        return [(step.for_each.steps, False)]
    if step.web is not None:
        return [(step.web.steps, True)]
    if step.app is not None:
        return [(step.app.steps, False)]
    if step.group is not None:
        return [(step.group.steps, False)]
    if step.steps is not None:
        return [(step.steps, False)]
    return []
