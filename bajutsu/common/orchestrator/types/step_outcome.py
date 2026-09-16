"""What one step did, as the report and the manifest record it."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common.assertions import AssertionResult
from bajutsu.common.drivers.actuation import Actuation
from bajutsu.common.evidence import Artifact

from .alert_event import AlertEvent


@dataclass
class StepOutcome:
    index: int
    action: str
    ok: bool = True
    reason: str = ""
    duration_s: float = 0.0
    # The absolute wall-clock instant (epoch seconds) the step began, derived from the scenario's
    # anchor pair rather than measured with its own clock read (BE-0348). No video correction is
    # applied here: a report subtracts `RunResult.video_anchor_s` at render time to get the seconds
    # to seek the recording to, so the correction can be recomputed from a manifest read back long
    # after the run instead of being baked in irreversibly.
    started_at: float = 0.0
    assertion_results: list[AssertionResult] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    # System prompts the guard cleared before this step succeeded (usually 0 or 1).
    alerts: list[AlertEvent] = field(default_factory=list)
    # What the driver actually did to the screen during this step, in order: the coordinate a tap
    # injected, the endpoints a swipe travelled, the channel that carried each gesture. Drained from
    # the driver once per step, so a step that ran its body twice (an alert the guard dismissed, then a
    # retry) carries both attempts. Evidence only — nothing on the verdict path reads it.
    actuations: list[Actuation] = field(default_factory=list)
    # How many of this step's actuations are missing from the list above: the driver's bounded log
    # discarding the oldest to make room for later ones (a pathological step, e.g. a `maxScrolls` in
    # the hundreds), a damaged record the report loader had to drop on a later read, or both. Recorded
    # rather than left implicit either way, so the list is never read as more complete than it is.
    dropped_actuations: int = 0
    # The value a `generate` step produced (BE-0377), so a later failure shows which value this run
    # actually used. Evidence only — nothing on the verdict path reads it. None for every other
    # action, and for a `generate` step that failed before it wrote its var.
    generated: str | None = None
    # Set by the step loop's reactive check when a driver positively confirmed the app under test had
    # crashed while settling this step (BE-0424). `pipeline.py` scans every phase's outcomes for it to
    # decide whether to write an `app-crash/` directory. At most one outcome per scenario carries it:
    # a wrapping `if`/`forEach` outcome that settles afterward folds the already-known signal into its
    # own `reason` and leaves this at the default, so the scan never has to choose among several.
    app_crashed: bool = False
    # The platform's own crash evidence, captured synchronously at the moment above — not later, when
    # a teardown `relaunch` in the same scenario's `after` phase could already have moved the launch
    # marker the sweep matches against. Deliberately excluded from `manifest.py`'s `_scenario_dict`:
    # raw `bytes` has no JSON encoding, and the durable copy is the redacted one under `app-crash/`.
    app_crash_artifacts: tuple[tuple[str, bytes], ...] = ()
