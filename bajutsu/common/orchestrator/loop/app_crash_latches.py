"""What the step loop remembers across a scenario about the app under test's own liveness (BE-0424)."""

from __future__ import annotations

from dataclasses import dataclass

# The step kinds whose own success is evidence the app under test answered — the positive observation
# a scenario's very first failing step would otherwise lack. Keyed positively, not by excluding the
# obvious offenders: an `if`/`forEach` settles `ok=True` on an empty branch or zero matched elements
# without the app behind the query ever answering (a dead app's SpringBoard-only tree answers a
# `query()` perfectly well), and `http` / `generate` / `totp` / `email` / `push` never reach the app at
# all. So a kind added later defaults to proving nothing, which only leaves the probe suppressed —
# never the reverse. `tapPoint` is out for the same reason: it injects a raw coordinate and succeeds
# whether or not anything is there to receive it. `relaunch` is out too, and handled separately below.
OBSERVES_APP = frozenset(
    {
        "tap",
        "double_tap",
        "long_press",
        "type",
        "select",
        "clear",
        "delete",
        "copy_",
        "select_option",
        "set_picker_value",
        "swipe",
        "drag",
        "scroll",
        "pinch",
        "rotate",
        "wait",
        "assert_",
    }
)

# The one step that deliberately terminates the app, so its failure — and everything settling after
# it — must never be read as a crash the app suffered on its own.
RELAUNCH = "relaunch"


@dataclass
class AppCrashLatches:
    """The three latches bounding the step loop's reactive app-crash probe, scoped to one scenario.

    `run_scenario` creates one and shares it with every `run_phase` call the same way it already
    shares `live_bindings`, so it survives `before`, the main steps, and every dispatched `after`
    rule — each of which builds its own fresh `StepLoopState` around it.
    """

    # A `relaunch` step failed, so the app's state is whatever that step left behind. Suppresses every
    # later probe in this scenario unconditionally: the failing step's own wrapping `if`/`forEach`
    # outcomes and any `after: on: error` cleanup step would each otherwise probe fresh and read
    # `notRunning` — honest, and caused by the check's own scenario rather than by a defect.
    deliberate_termination: bool = False
    # The app has not been observed answering since it was last launched. Seeded from the lease's own
    # readiness gate, because a `ready` answer on the bare-count rung cannot tell the app apart from
    # SpringBoard's icons — and that rung is the *ordinary* one for any target declaring no
    # `readyWhen`. Unlike the flag above it clears, on the first step whose success required the app to
    # answer: latching it for a whole scenario would disable this check outright for that whole class
    # of targets, not merely protect their first step.
    unconfirmed_launch: bool = False
    # The signal a driver already confirmed, so a later outcome in the same propagation folds it into
    # its own `reason` instead of probing again. Only the first, confirming outcome sets
    # `app_crashed` and captures artifacts — which is what keeps `pipeline.py`'s later scan
    # unambiguous. Deliberately not latched on an *unconfirmed* answer: a `None` from one step teaches
    # nothing about whether the next step's own failure is a crash.
    confirmed_signal: str | None = None
