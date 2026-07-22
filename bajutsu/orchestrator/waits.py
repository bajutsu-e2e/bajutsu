"""Condition waits: poll the screen (or the observed network) until satisfied, never a fixed
sleep — this is what keeps the run deterministic without `sleep`."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from bajutsu import assertions
from bajutsu.drivers import base
from bajutsu.elements import shows_app_ui
from bajutsu.orchestrator.types import AlertEvent, BlockedHandler, Clock, NetworkSource, _no_network
from bajutsu.scenario import Gone, Wait, WaitRequest

_logger = logging.getLogger(__name__)

_POLL = 0.05
_SETTLE_POLLS = 2  # consecutive unchanged polls that count as "settled"

# Mid-wait system-alert guard (BE-0269). A SpringBoard-level prompt collapses the iOS app-scoped tree
# to bare content (`not shows_app_ui`); rather than let a wait burn its whole timeout before the
# end-of-step guard looks, watch the already-fetched poll tree and ask the guard to clear it early.
# A hair above _SETTLE_POLLS: a false positive here costs a real AI-vision call, not a cheap re-poll.
_GUARD_DEBOUNCE_POLLS = 3  # consecutive collapsed polls before acting
# AI-vision calls per wait: caps a persistent false positive / a dismiss that never takes.
_GUARD_MAX_ATTEMPTS = 2
# Min seconds between attempts, so a stuck collapse can't hot-loop the guard.
_GUARD_COOLDOWN = 1.0


@dataclass
class WaitTrace:
    """Poll-by-poll record of a `for` wait, filled in place so a timeout is diagnosable (BE-0231).

    On a first-wait timeout these fields separate the candidate causes: a tree that never became
    non-empty (`first_nonempty_s is None`) points at "nothing rendered / transient-empty"; a
    non-empty tree with `elements_at_timeout` content but a still-unmet target points at "the awaited
    element didn't render / readyWhen mismatch"; a large `first_nonempty_s` points at a slow
    cold-boot render. Pure diagnosis — it never enters a verdict (prime directive 1).
    """

    target: str = ""
    timeout_s: float = 0.0
    polls: int = 0
    first_nonempty_s: float | None = None
    elements_at_timeout: int = 0


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
        raise ValueError(f"{_FLOOR_ENV}={raw!r} is not a valid float") from None


def _effective_timeout(w: Wait) -> float:
    return max(w.timeout, _timeout_floor())


def _exists(elements: list[base.Element], sel: base.Selector) -> bool:
    return len(base.find_all(elements, sel)) >= 1


def _adaptive_sleep(clock: Clock, before: float) -> None:
    """Sleep only the remainder of _POLL after subtracting time already spent (e.g. in query).

    When `driver.query()` is backed by a subprocess (a device-tree dump ≈ 100-300ms or more), the call
    itself already provides sufficient delay and an additional fixed sleep is wasteful."""
    elapsed = clock.now() - before
    remaining = _POLL - elapsed
    if remaining > 0:
        clock.sleep(remaining)


@dataclass
class _AlertGuardGate:
    """Fires the system-alert guard mid-wait when the polled tree stays collapsed (BE-0269).

    Fed each poll's already-fetched tree via `observe`. It is the deterministic trigger only — it
    decides *when* to ask the guard to look, never the wait's pass/fail (prime directive 1). Three
    bounds keep a real AI-vision call rare: a debounce (a transient collapsed frame is ignored), a
    cooldown between attempts, and a hard per-wait attempt ceiling after which it goes inert and the
    wait falls back to polling its own deadline.
    """

    driver: base.Driver
    clock: Clock
    on_blocked: BlockedHandler
    alerts: list[AlertEvent]
    _collapsed_polls: int = 0
    _attempts: int = 0
    _last_attempt: float | None = None
    _gave_up: bool = False

    def observe(self, elements: list[base.Element]) -> None:
        """Inspect one poll's tree; ask the guard to clear a blocking prompt if it's warranted."""
        if self._attempts >= _GUARD_MAX_ATTEMPTS:
            # Attempts are spent and the screen is still collapsed: the dismisses didn't take. Say so
            # once, loudly — otherwise the wait fails with a bare timeout that hides the fact the
            # guard already stepped in and gave up (determinism first: never fail silently).
            if not self._gave_up and not shows_app_ui(elements):
                self._gave_up = True
                _logger.warning(
                    "mid-wait alert guard gave up after %d attempts; the screen still looks blocked "
                    "by a system prompt — falling back to the wait's own timeout",
                    _GUARD_MAX_ATTEMPTS,
                )
            return
        if shows_app_ui(elements):
            self._collapsed_polls = 0
            return
        self._collapsed_polls += 1
        if self._collapsed_polls < _GUARD_DEBOUNCE_POLLS:
            return
        now = self.clock.now()
        if self._last_attempt is not None and now - self._last_attempt < _GUARD_COOLDOWN:
            return
        self._collapsed_polls = 0
        self._last_attempt = now
        self._attempts += 1
        event = self.on_blocked(self.driver)
        if event is not None:
            self.alerts.append(event)


def _wait(
    driver: base.Driver,
    w: Wait,
    clock: Clock,
    network: NetworkSource = _no_network,
    *,
    trace: WaitTrace | None = None,
    on_blocked: BlockedHandler | None = None,
    alerts: list[AlertEvent] | None = None,
) -> tuple[bool, str, list[base.Element] | None]:
    """Condition wait. Polls query() (or the observed network) until satisfied instead
    of a fixed sleep.

    When `trace` is given (a `for` wait only), each poll is recorded into it so a timeout can be
    diagnosed from artifacts (BE-0231 Unit 1); it never changes the wait's outcome.

    When `on_blocked` is given, the branches a system alert can *stall* — `for`, `settled`, and
    `screenChanged` (where a collapsed tree keeps the condition unmet and would otherwise burn the
    whole timeout) — watch the already-fetched tree for the collapsed-tree signature of a blocking
    prompt and ask the guard to clear it mid-wait, then resume polling against the *same* `deadline`
    (BE-0269). The condition check still decides pass/fail; the guard only accelerates recovery, and
    dismissed alerts are appended to `alerts` (the step's outcome list) for the report. `gone` is
    *not* guarded: a collapsed tree already satisfies "gone" and returns at once, so no timeout is
    wasted and there is nothing to accelerate (guarding it would mean redefining "gone" to reject a
    blank screen). `request` polls the network, not the screen, so it is not guarded either.

    Returns `(ok, reason, tree)` where `tree` is the last screen the wait queried — the settled
    device state, since nothing actuates in a wait. The caller reuses it as the step's `after`
    snapshot instead of re-querying (BE-0259). It is `None` for the `request` variant, which polls
    the observed network rather than the tree, so there is no screen read to hand back.
    """
    timeout = _effective_timeout(w)
    start = clock.now()
    deadline = start + timeout
    # Give the gate a real list to record into even when the caller passed none (e.g. a direct
    # _wait() unit test), so the record-the-event path has no None branch. `is not None`, not
    # `or []`: an empty list the caller *did* pass is falsy but must still be the one appended to.
    gate = (
        _AlertGuardGate(
            driver=driver,
            clock=clock,
            on_blocked=on_blocked,
            alerts=alerts if alerts is not None else [],
        )
        if on_blocked is not None
        else None
    )
    if w.for_ is not None:
        target = w.for_.as_selector()
        if trace is not None:
            trace.target = str(target)
            trace.timeout_s = timeout
        while True:
            t0 = clock.now()
            elements = driver.query()
            if trace is not None:
                trace.polls += 1
                if elements and trace.first_nonempty_s is None:
                    trace.first_nonempty_s = t0 - start
            if _exists(elements, target):
                return True, "", elements
            if gate is not None:
                gate.observe(elements)
            if clock.now() >= deadline:
                if trace is not None:
                    trace.elements_at_timeout = len(elements)
                return False, f"wait timeout: for {target} ({timeout}s)", elements
            _adaptive_sleep(clock, t0)
    if isinstance(w.until, Gone):
        # Not guarded (see the docstring): a collapsed tree already satisfies "gone", so this branch
        # returns at once under a system alert rather than burning the timeout — nothing to hurry.
        target = w.until.gone.as_selector()
        while True:
            t0 = clock.now()
            elements = driver.query()
            if not _exists(elements, target):
                return True, "", elements
            if clock.now() >= deadline:
                return False, f"wait timeout: gone {target} ({timeout}s)", elements
            _adaptive_sleep(clock, t0)
    if isinstance(w.until, WaitRequest):
        req = w.until.request
        need = req.count if req.count is not None else 1
        while True:
            t0 = clock.now()
            if assertions.count_matching(network(), req) >= need:
                return True, "", None
            if clock.now() >= deadline:
                label = assertions.request_label(req)
                return False, f"wait timeout: request {label} ({timeout}s)", None
            _adaptive_sleep(clock, t0)
    if w.until == "settled":
        return _wait_settled(driver, deadline, clock, gate)
    # until == "screenChanged"
    before = driver.query()
    if gate is not None:
        gate.observe(before)
    while True:
        t0 = clock.now()
        current = driver.query()
        if current != before:
            return True, "", current
        if gate is not None:
            gate.observe(current)
        if clock.now() >= deadline:
            return False, f"wait timeout: screenChanged ({timeout}s)", current
        _adaptive_sleep(clock, t0)


def _wait_settled(
    driver: base.Driver,
    deadline: float,
    clock: Clock,
    gate: _AlertGuardGate | None = None,
) -> tuple[bool, str, list[base.Element]]:
    """Wait until a non-empty screen stops changing (transition/animation finished).

    A blank/collapsed tree (e.g. a screen mid-render, or one covered by a system
    alert) is never treated as settled. Best-effort: timing out just proceeds with the
    current screen — a settle is a stabilization hint, not a correctness assertion, so
    it never fails the step. When `gate` is given, a screen that stays collapsed (a system alert)
    is cleared mid-settle rather than burning the whole timeout (BE-0269). Returns the last queried
    tree so the caller can reuse it as the step's `after` snapshot (BE-0259).
    """
    previous = driver.query()
    if gate is not None:
        gate.observe(previous)
    stable = 0
    while stable < _SETTLE_POLLS:
        if clock.now() >= deadline:
            return True, "", previous
        t0 = clock.now()
        current = driver.query()
        if gate is not None:
            gate.observe(current)
        if current == previous and any(el["identifier"] for el in current):
            stable += 1
        else:
            stable, previous = 0, current
        _adaptive_sleep(clock, t0)
    return True, "", previous
