**English** · [日本語](BE-0346-video-timing-sync-ja.md)

# BE-0346 — Anchor step and network timestamps to the recording's confirmed start

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0346](BE-0346-video-timing-sync.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0346") |
| Implementing PR | [#1500](https://github.com/bajutsu-e2e/bajutsu/pull/1500) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

Every [step](../../docs/glossary.md#scenario-authoring) in a run's HTML report carries a
timestamp that seeks the scenario's recorded video to the moment that step ran. This item gives
each scenario-video [interval](../../docs/glossary.md#evidence-capturepolicy-trace-triage) a
confirmed start time. It then threads one correction from that confirmed time through every place
a report timestamp is computed, so the seek lands on the moment the clicked row actually shows.

## Motivation

A step's report timestamp is `clock.now() - scenario_start`. The runner stamps `scenario_start`
once, right after the scenario's video recording begins. That stamp is not the instant the video's
first frame exists, and the gap runs in opposite directions on different
[actuators](../../docs/glossary.md#driver-backend-actuator-platform):

- On the `adb` actuator (Android) and the `playwright` actuator (web), the video starts *before*
  `scenario_start`: during app launch on Android, at browser-context creation on web. The report
  timestamp under-counts, and the seek lands before the action it names.
- On the `xcuitest` actuator (iOS), the video starts on demand. The evidence sink spawns it as a
  bare subprocess, and `simctl io recordVideo` (the underlying Simulator command) takes a
  non-trivial time to write its first frame. The runner stamps `scenario_start` *before* the video
  exists, so the report timestamp over-counts and the seek lands too late.

No code today measures this gap, so every report that includes a scenario video carries the
mismatch.

The fix must not reassign `scenario_start` after the fact, because the same value also produces
the scenario's reported `duration_s`. The video correction sits on top of it as a
separate offset. iOS is the exception: confirming the video's start *before* `scenario_start` is
stamped (Detailed design 1–2) means the confirmation wait itself delays the stamp. `duration_s` on
iOS *excludes* that wait. `run_scenario` stamps `scenario_start` after
`sink.start_scenario_intervals` returns, so up to `_VIDEO_START_TIMEOUT` (5s) of real time counts
toward the run's wall-clock but never appears in the scenario's reported duration. This item
accepts that trade-off for iOS, and states it outright so no reader has to infer it.

## Detailed design

**1. Confirm each video interval's true start.** Add a `true_start: float | None` field to
`Interval` (`bajutsu/evidence/intervals.py`). It holds the `time.monotonic()` instant at which a
capture was confirmed to have begun producing data, or `None` when the confirmation was never
attempted or never succeeded. `start_video` (iOS) and `start_screenrecord` (Android) each gain a
`confirm_started: bool = False` parameter. When it is set, each function spawns the recording
process and then polls a real, observable signal:

- iOS polls the output file until it grows *past the size it had before this attempt spawned*
  (`simctl io recordVideo` writes progressively to that path). Growth confirms the recording is
  producing frames. The pre-spawn baseline is load-bearing. A crash-retry (BE-0049) reuses the
  scenario id and so the same target path, and a finalized earlier attempt's leftover bytes would
  otherwise confirm a start that never happened on the very first poll.
- Android polls `adb.screenrecord_pids_cmd` until it reports a pid that was *not* present before
  this attempt spawned. This mirrors the shape `_await_screenrecord_stopped` already uses for the
  opposite direction, with the same baseline guard, here against a `screenrecord` leaked by an
  earlier attempt. The poll confirms merely that the device-side process exists. That guarantee is
  weaker than iOS's, because a process existing is not proof that its encoder is emitting frames.
  It is still a real signal, earlier than the moment the local `adb shell` client returns, and it
  lands before the app launches either way.

Both polls are bounded, condition-based waits, never a fixed `sleep`, matching prime directive 2.
A poll that times out leaves `true_start` at `None`. The correction then degrades to a no-op (the
behavior today) and never uses a guessed number.

The web actuator needs no polling. `PlaywrightDriver` stamps `time.monotonic()` immediately after
the recording page is created. `record_video_dir` enables recording for a context's pages, but the
video itself does not exist until a page does, so the stamp waits for `new_page()` rather than
`new_context()`.

**2. Confirm only where it changes behavior.** Two production call sites pass
`confirm_started=True`:

- `FileSink._start_simctl_interval`'s video branch (`bajutsu/evidence/core.py`), iOS's on-demand
  start.
- `AndroidEnvironment._prestart_video`
  (`bajutsu/platform_lifecycle/environments/android.py`).

Both waits sit on the scenario's critical path. The prestart is immediately followed by
`e.launch(...)` with nothing running concurrently, so its poll delays `am start` too. Both waits
are bounded and small: a probe round trip, plus at most `_VIDEO_START_TIMEOUT` (5s) when the signal
never arrives.

`AdbDriver.driver_interval` (`bajutsu/drivers/adb.py`) passes the flag too. A caller reaching that
driver directly then gets the same confirmation, instead of a silent regression to the uncorrected
behavior. The device pool never reaches it today, because `AndroidEnvironment` always prestarts.
Every other *caller* keeps the default `confirm_started=False`, so the opt-in stays per call site,
not per scenario.

Not every existing test is unaffected. `tests/test_adb.py`, `tests/test_evidence.py`, and
`tests/test_adb_lifecycle.py` each had to script the new `pgrep` probe for a changed call site,
because a test that leaves the probe unanswered stalls for the full `_VIDEO_START_TIMEOUT`.

**3. Apply the correction without reassigning `scenario_start`.** Android's video is a
*prestarted* interval, adopted (not started fresh) at scenario start via `intervals.adopt`
(`bajutsu/evidence/intervals.py`). That function must carry the wrapped interval's `true_start`
forward into the `Interval` it returns. Otherwise the confirmation from step 1 never reaches step 3,
and Android's fix silently degrades to a no-op.

In `run_scenario` (`bajutsu/orchestrator/loop.py`), once the scenario's intervals are started and
`scenario_start` is stamped, resolve `video_start_offset = true_start - scenario_start` for the
video interval when a confirmed `true_start` exists, else `0.0`. Thread the offset through
`_LoopConfig` and apply it in `_StepRunner._run_one`:
`outcome.started_at = max(0.0, (start - scenario_start) - video_start_offset)`.

A negative offset (Android, web) shifts a step's reported time later, into the video, canceling the
extra pre-launch footage. On iOS the residual stays small because `scenario_start` is stamped
after the blocking confirmation returns (see Motivation on why that differs from leaving
`scenario_start` untouched).

Expose the resolved absolute anchor as `RunResult.video_anchor_s` (`bajutsu/orchestrator/types.py`)
for step 4 to reuse. It is a plain additive scalar field, so `report/load.py`'s reconstruction
needs no change. It is also a raw `time.monotonic()` instant, meaningful only within the process
that produced it. `manifest_dict` (`bajutsu/report/manifest.py`) must exclude it from the
persisted JSON. A bare `asdict()` would write it out as a value with no meaning once read back.

One visible consequence is not a further bug to chase. For a video-capturing Android or web
scenario, a step's corrected `started_at` (and the report's rendered elapsed column) can now exceed
the scenario's `duration_s`. The two measure different things on purpose: the video's timeline
starts before the run's own step loop does, while `duration_s` measures the loop itself.
`docs/reporting.md` should say so, so a reviewer comparing the two numbers reads the gap as expected
rather than as evidence the fix is wrong.

**4. Unify the network-exchange anchor with the same fix.** `bajutsu/runner/pipeline.py` stamps its
own, independently drifting `scenario_start = time.monotonic()` before calling `run_scenario`. It
uses that stamp to compute each network exchange's report timestamp. The write happens after
`run_scenario` returns, so replace the local stamp with `result.video_anchor_s` and rename
`_write_network`'s parameter to match. This also removes a second, smaller pre-existing skew
between the step and network timelines.

**5. Extend the documentation.** `docs/evidence.md` (and its `docs/ja/` mirror) already scopes the
up-front recording to Android and web via `records_video_up_front`. This item adds `true_start` and
the offset correction to that section. `docs/reporting.md` (and its mirror) gains a note that a
report's `data-t` seek target is anchored to the confirmed or best-known video start, not the raw
`scenario_start`. The note also covers the one visible consequence a reader would otherwise misread
as a bug: a step's `started_at` exceeding the scenario's `duration_s`.

Work breakdown (mirrored in *Progress*):

- Unit 1 — `Interval.true_start`, the two polling helpers, and `confirm_started` on both starters.
- Unit 2 — wiring `confirm_started=True` at the two production call sites, and stamping `true_start`
  in the Playwright driver.
- Unit 3 — the `video_start_offset` correction in `loop.py` and `RunResult.video_anchor_s`.
- Unit 4 — the `pipeline.py` anchor unification.
- Unit 5 — the bilingual documentation update.

## Alternatives considered

- **A fixed, guessed startup-latency constant**, subtracted unconditionally on iOS instead of
  polling for a real signal. Rejected. A static number does not track a run's actual jitter (host
  load, Simulator version), so it is no more principled than today's uncorrected behavior. A
  condition wait that degrades to `None` on failure is never worse than the status quo, and is
  correct whenever the signal succeeds.
- **Leaving `true_start` unconfirmed and computing only a post-hoc estimate from the finished
  video's duration.** Rejected. A video's exact duration is not reliably knowable until the
  recording is finalized on `stop()`, well after every step's timestamp has been recorded. This
  approach would have to buffer and retroactively rewrite every step's `started_at`, instead of
  resolving a one-time offset at scenario start.
- **Also prestarting video on the iOS actuator**, so its cold-start footage is captured the way
  Android's and web's already are. Out of scope for this item. `_DeviceEnvironment`
  (`bajutsu/platform_lifecycle/environments/ios.py`) once carried `_prestart_video` /
  `_stop_prestarted_video` wiring left over from a retired plain-`simctl` environment (BE-0290
  removed the `idb` actuator and the `IosEnvironment` around it). A separate cleanup removed that
  dead plumbing, so today `prestarted_intervals` is an unconditional `[]` on this base class and
  `records_video_up_front` returns `False` for XCUITest. Whether iOS should also prestart is a
  separate design question with its own trade-off: it would add scenario-video coverage of app
  launch, at the cost of the same kind of critical-path latency this item already accepts for the
  confirmation poll. A future item can settle it; this fix does not bundle it.
- **Re-resolving `video_start_offset` on a mid-scenario driver relaunch** (`RelaunchFn`, the web
  driver's fault-isolation path). A relaunch replaces the whole browser through `_starter` rather
  than `_new_context`, so it neither restamps `PlaywrightDriver`'s `true_start` nor gives the
  replacement context a `record_video_dir`. Out of scope. The runner resolves `video_start_offset`
  once, from `sink.start_scenario_intervals`'s result, before the step loop that could ever trigger
  a relaunch begins, so a relaunch cannot make the resolved value wrong. What a relaunch *does* do
  is replace the browser context whose video the sink later finalizes. That already changes what
  the saved video contains, independently of this item. Re-anchoring the offset to a relaunched
  context's new start is future work, needed only if that pre-existing gap is addressed too.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `Interval.true_start` and `confirm_started` on `start_video` / `start_screenrecord`.
- [x] Unit 2 — wire `confirm_started=True` at the two production call sites; stamp `true_start` in
      the Playwright driver.
- [x] Unit 3 — `video_start_offset` correction in `loop.py`; `RunResult.video_anchor_s`.
- [x] Unit 4 — unify `pipeline.py`'s network-exchange anchor with `video_anchor_s`.
- [x] Unit 5 — extend `docs/evidence.md` and `docs/reporting.md` (both languages).

## References

- [`docs/evidence.md`](../../docs/evidence.md) — the evidence subsystem this item corrects and
  extends (interval capture, the sink's adopt-on-stop shape).
- [`docs/reporting.md`](../../docs/reporting.md) — the report this item's correction ultimately
  serves; documents the manifest fields a scenario's steps and network exchanges populate.
- [BE-0290 — Make XCUITest the default iOS backend and retire idb](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) —
  retired the plain-`simctl` `IosEnvironment` this item's *Alternatives considered* traces the
  now-inert `_prestart_video` wiring back to.
- [BE-0028 — Guard against over-matching evidence rules](../BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard.md) —
  another correctness fix in the same evidence-capture subsystem, at the capture-rule layer rather
  than the timestamp layer this item addresses.
