"""One scenario run's verdict and the evidence it produced."""

from __future__ import annotations

from dataclasses import dataclass, field

from bajutsu.common.assertions import AssertionResult
from bajutsu.common.drivers.actuation import Actuation
from bajutsu.common.evidence import Artifact

from .alert_event import AlertEvent
from .skipped_capture import SkippedCapture
from .step_outcome import StepOutcome
from .target_device_info import TargetDeviceInfo


@dataclass
class RunResult:
    scenario: str
    ok: bool
    steps: list[StepOutcome]
    expect_results: list[AssertionResult] = field(default_factory=list)
    failure: str | None = None
    # Scenario-level artifacts (the always-on screen recording, etc.).
    artifacts: list[Artifact] = field(default_factory=list)
    # Which backend (actuator) drove this scenario: "xcuitest" / "fake".
    backend: str = ""
    # The web rendering engine this result was produced on — "chromium" / "firefox" / "webkit"
    # — set only on a `--browsers` cross-engine run (BE-0076). Empty for iOS and any single-engine
    # run, so `backend` stays the actuator and `engine` carries the rendering-engine axis.
    engine: str = ""
    # The scenario's evidence-dir slug under the run dir (`NN-slug`), stamped by the runner that
    # named the dir, so anything cross-linking to that evidence reads the authoritative value
    # instead of re-deriving it (BE-0076). Empty when no evidence dir was written (e.g. tests).
    sid: str = ""
    # The simulator udid this scenario ran on — shows how a parallel pool split the work.
    device: str = ""
    # The simulator's device model / OS runtime (e.g. "iPhone 15" / "iOS 17.2"), for the
    # report's Environment tab; empty when not resolvable (e.g. the fake driver).
    device_name: str = ""
    device_runtime: str = ""
    # The four fields above, plus `backend`/`engine`, once per target a multi-target scenario
    # declared (BE-0428) — each of them describes exactly one target, so on such a run they stay
    # empty here rather than presenting one declared target's values as if they spoke for the whole
    # scenario. The same "empty means not applicable" convention `engine` already uses. An existing
    # reader compiled against today's shape — the JUnit and CTRF exports among them — therefore sees
    # a single-target run exactly as before, and an empty value rather than a misleading one here.
    target_devices: dict[str, TargetDeviceInfo] = field(default_factory=dict)
    # Wall-clock the scenario took end to end (steps + verification), for the report.
    duration_s: float = 0.0
    # The absolute wall-clock instant (epoch seconds) the scenario's video started, corrected by
    # `video_start_offset`. A report subtracts it from a step's or an exchange's `started_at` to get
    # the seconds to seek the recording to (BE-0348); persisted, unlike the monotonic instant this
    # used to be, so that derivation survives the run that produced it.
    video_anchor_s: float = 0.0
    # `video_anchor_s`'s counterpart for every *other* declared target's own scenario-wide video
    # (BE-0428), keyed by target name; empty for a single-target run and absent for a target that
    # recorded no video, the same "empty means not applicable" convention `target_devices` uses.
    # `video_anchor_s` above stays the primary's own anchor, unprefixed, for the same reason
    # `target_devices` leaves the singular device fields alone rather than moving them in here too.
    target_video_anchors: dict[str, float] = field(default_factory=dict)
    # Added to a raw `time.monotonic()` instant from this run to get its wall-clock epoch
    # (`scenario_wall_start - scenario_start`). The network collector stamps monotonic receive times,
    # so `pipeline.py` converts them through this rather than sampling its own wall/monotonic pair at
    # write time — a second pair would drift from every other timestamp on an NTP correction.
    wall_offset_s: float = 0.0
    # System prompts the guard cleared before the scenario-level `expect` re-checked.
    expect_alerts: list[AlertEvent] = field(default_factory=list)
    # Actuations the guard performed for that same expect-phase retry — the one place a gesture happens
    # with no step to attribute it to, so it is recorded here beside `expect_alerts` rather than left in
    # the driver's log and silently discarded.
    expect_actuations: list[Actuation] = field(default_factory=list)
    # A damaged `expect_actuations` record the report loader had to drop — `dropped_actuations`'
    # scenario-level counterpart, since this list has no step of its own to carry the disclosure.
    dropped_expect_actuations: int = 0
    # Evidence kinds the run couldn't supply (no eligible backend) — disclosed, not silent (BE-0020).
    skipped_captures: list[SkippedCapture] = field(default_factory=list)
    # The lifecycle phases' own step outcomes (BE-0392), each numbered from zero and kept beside
    # `steps` rather than folded into it — the separation `expect_results` already gets, and what
    # lets a report show setup and teardown as their own blocks instead of merging them into the
    # scenario's numbered sequence the way a `preconditions.setup` prelude does.
    before_outcomes: list[StepOutcome] = field(default_factory=list)
    after_outcomes: list[StepOutcome] = field(default_factory=list)
    # The verdict the `after` phase dispatched on — "success" / "error", or "" when the scenario
    # declared no `after` rules. Recorded rather than re-derived from `failure`, which by then may
    # also carry a cleanup step's own reason: without it a report cannot tell which rules ran, and
    # so cannot line an outcome up with the rule that produced it.
    after_verdict: str = ""
