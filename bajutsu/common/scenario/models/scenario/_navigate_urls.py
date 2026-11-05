"""Reject a literal `navigate` URL the step could never open, when the scenario loads (BE-0464).

Runs from `Scenario`'s own `model_validator`, never as `Navigate` model validation, for the reason
`_http_extract.py` gives: the run loop rebuilds each step after `${vars.*}` substitution
(`_interp_step`), so a model-level check would turn a token that expands to `javascript:` into an
uncaught `ValidationError` instead of a failed step. A value holding a `${...}` token is therefore
skipped here and checked by the runner's `navigate` handler once substituted, against the same
`navigate_url_problem` rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bajutsu.common.scenario.models.actions.navigate import navigate_url_problem

from ._functions import _iter_steps, _scenario_step_phases

if TYPE_CHECKING:
    from .scenario import Scenario


def _check_navigate_urls(scenario: Scenario) -> None:
    """Raise on the first token-free `navigate` value anywhere in *scenario* that breaks the rule."""
    for steps in _scenario_step_phases(scenario):
        for step in _iter_steps(steps):
            if step.navigate is None or "${" in step.navigate.url:
                continue
            problem = navigate_url_problem(step.navigate.url)
            if problem is not None:
                raise ValueError(problem)
