# Speeding Up Step Execution: Investigation Report

> The scope is iOS (XCUITest) and Android (adb plus the resident UIAutomator server). Web is out of
> scope. The target is 250–500 ms per step end to end, evidence capture included.
> The investigation began in a Claude Code session on Linux. With no Simulator or emulator available
> there, the early figures were estimates built up from code reading and measured figures already
> recorded in roadmap items. On 2026-09-03, real measurements on a Mac's Simulator and emulator —
> figures observable at the HTTP boundary (the round-trip average for `/tap`, `/elements`,
> `screenshot`, and similar calls, and the wall-clock time of a single step) — replaced the estimates
> in Sections 1–6. The breakdown inside the HTTP boundary (how many times an attribute is read, how
> many XCUI resolutions happen, and so on) was not observed directly and remains an estimate, tagged
> "est".

## Conclusion Summary

The measurements timed `controls.yaml`'s tap step, three runs each on an iPhone 17 Pro Simulator and
a `bajutsu-api34-arm64` emulator. A single step today takes 0.95–1.07 seconds on iOS and 3.25–3.32
seconds on Android's resident-server path. Android's `uiautomator dump` path was not measured this
time and stays at its estimated 8–12 seconds (est). Against the 250–500 ms target, iOS needs a
1.9–4.3× cut and Android a 6.5–13.3× cut.

