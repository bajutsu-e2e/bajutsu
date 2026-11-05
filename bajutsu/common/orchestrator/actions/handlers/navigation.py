"""Navigation handlers: `back` and `navigate`.

Driver-direct like the gestures — no injected device control — but a navigation intent rather than a
gesture, so it lives on its own. Each backend's `back()` performs its platform-correct primitive
(Android system key / iOS OS back button / web history); this handler just dispatches to it (BE-0210).
`navigate` hands its URL to `goto()`, which only a backend advertising `urlNavigation` performs
(BE-0464).
"""

from __future__ import annotations

from bajutsu.common.drivers import base
from bajutsu.common.orchestrator.actions._registry import _handler
from bajutsu.common.scenario import Step
from bajutsu.common.scenario.models.actions import navigate_url_problem


@_handler("back")
def _do_back(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.back is not None
    driver.back()


@_handler("navigate")
def _do_navigate(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.navigate is not None
    # The step arrives already substituted (`_interp_step`), so this is the run-time half of the
    # scheme rule the scenario check applies to token-free values at load time.
    url = step.navigate.url
    if "${" in url:
        raise base.SelectorError(f"navigate: unresolved token in URL {url!r}")
    problem = navigate_url_problem(url)
    if problem is not None:
        raise base.SelectorError(problem)
    driver.goto(url.strip())
