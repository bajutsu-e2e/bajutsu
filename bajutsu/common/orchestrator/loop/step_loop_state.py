"""The mutable context a run's step loop carries across its recursive descent."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common.drivers import base
from bajutsu.common.evidence import Artifact
from bajutsu.common.orchestrator.types import SelectionState, StepOutcome

from ._step_counter import _StepCounter
from .app_crash_latches import AppCrashLatches


@dataclass
class StepLoopState:
    """The mutable context a run's step loop carries across its recursive descent.

    Aggregates what the loop used to smuggle through closure `nonlocal`s and free variables, so a
    single object threads the shared state through nested `if` / `forEach` / `web` groups and an
    interrupt's recovery — all of which run through the same step loop and must see the same
    counter, outcomes, bindings, and screen-read bookkeeping.
    """

    counter: _StepCounter
    outcomes: list[StepOutcome]
    # `bindings` is a mutable dict (guaranteed by `run_scenario`) — extract steps add `vars.*`
    # entries so that subsequent steps and scenario-level `expect` can reference them.
    bindings: dict[str, str]
    # Scenario-scoped like `bindings`, and for the same reason: `run_scenario` creates one and hands
    # every phase the same object, so what a failing `relaunch` in `steps` latched still suppresses
    # the probe for an `after: on: error` rule dispatched afterward (BE-0424).
    app_crash: AppCrashLatches
    # One selection tracker per run, shared across the recursive step loop (like `_StepCounter`), so
    # a `copy` sees the selection a prior `select` left — and any action in between clears it (BE-0265).
    selection: SelectionState = field(default_factory=SelectionState)
    # `prev_after` carries a step's post-step tree to the next step's `before` (BE-0234 Unit 2):
    # nothing actuates between the two, so they observe the same device state and the `before` read
    # is skipped. It holds only a tree we actually read; a step that took no read leaves it None so
    # the next `before` reads fresh, and a `web` block resets it (the tree is a different driver's).
    prev_after: list[base.Element] | None = None
    # The previous step's `after.png` artifact, reused as this step's `before.png` (BE-0407 Unit 1)
    # instead of a fresh `driver.screenshot()`: nothing actuates between the two, so they are the
    # identical pixels. Set only when the shutter actually wrote a screenshot (a `NullSink` writes
    # nothing, so this stays `None` under it); `_handle_action` additionally forces it to `None` for
    # a recovery step, a `handleSystemAlert` step, or any scenario declaring `interrupts` — the
    # cases where an asynchronous interstitial could have arrived since, which the "nothing
    # actuated" premise does not rule out. Unlike `prev_after`, a `web` block does *not* reset this:
    # the shutter always targets the native driver, so the screen it captures is the same one
    # throughout, web block or not.
    prev_after_screenshot: Artifact | None = None
    total_reads: int = 0  # runner-issued screen reads, the BE-0234 read-count yardstick (Unit 1)
    # True while an interrupt's own recovery steps run (BE-0314). Those steps go through the step loop
    # too, so without this an interrupt whose recovery targets the very screen its `condition` matches
    # would re-trigger itself on each recovery step and recurse without end. Suppressing the guard
    # while recovery runs also keeps a run's interrupt handling to the outermost screen, not the
    # handlers reacting to each other mid-recovery.
    running_recovery: bool = False
    # When the proactive notification-banner sweep (BE-0416 Unit 8) last actually queried the
    # backend, in `clock.now()` units — `None` before the run's first step. Rate-limits that query
    # to the resolved poll interval, decoupled from how often a step runs, the same way
    # `_AlertGuardGate` rate-limits its own native probe (BE-0315).
    last_notification_banner_poll_at: float | None = None
