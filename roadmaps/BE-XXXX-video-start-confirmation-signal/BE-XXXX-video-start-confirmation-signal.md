**English** · [日本語](BE-XXXX-video-start-confirmation-signal-ja.md)

# BE-XXXX — Confirm a recording started from the recorder's own signal, not from a file that never grows

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-video-start-confirmation-signal.md) |
| Author | [@akiramatsuda](https://github.com/akiramatsuda) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Verification & coverage |
| Related | [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) · [BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md) · [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) · [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) gave every recording a
*confirmed* start instant, so a report's seek offsets point at the frame the recorder opened on
rather than at the moment the process was spawned. On iOS it confirmed that start by polling the
output file for growth. That signal does not exist: `simctl io recordVideo` keeps its mp4 at zero
bytes for the whole recording and writes it in one piece at finalize, so the poll could never
succeed and spent its entire ceiling on every scenario. This item replaces the file-growth poll with
the signal simctl actually offers — the `Recording started` line it writes to its own stderr — and
decouples the measured-origin acceptance window from the lane-tunable ceiling the poll shared.

## Motivation

Three findings, each measured rather than argued.

**The confirmation never succeeded, and it is not a CI-only defect.** On a completely idle machine
(Xcode 26.6, iPhone 17 Pro, iOS 26.5) the mp4 stayed at zero bytes for the whole recording at 10, 30
and 90 seconds — 186 samples at the poll's own 0.05s cadence in the 10-second case. The file keeps
the same inode before and after finalize, so it is not a temp-then-rename, and `lsof` shows simctl
holding no write descriptor on it while recording. `simctl io --help` states both halves outright:
it "writes 'Recording started' to stderr once the first video frame has been processed", and it
"exits once the in-flight frames are processed and the video file is finalized". The compiled
default is 5 seconds, so every local run capturing video paid it too.

**The cost was the whole ceiling, on every scenario, on green runs as much as red.** The iOS lane
had raised `BAJUTSU_VIDEO_START_TIMEOUT` to 20 seconds on the reasoning that "overshooting costs
nothing on the healthy path: the poll returns the instant the first byte lands". Across 400
`ios-e2e` runs (2026-08-25 to 09-04) the warning fired once per scenario in every job: `actuation`
logged 15 warnings for 14 scenarios, `golden` 4 for 4, `network` 3 for 3. At the per-job medians that
is about **14.5 minutes of dead wall clock per full iOS run**, against the roughly 157 macOS
job-minutes a run consumes. macOS runner concurrency is capped at 10 — a measured peak of 10 spanned
two different runs — so the lane sat near its throughput ceiling on busy days and queue waits reached
a 206-minute p90. [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
already recorded that the warning fires on every scenario and deferred the fix to a follow-up
proposal; this is that follow-up.

**The poll bought nothing, because the anchor was already correct without it.** Driving the real
`start_video` path confirms the arithmetic: it blocked for 20.03 seconds and gave up, yet
`measured_start` resolved to `spawned_at + 0.147s` — within 8 milliseconds of the stderr line's own
0.155s arrival. CI agrees. In an `ios-golden-run` artifact the per-scenario gap between the first
step's `started_at` and `video_anchor_s` is +18.26s, +29.91s and +14.71s, all non-zero, so
`measured_start` was resolving there too, and every `scenario.mp4` is present and non-empty. The
runner's own render probe reports `recordVideo: exit 0, 91512 bytes`: the pipeline was healthy
throughout.

A fourth problem is adjacent rather than causal. `_measured_start` used `_video_start_timeout()` as
the far side of the window it accepts a measured origin in, so the lane's 20-second value silently
widened that window twentyfold and admitted origins no recording can have.

## Detailed design

The work breaks into three units. Unit 2 depends on unit 1 only for ordering — landing the window
fix first keeps the harvested CI measurements describing shipped behavior.

**Unit 1 — Separate the origin window's far side from the lane's patience.** `_ORIGIN_STARTUP_CEILING
= 5.0` joins `_ORIGIN_SLACK` in
[`intervals.py`](../../bajutsu/common/evidence/intervals.py), and `_measured_start` bounds its origin
with it instead of `_video_start_timeout()`. The two answer different questions: how long a
*recorder* can take to open its first frame is a property of the recorder, while
`BAJUTSU_VIDEO_START_TIMEOUT` says only how patient a lane chose to be. Android and the web backend
are unaffected, since neither overrides the variable.

**Unit 2 — Confirm from stderr.** `Proc` gains `await_stderr(needle, timeout)`, which returns the
instant a line appeared or `None` at the deadline. `_SubprocessProc` sends the child's stderr to a
`tempfile.TemporaryFile` rather than a pipe, because nobody drains a recorder's stderr for the
minutes it runs and a full pipe buffer would block the child mid-recording; the wait reads it with
`os.pread`, so the child's own write offset is untouched, and carries one needle-width tail between
reads so a match straddling two reads is still seen. It always reads once before consulting the
deadline. `start_video` waits on `Recording started` instead of polling the file, and
`_await_video_file_growing` and `_file_size` are deleted. The tri-state `Interval.start_confirmed`,
the pre-scenario `on_video_start_stall` report, and the BE-0354 replacement-device rung that reads it
all keep their present semantics and timing — the only change is that the signal is now true.

**Unit 3 — Retire the lane override and correct the prose it rested on.**
[`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) drops `BAJUTSU_VIDEO_START_TIMEOUT: "20"`; a
0.15-second signal leaves the 5-second compiled default three decimal orders of headroom. The
variable and `_video_start_timeout()` stay, because Android's two confirmations still read them.
[`docs/evidence.md`](../../docs/evidence.md) described iOS's proxy as "the output file's first
written byte" and as the *stronger* of the two backends' signals, which inverts the truth, and
asserted that raising the ceiling "costs nothing on the healthy path";
[`docs/ci.md`](../../docs/ci.md) rested the per-trigger capture budget on the video warning firing
every scenario. All are corrected in both languages.

Work breakdown (mirrored in *Progress*):

1. `_ORIGIN_STARTUP_CEILING` and the `_measured_start` window.
2. `Proc.await_stderr`, the stderr-backed confirmation, and the removal of the file-growth poll.
3. The workflow override and the bilingual documentation.

## Alternatives considered

**Drop the confirmation and let `spawned_at` be the anchor proxy.** The measurements show
`measured_start` already resolves, so the anchor would survive. But `true_start` would be permanently
`None` and `start_confirmed` permanently `False`, which leaves `Lease.video_start_stalled` reporting
a stall on every scenario — an escalation rung armed on a signal that is always true. CI never fired
it only because a pinned UDID makes `can_replace` false.

**Demote the confirmation to a one-shot process-liveness check.** Cheap and honest, and it is what
Android's own `true_start` amounts to. Rejected because simctl documents a stronger signal that
costs the same: liveness proves the process exists, `Recording started` proves a frame was
processed.

**Keep the poll and shorten the ceiling, or move it to a background thread.** Shortening trades the
20 seconds for a confirmation that still never succeeds. Backgrounding it keeps a probe that cannot
answer, and makes `None` mean both "not attempted" and "not yet resolved", which disarms the
pre-scenario stall report that
[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
reads.

**Resolve "did this produce anything" at `stop()` instead.** `Interval.stop()` already reads the
finished file, so the verdict would be free and completely accurate. Rejected as a *replacement*:
that verdict arrives after `finish_scenario_intervals`, which is after the crash that would have
consumed it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `_ORIGIN_STARTUP_CEILING` and the `_measured_start` window.
- [x] Unit 2 — `Proc.await_stderr`, the stderr-backed confirmation, and the removal of the file-growth poll.
- [x] Unit 3 — the workflow override and the bilingual documentation.

## References

- [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) — introduced the confirmed
  start instant and the iOS file-growth poll this item replaces.
- [BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md) — added
  `BAJUTSU_VIDEO_START_TIMEOUT` and raised it for the iOS lane.
- [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) —
  recorded that the warning fires on every scenario and deferred the fix here.
- [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md) —
  the Android growth check, which stays: `screenrecord` does write progressively.
- [`docs/evidence.md`](../../docs/evidence.md) · [`docs/ci.md`](../../docs/ci.md)
