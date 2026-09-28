"""Reject a duplicate `var` across an `http` step's `extractBody`, or one colliding with `saveBody`.

`Scenario`'s own `model_validator` calls `_check_http_extract_vars` once at load time, walking the
as-loaded step tree — the only place a directly-written duplicate is visible, since a `use:` step's
own component body isn't resolved yet. `expand_components` and `apply_setups` (BE-0428's own
`_check_target_requirements` precedent) each rebuild an already-validated `Scenario` in a way
Pydantic never re-validates, so both call this check again on their own result — a `${params.*}`
substitution or a prepended setup's steps can each reveal a collision the as-loaded tree never
showed. `expand_data`'s per-row `${row.*}` substitution needs no such re-call: `_instantiate_rows`
re-validates each row through `Scenario.model_validate`, which re-fires every `model_validator`
including this one.

This check never runs as `Step` model validation: a per-step validator would re-fire when the run
loop rebuilds the step during `${vars.*}` / `${secrets.*}` substitution (`_interp_step`), raising an
uncaught `ValidationError` in place of the handler's own clean step failure. The runner's `http`
handler instead re-runs `HttpRequest.duplicate_extract_var` itself against the substituted step, so
a collision that substitution alone reveals still fails the step, at run time, with the handler's
own error.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bajutsu.common.scenario.models.actions import HttpRequest
    from bajutsu.common.scenario.models.steps import Step

    from .scenario import Scenario


def _walk_http_requests(steps: list[Step]) -> Iterator[HttpRequest]:
    """Every `http` action reachable from *steps*, recursing into every nested step list."""
    for step in steps:
        if step.http is not None:
            yield step.http
        if step.if_ is not None:
            yield from _walk_http_requests(step.if_.then)
            if step.if_.else_ is not None:
                yield from _walk_http_requests(step.if_.else_)
        elif step.for_each is not None:
            yield from _walk_http_requests(step.for_each.steps)
        elif step.web is not None:
            yield from _walk_http_requests(step.web.steps)
        elif step.app is not None:
            yield from _walk_http_requests(step.app.steps)
        elif step.group is not None:
            yield from _walk_http_requests(step.group.steps)
        elif step.steps is not None:
            yield from _walk_http_requests(step.steps)


def _check_http_extract_vars(scenario: Scenario) -> None:
    """Raise on the first `extractBody`/`saveBody` `var` collision anywhere in *scenario*'s steps."""
    phases = [scenario.steps, scenario.before]
    phases.extend(rule.steps for rule in scenario.after)
    phases.extend(entry.steps for entry in scenario.interrupts)
    for steps in phases:
        for http in _walk_http_requests(steps):
            dup = http.duplicate_extract_var()
            if dup is not None:
                raise ValueError(
                    f"http: var {dup!r} is used more than once across extractBody/saveBody "
                    "on the same step"
                )
