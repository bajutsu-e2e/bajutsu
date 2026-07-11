"""Condition waits: poll the screen (or the observed network) until satisfied, never a fixed
sleep — this is what keeps the run deterministic without `sleep`."""

from __future__ import annotations

import os

from bajutsu import assertions
from bajutsu.drivers import base
from bajutsu.orchestrator.types import Clock, NetworkSource, _no_network
from bajutsu.scenario import Gone, Wait, WaitRequest

_POLL = 0.05
_SETTLE_POLLS = 2  # consecutive unchanged polls that count as "settled"

# A lane may raise the floor under a wait's ceiling: a condition wait returns the instant it is
# satisfied, so a larger ceiling never slows a fast backend — it only gives a slow environment
# (e.g. the CI x86_64 software-rendered emulator) time to draw before the step is failed. Set by
# the Android e2e lane so the shared scenarios' `timeout: 5` need not be retuned per backend.
_FLOOR_ENV = "BAJUTSU_MIN_WAIT_TIMEOUT"


def _timeout_floor() -> float:
    raw = os.environ.get(_FLOOR_ENV)
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _effective_timeout(w: Wait) -> float:
    return max(w.timeout, _timeout_floor())


def _exists(elements: list[base.Element], sel: base.Selector) -> bool:
    return len(base.find_all(elements, sel)) >= 1


def _adaptive_sleep(clock: Clock, before: float) -> None:
    """Sleep only the remainder of _POLL after subtracting time already spent (e.g. in query).

    When `driver.query()` is backed by a subprocess (idb describe-all ≈ 100-300ms), the call
    itself already provides sufficient delay and an additional fixed sleep is wasteful."""
    elapsed = clock.now() - before
    remaining = _POLL - elapsed
    if remaining > 0:
        clock.sleep(remaining)


def _wait(
    driver: base.Driver, w: Wait, clock: Clock, network: NetworkSource = _no_network
) -> tuple[bool, str]:
    """Condition wait. Polls query() (or the observed network) until satisfied instead
    of a fixed sleep."""
    timeout = _effective_timeout(w)
    deadline = clock.now() + timeout
    if w.for_ is not None:
        target = w.for_.as_selector()
        while True:
            t0 = clock.now()
            if _exists(driver.query(), target):
                return True, ""
            if clock.now() >= deadline:
                return False, f"wait timeout: for {target} ({timeout}s)"
            _adaptive_sleep(clock, t0)
    if isinstance(w.until, Gone):
        target = w.until.gone.as_selector()
        while True:
            t0 = clock.now()
            if not _exists(driver.query(), target):
                return True, ""
            if clock.now() >= deadline:
                return False, f"wait timeout: gone {target} ({timeout}s)"
            _adaptive_sleep(clock, t0)
    if isinstance(w.until, WaitRequest):
        req = w.until.request
        need = req.count if req.count is not None else 1
        while True:
            t0 = clock.now()
            if assertions.count_matching(network(), req) >= need:
                return True, ""
            if clock.now() >= deadline:
                label = assertions.request_label(req)
                return False, f"wait timeout: request {label} ({timeout}s)"
            _adaptive_sleep(clock, t0)
    if w.until == "settled":
        return _wait_settled(driver, deadline, clock)
    # until == "screenChanged"
    before = driver.query()
    while True:
        t0 = clock.now()
        current = driver.query()
        if current != before:
            return True, ""
        if clock.now() >= deadline:
            return False, f"wait timeout: screenChanged ({timeout}s)"
        _adaptive_sleep(clock, t0)


def _wait_settled(driver: base.Driver, deadline: float, clock: Clock) -> tuple[bool, str]:
    """Wait until a non-empty screen stops changing (transition/animation finished).

    A blank/collapsed tree (e.g. a screen mid-render, or one covered by a system
    alert) is never treated as settled. Best-effort: timing out just proceeds with the
    current screen — a settle is a stabilization hint, not a correctness assertion, so
    it never fails the step.
    """
    previous = driver.query()
    stable = 0
    while stable < _SETTLE_POLLS:
        if clock.now() >= deadline:
            return True, ""
        t0 = clock.now()
        current = driver.query()
        if current == previous and any(el["identifier"] for el in current):
            stable += 1
        else:
            stable, previous = 0, current
        _adaptive_sleep(clock, t0)
    return True, ""
