**English** · [日本語](BE-0348-absolute-timestamp-recording-ja.md)

# BE-0348 — Record video, step, and network timestamps as absolute wall-clock time

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0348](BE-0348-absolute-timestamp-recording.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0348") |
| Implementing PR | [#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538) |
| Topic | Verification & coverage |
| Related | [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) gave each scenario's video
[interval](../../docs/glossary.md#evidence-capturepolicy-trace-triage) a confirmed start
(`Interval.true_start`). It anchored every step's and network exchange's report timestamp to that
start through a single offset, resolved once, in-process, from `time.monotonic()`.

This item keeps that anchoring mechanism but changes what the run records. Today the run bakes an
already-relative "seconds from the video's start" number into `StepOutcome.started_at` and
`network.json`'s `startedAt` while the scenario runs. Instead, every event records the absolute
wall-clock instant it happened. The report computes the relative offset its video seek bar needs
once, at render time, from those absolute values.

## Motivation

Two related problems motivate this item.

**1. A confirmation-timeout regression re-opens the exact drift BE-0346 fixed.**
`_await_video_file_growing` / `_await_screenrecord_started` (`bajutsu/evidence/intervals.py`)
confirm `true_start` by polling to a fixed `_VIDEO_START_TIMEOUT = 5.0` deadline. That deadline is a
hardcoded constant. Its four sibling CI-sensitive timeouts in
`bajutsu/platform_lifecycle/environments/xcuitest.py` (`BAJUTSU_XCUITEST_STARTUP_TIMEOUT` and its
three siblings) are all overridable by environment variable, and `.github/workflows/ios-e2e.yml`
raises them because a loaded macOS CI runner is measurably slower than a local one. A CI run
recently hit exactly this:

```
recordVideo produced no new bytes in runs/20260806-073350/02-filter-narrows-the-catalog/scenario.mp4
within 5.0s; step/network timestamps stay uncorrected for this scenario's video
```

When the poll times out, `true_start` stays `None`. By design, `_resolve_video_start_offset`
(`bajutsu/orchestrator/loop.py`) then falls back to `0.0` rather than guess. That scenario's report
timestamps then regress, with no warning in the report, to the naive `step_start - run_start` drift
that BE-0346 exists to prevent. `_VIDEO_START_TIMEOUT` is the one timeout in this family that never
received the env-var-override-plus-CI-tuning treatment its siblings already have.

**2. `RunResult.video_anchor_s` is deliberately unrecoverable once the process exits.** The field is
a raw `time.monotonic()` instant. `manifest_dict` (`bajutsu/report/manifest.py`) explicitly excludes
it from the persisted `manifest.json`, since a monotonic value means nothing once read back in a
different process. The consequence is that the relative offset baked into `StepOutcome.started_at`
and `network.json`'s `startedAt` while the scenario runs is the *only* copy of the timing
relationship that survives.

Suppose that offset was computed from a `None` `true_start` (problem 1), from a wrong assumption in
the offset arithmetic, or before a future improvement to how the anchor is resolved. Then no one can
recompute the scenario's report timestamps afterward without re-running the scenario. The raw timing
data a correct answer would need was never kept.

Both problems trace to the same root. Today's design computes a correction once, in-process, during
the run, and keeps only the derived number, never the raw data it came from.

## Detailed design

**Unit 1 — a wall-clock anchor, injected, not folded into `Clock`.** `run_scenario`
(`bajutsu/orchestrator/loop.py`) already stamps `scenario_start = clock.now()`, a `time.monotonic()`
instant used for every in-run duration and timeout decision. That stays exactly as it is, because a
wall clock can jump backward on an NTP correction, and nothing that decides whether a `wait` timed
out may use one.

Add a second, independent stamp, `scenario_wall_start`. It comes from an injectable callable
(`WallClock = Callable[[], float]`, defaulting to `time.time`). This follows the codebase's existing
convention for a single injected function (`Spawn`, `adb.RunFn` in
`bajutsu/evidence/intervals.py`) instead of extending the `Clock` Protocol. Extending `Clock` would
force every `FakeClock` / `TrackingClock` / `_LogicalClock` / `_AdvancingClock` test double across
the suite (roughly a dozen files) to grow a method none of them need for timing logic.

Any later monotonic instant `t` converts to wall-clock via
`scenario_wall_start + (t - scenario_start)`. This is a pure derived value, so a test can hold the
wall-clock callable fixed and assert on it directly.

**Unit 2 — `StepOutcome.started_at` becomes an absolute epoch timestamp.** `_StepRunner._run_one`
(`bajutsu/orchestrator/loop.py`) currently computes
`outcome.started_at = max(0.0, (start - scenario_start) - video_start_offset)`, a relative offset
corrected at record time. It instead computes
`outcome.started_at = scenario_wall_start + (start - scenario_start)`. The result is the step's
absolute wall-clock start, with no video correction applied yet (that moves to Unit 5).

**Unit 3 — `RunResult.video_anchor_s` becomes the absolute anchor, and stops being excluded from the
manifest.** `_resolve_video_start_offset` keeps its existing monotonic-arithmetic logic and its
existing fallback rules unchanged: no confirmed `true_start` yields `0.0`, and a positive offset
yields `0.0` with a log line. That logic is proven, and recording its result differently does not
require changing it.

Only the value `RunResult.video_anchor_s` carries changes. Instead of
`scenario_start + video_start_offset` (a monotonic instant), it becomes
`scenario_wall_start + video_start_offset` (an absolute wall-clock instant). `video_start_offset` is
a plain second-denominated delta, valid to add to either epoch.

`manifest_dict` (`bajutsu/report/manifest.py`) drops the `d.pop("video_anchor_s", None)` line that
excludes the field today. The value is now meaningful once persisted and re-read, which is the point
of this item.

**Unit 4 — network exchange timestamps store absolute instants.** `bajutsu/runner/pipeline.py`'s
`_write_network` currently writes `startedAt` as `received - video_anchor_s - duration` directly
into `network.json`, a relative number computed once, at write time. It instead writes the
exchange's absolute `startedAt`, derived from the same anchor-pair conversion used for
`video_anchor_s`. `network.json` thus gains the same raw-data property that `manifest.json` gets
from Unit 3.

**Unit 5 — the report computes relative seconds only when rendering.**
`bajutsu/report/rows.py`'s `_step_run_row` currently reads `out.started_at` directly as the
video-relative seconds for the `data-t` seek attribute. It instead computes
`data_t = out.started_at - run_result.video_anchor_s` at render time. That is the one place in the
whole pipeline that produces a video-relative number. It is also the one place to touch when the
number must be recomputed from scratch, whether after an improved anchor-resolution strategy or from
a `manifest.json` read back long after the run.

**Unit 6 — `_VIDEO_START_TIMEOUT` becomes configurable.** `bajutsu/evidence/intervals.py` gains
`_VIDEO_START_TIMEOUT_ENV = "BAJUTSU_VIDEO_START_TIMEOUT"` and a resolver
`_video_start_timeout() -> float`. The resolver mirrors `_runner_startup_timeout()` /
`_recovery_timeout()` in `bajutsu/platform_lifecycle/environments/xcuitest.py` exactly: an env
override, a fallback to the compiled default when the variable is unset or non-numeric, and
`max(0.0, float(raw))`.

The two production call sites (`start_video`, `start_screenrecord`) pass the resolved value
explicitly. A function default binds at import time and cannot pick up an env var per call. That is
already why `tests/test_intervals.py` drives deadlines through explicit arguments rather than by
patching the module constant.

`.github/workflows/ios-e2e.yml`'s workflow-level `env:` block raises the timeout, the same treatment
its four sibling timeouts already have. This unit shares no code with Units 1-5, but it shares the
exact confirmation and anchor path they touch. It is therefore scoped into this item rather than
shipped as an unrelated one-line change.

**Unit 7 — manifest schema and bilingual documentation.** Bump `manifest.json`'s `schemaVersion`.
`docs/reporting.md` / `docs/ja/reporting.md` document two points: the new absolute-epoch semantics
of `started_at` / `video_anchor_s` / `startedAt`, and that a report's relative seek offset is now a
render-time derivation rather than a stored value. `docs/evidence.md` / `docs/ja/evidence.md`
document the wall-clock anchor-pair mechanism alongside the existing `true_start` section, and the
new `BAJUTSU_VIDEO_START_TIMEOUT` override.

Work breakdown (mirrored in *Progress*):

- Unit 1 — the wall-clock anchor (`scenario_wall_start`), injected via a `WallClock` callable.
- Unit 2 — `StepOutcome.started_at` becomes an absolute epoch timestamp.
- Unit 3 — `RunResult.video_anchor_s` becomes the absolute anchor; stop excluding it from
  `manifest_dict`.
- Unit 4 — network exchange timestamps store absolute instants.
- Unit 5 — `bajutsu/report/rows.py` computes the video-relative `data-t` at render time.
- Unit 6 — `BAJUTSU_VIDEO_START_TIMEOUT` env override; raise it in `.github/workflows/ios-e2e.yml`.
- Unit 7 — manifest `schemaVersion` bump; bilingual `docs/reporting.md` / `docs/evidence.md`
  updates.

## Alternatives considered

- **Keep the relative-offset design, and merely stop excluding `video_anchor_s` from the manifest.**
  Rejected. `video_anchor_s` would still be a raw `time.monotonic()` instant, meaningless once read
  back in a different process (or on a different machine), so persisting it changes nothing. The
  actual gap is that no raw, cross-process-meaningful timing data survives the run at all. Fixing
  what the manifest excludes does not address that gap.
- **Extract frame-level timecodes from the finished recording (for example, via `ffprobe`) instead
  of a wall-clock anchor.** Rejected for the same reason BE-0346's own *Alternatives considered*
  rejects a post-hoc duration estimate. A video's exact timing is not knowable until `stop()`
  finalizes it, well after every step's timestamp needs to exist. This approach would have to buffer
  and retroactively rewrite every step's `started_at`, instead of resolving a value once, early, and
  carrying it forward.
- **Use `time.time()` directly for in-run duration and timeout arithmetic, instead of layering a
  wall-clock anchor on top of the unchanged `time.monotonic()` path.** Rejected. A wall clock can be
  adjusted backward mid-run (an NTP correction, a manual clock change), which would corrupt a `wait`
  timeout or a step's `duration_s`. Keeping `time.monotonic()` for every timing *decision*, and
  using `time.time()` for the once-per-scenario anchor this item adds, keeps that risk at zero.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — the wall-clock anchor (`scenario_wall_start`), injected via a `WallClock` callable.
- [x] Unit 2 — `StepOutcome.started_at` becomes an absolute epoch timestamp.
- [x] Unit 3 — `RunResult.video_anchor_s` becomes the absolute anchor; stop excluding it from
      `manifest_dict`.
- [x] Unit 4 — network exchange timestamps store absolute instants.
- [x] Unit 5 — `bajutsu/report/rows.py` computes the video-relative `data-t` at render time.
- [x] Unit 6 — `BAJUTSU_VIDEO_START_TIMEOUT` env override; raise it in `.github/workflows/ios-e2e.yml`.
- [x] Unit 7 — manifest `schemaVersion` bump; bilingual `docs/reporting.md` / `docs/evidence.md`
      updates.

## References

- [BE-0346 — Anchor step and network timestamps to the recording's confirmed start](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) —
  the confirmation/anchor mechanism this item builds on and revises the storage format of, rather
  than replacing.
- [`docs/evidence.md`](../../docs/evidence.md) — the evidence subsystem whose confirmed-start
  section this item extends with the wall-clock anchor and the configurable timeout.
- [`docs/reporting.md`](../../docs/reporting.md) — the report this item's absolute timestamps
  ultimately serve; documents the manifest fields a scenario's steps and network exchanges populate.
