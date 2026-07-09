"""Navigation handlers: `back`.

Driver-direct like the gestures — no injected device control — but a navigation intent rather than a
gesture, so it lives on its own. Each backend's `back()` performs its platform-correct primitive
(Android system key / iOS OS back button / web history); this handler just dispatches to it (BE-0210).
"""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.orchestrator.actions._registry import _handler
from bajutsu.scenario import Step


@_handler("back")
def _do_back(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.back is not None
    driver.back()
