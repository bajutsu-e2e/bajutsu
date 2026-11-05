"""Check a scenario's `interrupts` handlers against already-fetched trees mid-step (BE-0314)."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common import assertions
from bajutsu.common.assertions import EvalContext
from bajutsu.common.drivers import base
from bajutsu.common.orchestrator.substitution import _interp_asserts
from bajutsu.common.orchestrator.types import NetworkSource
from bajutsu.common.scenario import Assertion, Interrupt

from ._shared import _ExecSteps, _with_attributes

# Consecutive fires of the *same* interrupt entry allowed within one step's resolution (BE-0314). A
# mis-set entry — a condition its own `steps` never clear — must not hang the run, so after this many
# fires it goes inert and the step falls back to its ordinary outcome. In the spirit of BE-0269's
# `_GUARD_MAX_ATTEMPTS`: a small fixed ceiling that turns "the recovery didn't take" into a clean
# step failure/timeout rather than an infinite loop.
_INTERRUPT_MAX_FIRES = 3


@dataclass
class _InterruptGuard:
    """Checks a scenario's `interrupts` handlers against already-fetched trees mid-step (BE-0314).

    Analogous to `waits._AlertGuardGate`, but for in-tree interstitial screens the assertion DSL can
    see (not out-of-process system alerts): each entry's `condition` is evaluated against a tree the
    loop already holds — a `wait`'s poll tick, or an act step's pre-action read — and its `steps` run
    to clear the screen when it matches, wherever in the sequence it surfaced. It is the deterministic
    trigger only; `condition` is a machine predicate, never a model call (prime directive 1).

    One guard is built per step, so `_fires` — the per-entry consecutive-fire counter — resets each
    step; an entry that keeps matching (its recovery didn't clear it) goes inert at
    `_INTERRUPT_MAX_FIRES` and the step falls through to its ordinary outcome. A non-matching entry
    resets its own counter, so "consecutive" stays honest. `failure` records a recovery step's
    failure so the caller fails the step loudly rather than swallowing it (determinism first).
    """

    interrupts: list[Interrupt]
    driver: base.Driver
    network: NetworkSource
    bindings: dict[str, str]
    run_recovery: _ExecSteps
    _fires: dict[int, int] = field(default_factory=dict)
    failure: str | None = None
    # Conditions interpolated once against the step's bindings (below), not per poll: `observe` runs
    # every wait tick, and re-interpolating a `${...}`-free condition there re-serializes it each time
    # for no change. The bindings a guard sees are the step's, fixed for its lifetime.
    _conditions: list[Assertion] = field(init=False)

    def __post_init__(self) -> None:
        self._conditions = [
            _interp_asserts([e.condition], self.bindings)[0] for e in self.interrupts
        ]

    def _fire_once(self, elements: list[base.Element]) -> bool:
        """Run one pass over the entries against `elements`; return whether any recovery ran."""
        fired = False
        net = self.network()
        for i, condition in enumerate(self._conditions):
            if self.failure is not None:
                return fired
            if self._fires.get(i, 0) >= _INTERRUPT_MAX_FIRES:
                continue
            # An `attribute` condition reads its element beside this tree, so it repeats each tick.
            ctx = _with_attributes(EvalContext(), self.driver, [condition])
            if not assertions.passed(assertions.evaluate(elements, [condition], net, ctx=ctx)):
                self._fires[i] = 0
                continue
            self._fires[i] = self._fires.get(i, 0) + 1
            failure = self.run_recovery(self.interrupts[i].steps, self.driver)
            if failure is not None:
                self.failure = failure
                return fired
            fired = True
        return fired

    def observe(self, elements: list[base.Element]) -> bool:
        """Check the entries against one poll's tree; return whether the wait should abort now.

        A `True` return means a recovery step just failed (`self.failure` is now set): the outcome
        is already decided, so `_wait`/`_wait_settled` end the poll immediately instead of burning
        the rest of the timeout on a wait that can no longer pass — the caller (the run loop) reads
        `self.failure` for the real reason once `_run_step_body` returns (BE-0314)."""
        self._fire_once(elements)
        return self.failure is not None

    def clear_before_act(self, seed: list[base.Element]) -> list[base.Element]:
        """Before a UI act, clear any matching interstitial, re-reading until settled or capped.

        `seed` is the pre-action tree the loop already read for the step (a `screenChanged` step's
        `before`, or the one extra query an interrupts-declaring scenario pays for a bare act). Each
        fired recovery actuates, so the new screen is re-read and re-checked — the loop a `wait` gets
        for free from its own polling.

        Returns the settled pre-act tree — the last one read, so it reflects the screen *after* any
        interstitial was cleared. The caller re-baselines its `screenChanged` `before` from it, so a
        recovery's own screen mutation is not misattributed to the step's action (the return is `seed`
        unchanged when nothing fired).
        """
        elements = seed
        while self.failure is None and self._fire_once(elements):
            elements = self.driver.query()
        return elements
