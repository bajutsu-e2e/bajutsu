"""Condition waits: poll the screen (or the observed network) until satisfied, never a fixed
sleep — this is what keeps the run deterministic without `sleep`."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from bajutsu import assertions
from bajutsu.drivers import base
from bajutsu.elements import shows_app_ui
from bajutsu.evidence.network import TransitionSource, _no_transitions
from bajutsu.orchestrator.types import (
    AlertEvent,
    AlertGuardConfig,
    Clock,
    NetworkSource,
    _no_network,
)
from bajutsu.scenario import Gone, Wait, WaitRequest

_logger = logging.getLogger(__name__)

_POLL = 0.05
_SETTLE_POLLS = 2  # consecutive unchanged polls that count as "settled" (tree-diff fallback)
# Quiescence window for the signal-based settle path (BE-0310): once no further screen-transition
# has been reported for this long, the last transition is taken as finished. Short by design —
# `viewDidAppear` already fires *after* the appearance transition completes, so this only smooths
# over a chained transition posting more than one report in quick succession, not a whole
# animation's duration.
_TRANSITION_QUIESCENCE = 0.3

# Min seconds between live "still waiting …" lines. Waits poll every _POLL (50ms); a per-poll
# progress line would flood the run log, so the heartbeat below throttles it to a readable cadence.
# 5s keeps the countdown legible rather than a per-second scroll.
_TICK_INTERVAL = 5.0

# Emits a run-log line while a wait is pending; the float is the seconds left before timeout. Bound
# by the caller (the run loop) to prefix the scenario/step and format the condition.
WaitTick = Callable[[float], None]


def describe_wait(w: Wait) -> str:
    """A human-readable description of what a wait is blocked on, for live progress.

    Renders the condition as `key=value` — `for id='home.title'`, `until gone id='spinner'`,
    `until request GET /login`, `until settled` / `until screenChanged` — reusing the assertion
    report's `sel_str` so a pending line and an assertion detail render a selector the same way.
    (`_wait`'s timeout reason prints the raw selector dict, so it is not byte-identical to this.)
    """
    if w.for_ is not None:
        return f"for {assertions.sel_str(w.for_)}"
    if isinstance(w.until, Gone):
        return f"until gone {assertions.sel_str(w.until.gone)}"
    if isinstance(w.until, WaitRequest):
        return f"until request {assertions.request_label(w.until.request)}"
    return f"until {w.until}"  # "settled" | "screenChanged"


@dataclass
class _Heartbeat:
    """Throttled emitter of "still waiting …" lines so a pending wait shows what it awaits.

    Purely a display aid, fed the poll clock via `tick`: it never reads the tree or influences the
    wait's pass/fail (prime directive 1). The first `tick` always fires, so even a wait that resolves
    on its first poll surfaces its condition once; later ticks are spaced by `_TICK_INTERVAL`.
    """

    emit: WaitTick
    deadline: float
    _next: float = 0.0  # clock time of the next allowed emit; 0 => fire on the first tick

    def tick(self, now: float) -> None:
        if now >= self._next:
            self._next = now + _TICK_INTERVAL
            self.emit(max(0.0, self.deadline - now))


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
    """Fires the system-alert guard mid-wait (BE-0269; native path BE-0315).

    Fed each poll's already-fetched tree via `observe`. It is the deterministic trigger only — it
    decides *when* to act, never the wait's pass/fail (prime directive 1).

    On a backend advertising `HANDLE_SYSTEM_ALERT` it prefers the native path (BE-0315): it reads
    BE-0316's SpringBoard query (`system_alert_labels`) on its own wall-clock interval
    (`guard.poll_interval`, decoupled from the wait's `_POLL`) and taps a policy-named button the
    moment a poll finds one — no debounce, cooldown, or attempt ceiling, because a native query
    reports a fact (not the collapsed-tree proxy's correlation, so no transient-frame false positive)
    and the fixed interval already rate-limits the cross-process query. A definitive "no alert" even
    suppresses the vision path's false positive. This resolves the tension BE-0316 recorded for
    keeping the guard reactive — a native query is not a model call, so "a passing scenario never
    calls the model" still holds.

    Where the backend lacks the capability — or an alert is up but no policy label resolves — it
    falls back to the vision path, whose three bounds keep a real AI-vision call rare: a debounce (a
    transient collapsed frame is ignored), a cooldown between attempts, and a hard per-wait attempt
    ceiling after which it goes inert and the wait falls back to polling its own deadline.
    """

    driver: base.Driver
    clock: Clock
    guard: AlertGuardConfig
    alerts: list[AlertEvent]
    _native: bool = field(init=False)
    _last_native: float | None = None
    _collapsed_polls: int = 0
    _attempts: int = 0
    _last_attempt: float | None = None
    _gave_up: bool = False

    def __post_init__(self) -> None:
        self._native = base.Capability.HANDLE_SYSTEM_ALERT in self.driver.capabilities()

    def observe(self, elements: list[base.Element]) -> None:
        """Inspect one poll; clear a blocking system prompt if warranted (native first, then vision)."""
        if self._native:
            self._observe_native()
        else:
            self._observe_vision(elements)

    def _observe_native(self) -> None:
        # Poll the native presence query on its own wall clock, not every `_POLL`: a per-tick
        # cross-process SpringBoard query would roughly double the single-main-thread runner's load
        # (BE-0315). `_last_native` starts None so the first poll fires at once, bounding detection
        # latency to one interval.
        now = self.clock.now()
        if self._last_native is not None and now - self._last_native < self.guard.poll_interval:
            return
        self._last_native = now
        state, event = self.guard.probe_native(self.driver)
        if state == "dismissed" and event is not None:
            self.alerts.append(event)
        elif state == "unhandled":
            # An alert is up but no policy label resolves — an unknown button, or a query that could
            # not name it. Fall back to the vision guard, bounded by the same cooldown / attempt
            # ceiling as the vision path so a persistently unhandled alert cannot fire an unbounded
            # stream of AI-vision calls.
            self._fire_vision_bounded()
        # "absent" is a deterministic no-alert fact — do nothing, which also suppresses the vision
        # path's transient-frame false positive.

    def _observe_vision(self, elements: list[base.Element]) -> None:
        """The collapsed-tree-proxy + vision path for a backend without the native capability."""
        if shows_app_ui(elements):
            self._collapsed_polls = 0
            return
        self._collapsed_polls += 1
        if self._collapsed_polls < _GUARD_DEBOUNCE_POLLS:
            return
        self._collapsed_polls = 0
        self._fire_vision_bounded()

    def _fire_vision_bounded(self) -> None:
        """Fire the vision guard under the shared per-wait bounds (cooldown + attempt ceiling).

        Shared by the collapsed-tree path and the native "unhandled" fallback so an AI-vision call
        stays rare on both: a cooldown spaces attempts and a hard ceiling stops them, after which it
        goes inert once (loudly) and the wait falls back to its own timeout (never fail silently).
        """
        if self._attempts >= _GUARD_MAX_ATTEMPTS:
            if not self._gave_up:
                self._gave_up = True
                _logger.warning(
                    "mid-wait alert guard gave up after %d vision attempts; the prompt still looks "
                    "blocking — falling back to the wait's own timeout",
                    _GUARD_MAX_ATTEMPTS,
                )
            return
        now = self.clock.now()
        if self._last_attempt is not None and now - self._last_attempt < _GUARD_COOLDOWN:
            return
        self._last_attempt = now
        self._attempts += 1
        event = self.guard.vision(self.driver)
        if event is not None:
            self.alerts.append(event)


def _wait(
    driver: base.Driver,
    w: Wait,
    clock: Clock,
    network: NetworkSource = _no_network,
    *,
    trace: WaitTrace | None = None,
    alert_guard: AlertGuardConfig | None = None,
    alerts: list[AlertEvent] | None = None,
    on_tick: WaitTick | None = None,
    transitions: TransitionSource = _no_transitions,
    on_interrupt_poll: Callable[[list[base.Element]], bool] | None = None,
) -> tuple[bool, str, list[base.Element] | None]:
    """Condition wait. Polls query() (or the observed network) until satisfied instead
    of a fixed sleep.

    When `trace` is given (a `for` wait only), each poll is recorded into it so a timeout can be
    diagnosed from artifacts (BE-0231 Unit 1); it never changes the wait's outcome.

    When `alert_guard` is given, the branches a system alert can *stall* — `for`, `settled`, and
    `screenChanged` (where a collapsed tree keeps the condition unmet and would otherwise burn the
    whole timeout) — drive the guard mid-wait, then resume polling against the *same* `deadline`. On
    an iOS backend the guard queries SpringBoard natively on its own interval (BE-0315, reusing
    BE-0316's primitive); elsewhere it watches the already-fetched tree for the collapsed-tree
    signature of a blocking prompt and asks the vision guard to clear it (BE-0269). The condition
    check still decides pass/fail; the guard only accelerates recovery, and dismissed alerts are
    appended to `alerts` (the step's outcome list) for the report. `gone` is
    *not* guarded: a collapsed tree already satisfies "gone" and returns at once, so no timeout is
    wasted and there is nothing to accelerate (guarding it would mean redefining "gone" to reject a
    blank screen). `request` polls the network, not the screen, so it is not guarded either.

    When `on_interrupt_poll` is given, it is called with each poll's already-fetched tree — after
    the wait's own condition is checked, so it fires only while the wait is still blocked — so a
    scenario's `interrupts` handlers can clear an interstitial screen mid-wait (BE-0314). Like the
    alert guard, it rides on the poll the wait already performs (zero extra query) and resumes
    against the *same* `deadline`; the `gone`/`request` branches are not hooked (a collapsed tree
    already satisfies `gone`, and `request` polls the network, not the screen). A `True` return ends
    the wait immediately (skipping the `deadline` check) rather than burning the rest of the
    timeout: an interrupt's own recovery `steps` can fail, and that failure is already decided by
    the first poll that hits it, so polling on would only turn a fast, loud failure into a slow one.
    The caller (the run loop) knows the real reason and overrides the placeholder this returns.

    When `on_tick` is given, a throttled "still waiting …" line is emitted while the wait is pending:
    once on entry — so even an instantly-satisfied wait surfaces its condition — then every
    `_TICK_INTERVAL` until it resolves. It is display only and never affects the outcome.

    `transitions` (BE-0310) is the `settled` branch's read-only screen-transition signal; the
    default reports none, so `settled` keeps its unchanged tree-diff behavior unless a caller passes
    a real source.

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
            guard=alert_guard,
            alerts=alerts if alerts is not None else [],
        )
        if alert_guard is not None
        else None
    )
    hb = _Heartbeat(on_tick, deadline) if on_tick is not None else None
    if hb is not None:
        # Fire once up front so the awaited condition is shown even for a wait that resolves on its
        # first poll (the common fast case), before any per-loop tick has had a chance to run.
        hb.tick(start)
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
            if on_interrupt_poll is not None and on_interrupt_poll(elements):
                return False, "interrupt recovery failed", elements
            if clock.now() >= deadline:
                if trace is not None:
                    trace.elements_at_timeout = len(elements)
                return False, f"wait timeout: for {target} ({timeout}s)", elements
            if hb is not None:
                hb.tick(clock.now())
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
            if hb is not None:
                hb.tick(clock.now())
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
            if hb is not None:
                hb.tick(clock.now())
            _adaptive_sleep(clock, t0)
    if w.until == "settled":
        return _wait_settled(
            driver, deadline, clock, gate, hb, transitions, on_interrupt_poll, start
        )
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
        if on_interrupt_poll is not None and on_interrupt_poll(current):
            return False, "interrupt recovery failed", current
        if clock.now() >= deadline:
            return False, f"wait timeout: screenChanged ({timeout}s)", current
        if hb is not None:
            hb.tick(clock.now())
        _adaptive_sleep(clock, t0)


def _wait_settled(
    driver: base.Driver,
    deadline: float,
    clock: Clock,
    gate: _AlertGuardGate | None = None,
    hb: _Heartbeat | None = None,
    transitions: TransitionSource = _no_transitions,
    on_interrupt_poll: Callable[[list[base.Element]], bool] | None = None,
    start: float = 0.0,
) -> tuple[bool, str, list[base.Element]]:
    """Wait until a non-empty screen stops changing (transition/animation finished).

    When `transitions` has reported a screen-transition event *since this wait began*
    (`events[-1][1] >= start`), settled is a positive signal — no further transition reported for
    `_TRANSITION_QUIESCENCE` — rather than an inference from tree reads; see
    `_wait_settled_by_signal`. This is re-checked on every poll, not only at entry: `viewDidAppear`'s
    report is POSTed fire-and-forget *after* the appearance animation, so for the canonical
    tap → navigate → `settled` step it lands a few hundred ms into the wait, not before it — the wait
    switches onto the signal path the instant that report arrives, mirroring the readiness gate's
    per-tick re-read. The since-start guard mirrors the one the readiness gate applies to the same
    signal (BE-0310): a transition left over from a *prior* step (the collector is scenario-scoped,
    not per-wait) predates `start`, so it is ignored rather than settling this wait instantly and
    missing the current step's own transition. Until a since-start transition is observed (the app
    doesn't link the observer, or its report is still in flight), this runs the original tree-diff
    behavior, which waits the animation out: a blank/collapsed tree (e.g. a screen mid-render, or one
    covered by a system alert) is never treated as settled, and settled is two consecutive unchanged
    polls with an identified element. Both paths are best-effort: timing out
    just proceeds with the current screen — a settle is a stabilization hint, not a correctness
    assertion, so it never fails the step. When `gate` is given, a screen that stays collapsed (a
    system alert) is cleared mid-settle rather than burning the whole timeout (BE-0269). When `hb` is
    given, it emits the throttled "still waiting …" progress line while settling. Returns the last
    queried tree so the caller can reuse it as the step's `after` snapshot (BE-0259).

    A `True` from `on_interrupt_poll` ends the settle immediately (BE-0314) — a failed interrupt
    recovery is a decided outcome the caller (the run loop) fails the step on, so polling toward
    settled would only delay a failure that best-effort settling would otherwise mask.
    """
    previous = driver.query()
    if gate is not None:
        gate.observe(previous)
    stable = 0
    while stable < _SETTLE_POLLS:
        # A qualifying transition can land mid-wait, not only before it: `viewDidAppear`'s
        # fire-and-forget report arrives *after* the appearance animation, so for the canonical
        # tap → navigate → `settled` step it lands a few hundred ms into this wait rather than at
        # entry. Re-consult every poll — like the readiness gate — and switch to the signal path the
        # instant a since-start transition appears; until then the tree-diff loop below waits the
        # animation out. A left-over transition from a prior step predates `start`, so it is ignored.
        events = transitions()
        if events and events[-1][1] >= start:
            return _wait_settled_by_signal(
                driver, deadline, clock, gate, hb, transitions, events[-1][1], on_interrupt_poll
            )
        if clock.now() >= deadline:
            return True, "", previous
        t0 = clock.now()
        current = driver.query()
        if gate is not None:
            gate.observe(current)
        if on_interrupt_poll is not None and on_interrupt_poll(current):
            return False, "interrupt recovery failed", current
        if current == previous and any(el["identifier"] for el in current):
            stable += 1
        else:
            stable, previous = 0, current
        if hb is not None:
            hb.tick(clock.now())
        _adaptive_sleep(clock, t0)
    return True, "", previous


def _wait_settled_by_signal(
    driver: base.Driver,
    deadline: float,
    clock: Clock,
    gate: _AlertGuardGate | None,
    hb: _Heartbeat | None,
    transitions: TransitionSource,
    last: float,
    on_interrupt_poll: Callable[[list[base.Element]], bool] | None = None,
) -> tuple[bool, str, list[base.Element]]:
    """The signal-based settle path (BE-0310): quiescence since the last observed transition.

    "No further screen-change transition reported for `_TRANSITION_QUIESCENCE`" is a positive "the
    last transition has finished and no new one started," not "two reads happened to match" — the
    window restarts each time a fresh transition is observed. `last` is the most recent transition's
    receive time, already fetched by the caller (`_wait_settled`) to confirm at least one had been
    reported. A collector only ever appends in receive order, so it stays non-empty and its final
    element is always the newest — later reads take `transitions()[-1][1]` rather than scanning for
    a max.

    A `True` from `on_interrupt_poll` ends the settle immediately (BE-0314), same as the tree-diff
    fallback above — the signal path is still a settle loop over `driver.query()`, so a scenario's
    `interrupts` handlers apply here too, not only when no transition signal is available.
    """
    # Diagnostic only (BE-0310 Unit 5): confirms the signal path actually decided settled on a real
    # device, so on-device verification needs no extra instrumentation to observe it.
    _logger.debug(
        "settled via the screen-transition signal (quiescence=%ss)", _TRANSITION_QUIESCENCE
    )
    current = driver.query()
    if gate is not None:
        gate.observe(current)
    while clock.now() - last < _TRANSITION_QUIESCENCE:
        if clock.now() >= deadline:
            return True, "", current
        if on_interrupt_poll is not None and on_interrupt_poll(current):
            return False, "interrupt recovery failed", current
        t0 = clock.now()
        last = transitions()[-1][1]
        current = driver.query()
        if gate is not None:
            gate.observe(current)
        if hb is not None:
            hb.tick(clock.now())
        _adaptive_sleep(clock, t0)
    return True, "", current