Almost all of a step's time goes to two things: host-device round trips and evidence capture. The
orchestrator's own computation is under 1 ms per step and is not the bottleneck. The measurement
found a `tap` step round-trips the device five times on iOS and seven times on Android (the
scenario's first step adds one more round trip, for the pre-action evidence read). Two screenshots
and the tap itself are common to both. The tree-read count is one on iOS and four on Android; Android
also has no HTTP round trip equivalent to `drain_interruptions`. Only one of those round trips does
the tap's actual semantic work.

The path to a faster step splits into three stages. The stage letters are not independent milestones —
each one down the list needs a larger design change than the one before it.

| Stage | What it does | Expected time per step | Design change |
|---|---|---|---|
| A | Remove redundant evidence and reads (`before.png` and `elements.json` written twice, a re-read after the step) | iOS 0.4–0.9 s, Android 1.2–2 s (est) | None (inside the orchestrator) |
| B | Remove waste in communication and on-device work (keep-alive, batched attribute reads, removing the double dump) | iOS 0.3–0.6 s, Android 0.6–1.2 s (est) | Inside the driver and runner |
| C | Move the step loop to a device-side executor (evaluate waits and asserts on the device) | iOS 0.2–0.35 s, Android 0.15–0.3 s (est) | Adds a protocol |

The individual savings stages A and B are each responsible for, in this table, were not measured —
they stay as Linux-side estimates. Android does carry one measured finding this table's estimate did
not account for: `POST /act` (the device-side execution of a tap) takes 2.2 seconds, and most of that
is likely a fixed wait in the resident server, `POSTDATE_BUDGET_MS` (2000 ms). Details are in
[Section 3.3](#33-android) and [Section 4.3](#43-android-stage-b). If this wait can actually be
removed, stage B alone could bring Android close to the target too — the Android column for stages A
and B needs revisiting once that is measured.

Stages A and B alone do not bring iOS to the target, even net of the `POST /act` finding. Removing
XCUITest's fixed costs (a roughly 35 ms snapshot and 100–300 ms of tap synthesis) still is not
enough, because the structure that pays a host round trip on every poll remains. iOS reaches the
target only at stage C. The proposal to "run the scenario directly in Swift" is that stage C. The
recommendation is to place the executor inside the XCTest runner, not the in-app software development
kit (SDK, BajutsuKit) — the reasons are in [Section 5](#5-a-design-that-reaches-the-target-phase-c).

## 1. Investigation Method and What This Environment Could Determine

This environment could measure two things directly:

- The orchestrator's own overhead and the driver call count per step kind, measured by
  `bench_orchestrator.py` against a fake driver.
- That the runtime tracer, `trace_run.py`, works: a run against the fake backend produced a
  per-step breakdown.

The driver side rests on three code-reading reports, written in English and kept under `reports/`:

- [`reports/orchestrator.md`](reports/orchestrator.md): the skeleton common to every step, and the
  call sequence per step kind.
- [`reports/ios.md`](reports/ios.md): the Python driver, the Swift HTTP server, the XCUITest element
  provider, and BajutsuKit's current state.
- [`reports/android.md`](reports/android.md): the adb driver, the resident server's Kotlin side, and
  its settle and catch-up machinery.

Every estimate is tagged "est". A measured figure already recorded in a roadmap item carries its
source BE number.

The 2026-09-03 measurements ran on an Apple M5 Mac (macOS 26.5.2), against
`demos/showcase/scenarios/controls.yaml` (eight steps: tap, wait, scroll, assert), on an iOS Simulator
(iPhone 17 Pro, iOS 26.5) and an Android emulator (`bajutsu-api34-arm64`, API 34, arm64-v8a), both
already booted. These are Simulator and emulator figures, not real-device ones, and the scenario ran
only once — `POST /act` carries three samples, and the `GET /elements` family 15–26. Read every
figure in Sections 1–6 with that sample size in mind. [`trace_run.py`](trace_run.py) reproduces them
against the same targets.

## 2. Measurement Results in This Environment

### 2.1 Orchestrator Overhead and Round-Trip Counts

`bench_orchestrator.py` runs each step kind 20 times against a fake driver holding a 300-element
screen. The table below is the result with driver latency set to zero (`--model zero`).

| Step kind | Sink | Wall-clock per step | Driver calls per step |
|---|---|---|---|
| `tap` | NullSink | 0.4 ms | tap 1, drain_actuations 1, drain_interruptions 1 |
| `tap` | FileSink | 11.7 ms | the above, plus screenshot 2, query 1.05 |
| `tap` and `wait for` pair | FileSink | 12.1 ms | screenshot 2, query 1.02, tap 0.5 |
| `wait until: settled` | NullSink | 100.6 ms | query 3 |
| `wait until: settled` | FileSink (with guard) | 113.5 ms | query 3.05, screenshot 2, system_alert_labels 1 |
| `assert` | FileSink | 10.5 ms | query 1.05, screenshot 2 |
| `type` (with `into`) | FileSink | 11.3 ms | tap 1, type_text 1, screenshot 2, query 1.05 |

Four things follow from this table:

- The orchestrator's own computational cost is under 1 ms. FileSink's 11 ms is the CPU and I/O of
  writing a 300-element `elements.json` twice.
- In a normal run (FileSink), every step kind takes **two screenshots**. `before.png` is
  [BE-0341](../../../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture.md)'s
  pre-action evidence, and `after.png` is the step's mandatory evidence.
- `wait until: settled`'s tree-diff path takes at least 100 ms even when the condition is already
  satisfied — the product of `_SETTLE_POLLS = 2` and `_POLL = 0.05`
  ([`bajutsu/common/orchestrator/waits.py:31-32`](../../../../bajutsu/common/orchestrator/waits.py)).
  It reads at least three times.
- `drain_interruptions` runs on every step. On iOS, this is an HTTP round trip
  ([`bajutsu/common/drivers/xcuitest.py:1170-1176`](../../../../bajutsu/common/drivers/xcuitest.py)).

The same scenario can be reprojected against the per-call averages measured on the Mac (query, tap,
and screenshot round trips). Using the iOS model (query 67 ms, tap 749 ms, screenshot 90 ms), a `tap`
step with FileSink comes to roughly 1040 ms, and `wait until: settled` to roughly 601 ms. Using the
Android model (query 263 ms, tap 2401 ms, screenshot 104 ms), `tap` comes to roughly 2968 ms and
`wait until: settled` to roughly 1098 ms. This reprojection lines up closely with the wall-clock the
tracer measured directly for a real tap step — iOS 0.95–1.07 s, Android 3.25–3.32 s (see
[Conclusion Summary](#conclusion-summary)) — which confirms the round-trip-count model itself, aside
from `POST /act`'s outsized cost. `bench_orchestrator.py`'s `MODELS` dict carries these per-call
averages; passing `--model ios` or `--model android_resident` reruns this reprojection.

### 2.2 Tracer Verification

`trace_run.py` records how long every driver call, HTTP round trip, subprocess call, and evidence
capture takes, attributed to the step in flight, without modifying product code. Here is a sample
output against the fake backend:

```text
== per step (seconds) ==
step                      wall  driver   evid.  subproc  driver-call counts
0:wait                   1.006   0.000   0.002    0.000  query=22, screenshot=2, system_alert_labels=1, drain_interruptions=1
1:assert                 0.002   0.000   0.002    0.000  screenshot=2, query=1, drain_interruptions=1
```

Against an empty screen, `wait until: settled` queried 22 times before its one-second timeout. On
iOS, every one of those queries carries a snapshot and an HTTP round trip.

## 3. Breakdown of a Single Step Today

### 3.1 The Skeleton Common to Both Backends (Orchestrator)

`_handle_action`
([`bajutsu/common/orchestrator/loop.py:1249-1730`](../../../../bajutsu/common/orchestrator/loop.py))
processes every step kind in the same order:

| Order | Step | Round trip to the device | Location |
|---|---|---|---|
| 1 | Pre-action evidence, `before.png` and `elements.before` | One screenshot. The first step in a scenario also pays one hidden query inside the sink | `loop.py:1274-1329`, `evidence/core.py:170` |
| 2 | Pre-action query for the `screenChanged` policy | One, only if that policy is set | `loop.py:1366-1376` |
| 3 | Pre-action query for the interrupt guard | One, only if `interrupts` is declared | `loop.py:1404-1412` |
| 4 | The step's body (tap, wait, assert) | Depends on kind (table below) | `loop.py:368-473` |
| 5 | `drain_actuations` and `drain_interruptions` | iOS: one HTTP call. Android: in memory | `loop.py:1576-1581` |
| 6 | Mandatory `after.png` | One screenshot | `loop.py:1601` |
| 7 | Post-step tree read (for `elements.json`) | One query for a mutating step; `assert` and `wait` reuse their own read ([BE-0259](../../../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md)) | `loop.py:1640-1727` |

The body's round trips vary by kind:

| Kind | Minimum round trips | Notes |
|---|---|---|
| `tap` | 1 (inside the driver: iOS is 2 HTTP calls; Android is 3 HTTP calls and 4 dumps) | If off-screen, scroll recovery repeats query, is_tappable, and scroll |
| `type` (with `into`) | tap 1, type_text 1 | |
| `wait for` | A query every 50 ms until the condition is met | A guard adds `system_alert_labels` every second |
| `wait until: settled` (tree diff) | query 3 | 100 ms floor |
| `wait until: settled` (BE-0310 transition signal) | A query every 50 ms during the 0.3-second post-transition quiet period | The read serves only the guard ([`waits.py:1042-1061`](../../../../bajutsu/common/orchestrator/waits.py)) |
| `assert` | query 1 | |

Evidence is written synchronously. `elements.json` is written twice, before and after, to the same
file, and each write goes through secret redaction, JSON formatting, and a regex scan
([`evidence/sink.py:161-210`](../../../../bajutsu/common/evidence/sink.py)).

### 3.2 iOS

A tap step's breakdown, measured on an iPhone 17 Pro Simulator against `controls.yaml`
(2026-09-03). What happens inside the HTTP boundary (how many times an attribute is read, for
example) was not observed directly and stays an estimate (est) drawn from reading
[`reports/ios.md`](reports/ios.md).

| Phase | Measured or estimated | Cause | Location |
|---|---|---|---|
| `before.png` | 90 ms (measured, `GET /screenshot` mean, 17 calls) | Sends `app.screenshot().pngRepresentation` as a full-resolution PNG, occupying the runner's serial queue | `XcuitestElementProvider.swift:330`, `APIHandler.swift:27-51` |
| `/elements` | 63 ms (measured, `GET /elements` mean, 18 calls) | `app.snapshot()` at roughly 34 ms (measured in BE-0105), plus a `safariViewService.state` cross-process call (XPC) every time, and — for a BajutsuKit-linked app — a second round trip for `/zorder` | `XcuitestElementProvider.swift:49-50`, `drivers/zorder.py:36-106` |
| Tap itself | 690 ms (measured, `POST /tap` mean, 3 calls) | After resolving a live element from its position path, reads its attributes one at a time — `exists`, identifier, label, type, enabled, selected, frame ×2, isHittable, roughly 11 XCUI resolutions (this breakdown is est) — then synthesizes the tap | `XcuitestElementProvider.swift:127-151, 410-430` |
| `drain_interruptions` | 1.2 ms (measured, 9 calls) | A fresh TCP connection every time | `xcuitest.py:591, 1170` |
| `after.png` | 90 ms (measured, same call as `before.png`) | Same as above | |
| Post-step query | 63 ms (measured, same call as `/elements`) | For `elements.json` | |
| Total | 0.95–1.07 s (measured, tap-step wall-clock, 3 runs) | | |

Simply summing the per-phase figures gives roughly 997 ms, close to the measured wall-clock
(0.95–1.07 s). The remaining tens of milliseconds are the orchestrator's own computation and evidence
writes, seen in [Section 2.1](#21-orchestrator-overhead-and-round-trip-counts).

There are also these fixed costs:

- A fixed sleep on stale retries: 0.5 s on the first retry, 1.0 s on the second
  ([`xcuitest.py:141-149`](../../../../bajutsu/common/drivers/xcuitest.py)). The runner maps every
  exception during actuation to `stale`, so even a transient failure mid-animation pays 0.5 s.
- Text entry via `app.typeText` costs roughly 50 ms per character (est).
- `system_alert_labels` is a SpringBoard snapshot, 100–500 ms (est); it runs every second during a
  guarded wait.
- The runner restarts every four scenarios (`_MAX_WARM_REUSES = 3`,
  `environments/xcuitest.py:272`). A cold start is 15–40 seconds (est).

`wait_for` is a host-side 50 ms poll, not a condition wait inside the runner. `docs/drivers.md` says
to "use the runner's native condition wait" and that "screenshot uses `simctl io screenshot`" —
neither matches the current implementation.

### 3.3 Android

A tap step's breakdown on the resident-server path, measured on a `bajutsu-api34-arm64` emulator
against `controls.yaml` (2026-09-03). An earlier version of this report estimated this breakdown by
splitting it into `_settle`, "one device-side read", and "`/clock` and `/act`". What can actually be
measured is only `POST /act`, at the HTTP boundary — its interior is a single response, whose
breakdown stays an estimate (est) drawn from reading
[`reports/android.md`](reports/android.md).

| Phase | Measured or estimated | Cause | Location |
|---|---|---|---|
| `before.png` | 103 ms (measured, screencap mean, 17 calls) | A subprocess call to `adb exec-out screencap -p` | `backend_cli/adb.py:975-990` |
| `POST /act` (element resolution, tap, and publish wait) | 2204 ms (measured, mean, 3 calls) | Its interior is an est of `_settle`-equivalent reads and tap synthesis, but most of the cause is likely the `POSTDATE_BUDGET_MS` wait discussed below | `ResidentServerTest.kt:171-229, 625` |
| `after.png` | 103 ms (measured, same call as `before.png`) | Same as above | |
| Post-step query (`GET /source`) | 45–670 ms (measured, wide spread) | A "pass-through" read that has already overtaken the prior read mark returns in roughly 45 ms; a read right after a tap, where `?since=` still has to wait, takes 500–670 ms | `adb_resident.py:105-146` |
| Total | 3.25–3.32 s (measured, tap-step wall-clock, 3 runs) | | |

Simply summing the per-phase figures gives roughly 2.5–2.9 s, a few hundred milliseconds under the
measured wall-clock (3.25–3.32 s). That gap is the `GET /clock` call taken right before `POST /act`
(measured under 2 ms, 3 calls), evidence writes, and other small calls this table does not list.

`POST /act` landing at nearly the same 2.2 seconds almost every time is a sign of a fixed budget being
used up, not a variable wait. The resident server's `respondAct`
(`ResidentServerTest.kt:184-188`) waits, before injecting the tap, for an accessibility event to
overtake the `since` mark the host sent. That budget is `POSTDATE_BUDGET_MS` (2000 ms,
`ResidentServerTest.kt:642`). This `since` value comes from the host's `_capture_mark()`
(`drivers/adb.py:812-831`), taken as the device clock's "now" right before sending this `POST /act`
(`drivers/adb.py:1299`). If the screen is already still, no event arrives sooner than the one this
tap's own injection produces — so this wait has no way to be satisfied before injection, and it burns
the full two-second budget before moving on. `controls.yaml`'s tap targets are all on a screen that is
already still right before the tap, so most of the measured roughly 2.2 seconds is likely attributable
to this wait — though this has not been confirmed against the resident server's own logs. It is listed
as the top priority in [Section 4.3](#43-android-stage-b), as a candidate fix that still needs that
confirmation.

The wide spread in the measured `GET /source` figures (45–670 ms) has the same
`POSTDATE_BUDGET_MS` explanation. `respondSource`
(`ResidentServerTest.kt:353-366`) uses the same two-second budget when a `since` is present and
returns immediately once the mark is already overtaken. In the `scroll` step observed in Section 2,
both of these reads stretched to the full two-second budget, and the `scroll` step's wall-clock came
to 7.1 seconds. `scroll`'s own breakdown is out of scope for this report, but a note on it is added in
[Section 7](#7-open-questions-and-risks).

Every read opens a fresh TCP connection, and the server answers with `Connection: close`
(`adb_resident.py:125`, `ResidentServerTest.kt:544`). The host parses the same XML twice
(`adb_resident.py:78-102`, `drivers/adb.py:404-437`). There is also a fixed per-scenario cost: the
resident server's Android Application Package (APK) is reinstalled every time, and instrumentation is
restarted after that (`adb_resident.py:383-399`). The measured figure was 1.5 seconds, but that is a
reinstall onto an emulator that was already booted with the package already present — the fixed cost
of an emulator cold boot or a first-time package install stays at an estimated 6–12 seconds (est).

The `uiautomator dump` path is 2.3–2.5 seconds per read (measured in BE-0234). If the server APK is
not built, or the channel fails, the driver silently falls back to this path.

## 4. Bottleneck Ranking and Countermeasures

The expected values are the cut per step. The "Determinism" column notes the effect on the prime
directives (no fixed sleep, deterministic judgment, app-agnosticism).

### 4.1 Common to Both Backends (Stage A)

Unless noted otherwise, the iOS and Android expected values rest on the measured figures in
[Section 3.2](#32-ios) and [Section 3.3](#33-android).

| Rank | Countermeasure | Expected value | Determinism | Where to change |
|---|---|---|---|---|
| 1 | Drop `before.png`, or reuse the previous step's `after.png`. Nothing actuates between them, so the screen is the same | iOS 90 ms, Android 103 ms | No effect. Evidence only | `loop.py:1274-1329` |
| 2 | Move `after.png` off the critical path. Capture on an async thread and write asynchronously too; consider JPEG or a smaller size | iOS 90 ms, Android 103 ms | No effect | `loop.py:1601`, `evidence/core.py:174-186` |
| 3 | Write `elements.json` once, after the step. Issue the post-step query only when a policy actually requires it | iOS 63 ms, Android 45–670 ms (measured, a wide spread — see Section 3.3) | No effect | `loop.py:1712-1727`, `evidence_rules.py:190-194` |
| 4 | Remove the hidden query inside the sink on the first step | One read per scenario | No effect | `evidence/core.py:170` |
| 5 | Stop the read during the settle wait on the BE-0310 transition-signal path. Read only when a guard or an interrupt is present | iOS 200–300 ms (est, per settle) | No effect. The judgment stays the signal | `waits.py:1042-1061` |
| 6 | Carry `drain_interruptions` on the `/tap` or `/elements` response instead of its own round trip | iOS 1.2 ms | No effect | `xcuitest.py:1170`, `APIHandler.swift` |

### 4.2 iOS (Stage B)

From here on, the target is the inside of `POST /tap` (measured at 690 ms). The call count and time
inside it are outside the HTTP boundary, not measured directly, and the expected values stay an
estimate (est) drawn from reading [`reports/ios.md`](reports/ios.md).

| Rank | Countermeasure | Expected value | Determinism | Where to change |
|---|---|---|---|---|
| 1 | Batch the tap-path attribute reads into one `el.snapshot()` call. Cache `app.frame` | 150–300 ms | No effect | `XcuitestElementProvider.swift:410-430, 122-125` |
| 2 | Go further: tap the recorded frame's center with `app.coordinate`. Verify identity only when staleness is suspected | Another 50–150 ms | Needs review. [BE-0396](../../../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md) already takes the coordinate-tap route for Safari | Same as above |
| 3 | Check `safariViewService.state` only when the snapshot has a remote-view boundary node | 5–50 ms per query | No effect | `XcuitestElementProvider.swift:50` |
| 4 | Make `/zorder` lazy. Call it only when resolving a selector's z-order ambiguity actually requires it | 5–30 ms per query | No effect | `xcuitest.py:744-768` |
| 5 | Enable HTTP keep-alive on both ends | 1–3 ms per round trip | No effect | `xcuitest.py:591`, `HTTPServer.swift:326` |
| 6 | Re-query immediately on the first stale retry instead of sleeping first | 0.5 s (when it happens) | No effect. The re-query is itself a wait | `xcuitest.py:146, 848-853` |
| 7 | Have the SpringBoard probe check `alerts.firstMatch.exists` first. Enumerate buttons only when one exists | 100–400 ms per probe | No effect | `XcuitestElementProvider.swift:302-320` |
| 8 | Raise `BAJUTSU_XCUITEST_MAX_WARM_REUSES`. Skip reinstall when the digest matches | 4–10 s per scenario | No effect | `environments/xcuitest.py:259-306` |
| 9 | Replace character-by-character text entry with `simctl pbcopy` and `typeKey("v", .command)` | 40 ms per character | Needs review. Some input fields do not accept paste | `simctl.py:842-878` |

### 4.3 Android (Stage B)

The measurement in Section 3.3 surfaced the `POSTDATE_BUDGET_MS` wait as the top-priority candidate,
and the ranking below reflects that. Every expected value but the first is unchanged from the earlier
version, an estimate (est) drawn from reading [`reports/android.md`](reports/android.md).

| Rank | Countermeasure | Expected value | Determinism | Where to change |
|---|---|---|---|---|
| 1 | Skip the `POSTDATE_BUDGET_MS` (2000 ms) wait `respondAct` pays on `since` before injecting, when the screen is already known to be still. One approach: answer immediately when only `VIEW_CLICKED` is expected with no layout event | Up to nearly the measured 2.2 seconds per tap (needs verification — the reasoning in Section 3.3 is not yet confirmed by the server's own logs) | Needs review. First confirm from the server's logs that this wait is really being used up, then revisit its effect on the read-lag barrier's (`_READ_LAG_S`) safety | `ResidentServerTest.kt:184-188, 353-366, 625, 642`, `drivers/adb.py:536, 1299` |
| 2 | Skip `settledDump`'s second dump when no accessibility (a11y) event arrived around the first one — `ReadMark` already knows | 100–200 ms (est, per read) | No effect. The judgment condition is unchanged | `ResidentServerTest.kt:482-490, 558-587` |
| 3 | Walk every `nativeZ` node only when the app opts in | 20–100 ms (est, per read) | No effect | `ResidentServerTest.kt:427-457` |
| 4 | Carry the device-settled tree in `/act`'s own response, so the host's `_settle` can skip its first read | 400–600 ms (est) | No effect. The tree already matched twice on-device | `drivers/adb.py:1015-1031, 1341`, `ResidentServerTest.kt:516-524` |
| 5 | Return screenshots from the resident server via `UiAutomation.takeScreenshot()`. Remove the subprocess call | Measured 103 ms per shot | No effect | `backend_cli/adb.py:975-990` |
| 6 | Keep-alive: stop the server's `Connection: close` and reuse the same socket across `handle` calls | 30–150 ms (est) | No effect | `adb_resident.py:125, 183, 269`, `ResidentServerTest.kt:83, 544` |
| 7 | Reuse the resident server across the whole run. Skip reinstall when the signature and version already match | 6–12 s per scenario (est — the 1.5 s measured in Section 3.3 was a reinstall onto an already-booted emulator) | No effect | `adb_resident.py:395-399`, `environments/android.py:367` |
| 8 | Stop parsing the XML twice | 10–40 ms (est, per read) | No effect | `adb_resident.py:78-102`, `drivers/adb.py:404-437` |
| 9 | Add `swipe` to `/act`, giving a pan the same publish confirmation a tap gets | The measured `scroll` step is 7.1 s (Section 3.3 — both `GET /source` calls burned the full `POSTDATE_BUDGET_MS`) | No effect | `drivers/adb.py:1477-1552` |

## 5. A Design That Reaches the Target (Phase C)

Stage C does not exist yet, so every figure in this section is an estimate (est). Only building it
lets those figures be replaced with measured ones.

### 5.1 The Boundary of Judgment

Prime directive 1 is "an AI does not judge," not "the host must judge." A device-side executor
deterministically resolving a selector and evaluating a condition wait or an assert does not violate
that directive. Python keeps three responsibilities:

- Expand the scenario and send it as a sequence of steps the device side can execute.
- Receive the evidence the device side returns (the element tree, coordinates, screenshots, and read
  timestamps) and write `manifest.json` and the HTML report.
- Decide pass/fail. A device-side evaluation is an input; the host confirms the final verdict against
  the same deterministic rules.

What moves to the device side:

- Selector resolution.
- `wait for`, `until: gone`, and `until: settled`.
- The `assert` kinds closed to the screen (`exists`, `label`, `value`, `count`, `enabled`).
- `tap`, `type`, `swipe`, and `scroll`.

The `http`, `email`, `generate`, `visual`, `golden`, and `request` families of `assert` stay on the
host.

### 5.2 iOS: A Step Executor Inside the XCTest Runner

"Run the scenario directly in Swift" happens inside the XCTest runner's own process, not inside the
app (BajutsuKit). Three reasons:

- BajutsuKit has none of event injection, keyboard input, a frame-bearing accessibility tree, or a
  screenshot capability ([`reports/ios.md`](reports/ios.md), Section 5). `BajutsuTouch` is an
  observation-only swizzle; it does not synthesize input.
- SpringBoard's permission dialogs, the system keyboard, and `SFSafariViewService` content are
  unreachable from inside the app.
- The process boundary is exactly what lets a crash in the app be observed.

An executor inside the runner can be built by adding `POST /scenario` (a step sequence) on top of the
current `APIHandler`. The executor does four things natively:

1. Takes one `app.snapshot()` and resolves the selector inside the runner — porting the current
   Python-side `resolve_unique` to Swift.
2. Taps by `app.coordinate`, at the resolved element's frame center. It does not re-read attributes.
3. Evaluates condition waits with a snapshot loop (a 30–40 ms cadence). BajutsuKit's screen-transition
   signal (BE-0310) currently reaches only the Python-side collector; extending it to reach the runner
   too lets `settled` use it as its condition directly.
4. Returns screenshots and the element tree asynchronously alongside the step's result (sent per step
   over chunked transfer).

A tap step is estimated at 35 ms for the snapshot, 100–200 ms for the coordinate tap, and 35–70 ms for
the settle judgment — 200–350 ms total. The screenshot is off the critical path.

XCUITest waits for the app to reach quiescence around each event. If this 100–200 ms of tap synthesis
remains, using the same private API WebDriverAgent uses becomes an option: disabling
`XCUIApplicationProcess`'s `waitForQuiescenceIncludingAnimationsIdle:`. It is a change inside the test
bundle, so it does not affect the app — but it depends on a private API an Xcode update could break.

### 5.3 Android: A Step Executor Inside the Instrumentation Server

The resident server already runs as an instrumentation, with a `UiAutomation` session and an
accessibility-event listener ([`reports/android.md`](reports/android.md), Section 6). An executor
needs four more things:

1. Replace the tree read, currently `dumpWindowHierarchy`'s XML, with a direct
   `AccessibilityNodeInfo` walk — the same walk `nativeZ` already does.
2. Make `wait` and `settled` event-driven, on `TYPE_WINDOW_CONTENT_CHANGED` and `WINDOWS_CHANGED`.
   Replace the current approach — wait until two dumps match — with quiescence from the events.
3. Inject taps and pans with `UiAutomation.injectInputEvent`, and judge publish confirmation by event
   kind too.
4. Take screenshots with `UiAutomation.takeScreenshot()` and return them with the result.

A tap step is estimated at 20–50 ms for the tree walk, 50 ms for injection, and a few tens of
milliseconds for the event-driven settle — 150–300 ms total.

### 5.4 A Protocol Proposal and Its Staged Rollout

There is no need to move everything at once. Rolling it out in the following order lets each stage
land on its own:

1. Move `wait for` and `until: gone` to the device side (`POST /wait`, carrying the selector and
   timeout). This removes the 50 ms polling round trips.
2. Move `settled` to the device side. iOS receives the transition signal directly; Android uses a11y
   events.
3. Move the `assert` kinds closed to the screen to the device side.
4. Bundle the step sequence into one `POST /scenario`. This is where a step costs one round trip or
   fewer.

`find_all` and `resolve_unique` in `bajutsu/common/drivers/base.py` define selector semantics today.
What needs porting is `within`, `idMatches`, `labelMatches`, and the trait derivations, plus Android's
`_derived_label` (`drivers/adb.py:251-282`). Port them to Swift and Kotlin, and check equivalence
against the existing driver conformance suite (BE-0114). An ambiguous selector should fail with the
same wording on the device side too.

### 5.5 Why Not an In-App Executor

An executor that lives entirely inside the app is appealing for minimizing round trips. Beyond the
three reasons already given, it is not the choice made here because it violates the app-agnostic
principle (prime directive 3): every app under test would need the SDK built in, and an app without
it would not work at all. An in-app SDK's role is better kept to an observation aid — like the
screen-transition signal — not an actuator.

## 6. Proposed Implementation Order

| Stage | Scope | Rough time | Candidate BE items |
|---|---|---|---|
| 0 | Measure on a Mac and replace this report's estimates with measured figures | Done (reflected in this report) | None |
| A | Section 4.1, items 1–6 | 1–2 weeks | Async and dedup evidence capture (1 item) |
| B-Android-1 | Section 4.3, item 1 (the `POSTDATE_BUDGET_MS` wait) | A few days. First confirm the mechanism from the server's logs | Revisit Android tap wait time (1 item) |
| B-iOS | Section 4.2, items 1, 3, 4, 5, 6 | 1–2 weeks | Batch the XCUITest tap-path attribute reads (1 item), keep-alive (1 item, both OSes) |
| B-Android-2 | Section 4.3, items 2, 3, 4, 5, 6, 7 | 2–3 weeks | Optimize resident-server reads (1 item), server-side screenshots and reuse (1 item) |
| C | Section 5.4, items 1–4 | 4–8 weeks | Device-side step executor (protocol: 1 item, iOS: 1 item, Android: 1 item) |

Stage A is independent of the other stages and is the first to pay off.
B-Android-1 turned out, per Section 3.3's measurement, to be the single largest factor in Android's
cost, at 2.2 seconds per tap. It is worth starting as its own small, independent change ahead of the
other B stages — but, per the "Determinism" column in the table, the wait needs to be confirmed as
actually being used up, from the server's logs, before it is implemented. Stage C's item 1 (moving
`wait` to the device side) can start alongside stage B.

## 7. Open Questions and Risks

- iOS's tap synthesis (including XCUITest's quiescence wait) has not been measured — this is the
  number that sets stage C's floor. What could be measured was `POST /tap`'s total (690 ms); its
  interior split between resolution and tap synthesis has not been separated.
- `app.snapshot()` waits for the app to settle. It runs longer than 34 ms during an animation, and a
  device-side executor's `settled` floor is bound by animation length in the same way.
- Android's `waitForIdle` waits up to 10 seconds on a screen where a11y events never stop. An
  event-driven design needs to set that cap explicitly.
- The `POSTDATE_BUDGET_MS` reasoning in [Section 3.3](#33-android) and
  [Section 4.3](#43-android-stage-b) was built from code reading and a correlation with the
  measurement, not confirmed against the resident server's own logs. Before implementing the fix, that
  confirmation — that the wait really is being used up — needs to happen first.
- `scroll` turned out, by measurement, to be a step far heavier than tap: 7.1 seconds on iOS and 7.1
  seconds on Android. On iOS, a single `POST /scroll` call takes 3.1–3.5 s. On Android, the swipe
  itself takes 2.7 s, and `GET /source` also burns its `POSTDATE_BUDGET_MS`. This report breaks down
  tap alone, so `scroll`'s own breakdown stays out of scope.
- `scroll`'s cost may not be confined to the `scroll` step alone. As the table in
  [Section 3.1](#31-the-skeleton-common-to-both-backends-orchestrator) shows, when a `tap` target is
  off-screen, `tap` itself falls back to scroll recovery internally. How often that recovery actually
  fires is unconfirmed, but if it fires, speeding up `scroll` would cut not only the `scroll` step but
  also any `tap` step that goes through this recovery. `scroll`'s stage B candidate (item 9 in
  [Section 4.3](#43-android-stage-b) covers only the Android side; iOS has none listed yet) may be
  worth reprioritizing with that knock-on effect in mind.
- `docs/drivers.md`'s description of iOS gets two points wrong: the condition-wait path and the
  screenshot path. Neither matches the implementation. Filing this as an Issue via `record-issue` is
  the right fix.
- Stage C adds a "run a step sequence" path to the `Driver` interface. Because this is a cross-cutting
  change, it needs the design agreed as a BE item before work starts.

## Appendix

- [`bench_orchestrator.py`](bench_orchestrator.py): the orchestrator measurement against a fake
  driver.
- [`trace_run.py`](trace_run.py): the runtime tracer.
- [`reports/orchestrator.md`](reports/orchestrator.md), [`reports/ios.md`](reports/ios.md),
  [`reports/android.md`](reports/android.md): the code-reading reports (English).
