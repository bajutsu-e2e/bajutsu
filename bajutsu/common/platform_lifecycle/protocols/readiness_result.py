"""How the post-launch readiness gate ended, for the wait-timeout diagnostic (BE-0231)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ReadinessResult:
    """The outcome of the post-launch readiness gate, for the wait-timeout diagnostic (BE-0231).

    When the first scenario `wait` times out, this says whether the gate had declared the app ready
    and on which signal — the evidence that separates "the gate returned before the content the
    scenario needs" from "the content rendered, then the awaited element didn't".

    BE-0424 added a second consumer, and it is the first to *decide* from this rather than word a
    message: the step loop reads `ready` and `signal` to tell an app that never reached the
    foreground from one that was running and then crashed, which decides whether its reactive
    app-crash probe runs on a scenario's very first failing step. Still not a verdict (prime
    directive 1) — it gates a diagnostic probe, and the probe itself never decides pass/fail either.
    """

    ready: bool
    signal: Literal["screenChanged", "readyWhen", "namespace", "count", "timeout"]
    elapsed_s: float
    # Whether the tree stopped changing before the gate returned. A signal fires as soon as the app's
    # first content appears, which can be mid-transition — and a touch synthesized into a screen still
    # moving is the one the Simulator drops, surfacing much later as an unrelated step's wait timeout.
    # False says the gate spent its settle budget without two matching samples and returned on the
    # signal alone, which is the state to suspect when such a timeout follows. Diagnostic like
    # `signal`: it never enters a verdict (prime directive 1).
    settled: bool = True
