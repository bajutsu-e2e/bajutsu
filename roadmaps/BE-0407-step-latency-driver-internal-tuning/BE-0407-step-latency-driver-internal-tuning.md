**English** · [日本語](BE-0407-step-latency-driver-internal-tuning-ja.md)

# BE-0407 — Cut step latency by deduplicating evidence reads and tuning driver internals (iOS and Android)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0407](BE-0407-step-latency-driver-internal-tuning.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0407") |
| Implementing PR | [#1897](https://github.com/bajutsu-e2e/bajutsu/pull/1897) (Group 1, units 1, 3-5), [#1912](https://github.com/bajutsu-e2e/bajutsu/pull/1912) (Group 1 unit 6, Group 2 units 7, 9, 10, 11, 12, 13, and half of 14), [#1925](https://github.com/bajutsu-e2e/bajutsu/pull/1925) (Group 1 unit 2, completing Group 1), [#1938](https://github.com/bajutsu-e2e/bajutsu/pull/1938) (Group 3 units 16, 18, 19, 21, 22, 23), [#2117](https://github.com/bajutsu-e2e/bajutsu/pull/2117) (reverts unit 9), [#1944](https://github.com/bajutsu-e2e/bajutsu/pull/1944) (closes units 8, half of 14, 15, 17, 20, 24 by decision, completing the item) |
| Topic | Platform support |
| Related | [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md), [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md), [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md), [BE-0341](../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture.md), [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md), [BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md), [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md), [BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md) |
<!-- /BE-METADATA -->

## Introduction

A real-device measurement pass, recorded under
[`misc/step-performance/`](misc/step-performance/README.md) in this item's own
directory, timed a single `tap` step end to end on both backends: 0.95–1.07 seconds on the iOS
Simulator, 3.25–3.32 seconds on the Android emulator's resident-server path, against a 250–500
millisecond target. Almost none of that time is the tap itself — it is redundant host-device round
trips the orchestrator and the two drivers pay on every step, reachable today with no change to the
`Driver` protocol or the wire format either backend speaks. This item is that reduction: a MECE list
of evidence-capture and driver-internal cuts, phased so the cheap, self-contained wins ship first. It
targets roughly 0.3–0.6 seconds per iOS tap step and 0.6–1.2 seconds per Android tap step — real
progress, but still short of the 250–500 millisecond target. Closing the remaining gap needs a
device-side step executor, a protocol-level change out of this item's scope and tracked as a separate,
larger set of proposals.

## Motivation

The measured step is not slow because the tap is slow. `POST /tap` on iOS averages 690 milliseconds
and Android's `POST /act` averages 2204 milliseconds (both against
[`demos/showcase/scenarios/controls.yaml`](../../demos/showcase/scenarios/controls.yaml), three
samples each, 2026-09-03), but a plain `tap` step round-trips the device five times on iOS and seven
times on Android before the orchestrator moves to the next step. Two screenshots and the tap itself
are common to both: iOS adds one tree read and one interruption drain (5 total), while Android — which
has no interruption-drain round trip — adds four tree reads (7 total). Only one of those round trips
does the step's actual semantic work.

Three costs stack on top of that count. First, evidence capture is written synchronously and
sometimes redundantly: `before.png` is taken even though nothing actuated the device since the
previous step's `after.png` left it in an identical state
([`bajutsu/common/orchestrator/loop.py:1274-1329`](../../bajutsu/common/orchestrator/loop.py)),
and `elements.json` is serialized, secret-scrubbed, and written twice per step — once before the
action, once after
([`bajutsu/common/evidence/sink.py:161-210`](../../bajutsu/common/evidence/sink.py)). Second, a
`wait until: settled` step pays a 100 millisecond floor — three reads at
[`bajutsu/common/orchestrator/waits.py:31-32`](../../bajutsu/common/orchestrator/waits.py) — even
when the screen was already settled before the wait began. Third, each backend carries its own
internal overhead inside the single round trip that dominates its step: iOS resolves roughly eleven
element attributes one at a time inside `POST /tap`
([`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:127-151,410-430`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)),
and Android's resident server appears to spend most of `POST /act` inside a fixed
`POSTDATE_BUDGET_MS` wait that a settled screen can never satisfy before its budget runs out
([`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt:184-188,642`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)).

None of this touches the determinism contract. Every cut below removes a redundant read, a redundant
write, or a fixed wait that a settled screen can never satisfy — no condition wait moves from
polling to a fixed `sleep`, and no selector resolution changes. A reader can check that the change
landed the way the investigation measured it: rerun
[`trace_run.py`](misc/step-performance/trace_run.py) against the same scenario and confirm
the iOS tap step lands in the 0.3–0.6 second band and the Android tap step in the 0.6–1.2 second band
this item targets, the same way the investigation measured today's 0.95–1.07 and 3.25–3.32 second
baselines.

## Detailed design

**Implementation order.** This item is the first of four related items in a strict order: this item,
then the device-side protocol item, then the iOS executor item, then the Android executor item. Each
later item must not begin until its predecessor has shipped — the protocol item is designed against
this item's shipped, measured baseline, not a moving target, and this item is also what proves out the
tracer ([`trace_run.py`](misc/step-performance/trace_run.py)) the later items depend on to verify
their own results. This item has no predecessor in the sequence and can begin immediately.

The work is phased in three independent groups — common to both backends, iOS-only, and
Android-only — each shippable as its own pull request against the shared yardstick
[`trace_run.py`](misc/step-performance/trace_run.py) already establishes. None of the units
below changes the `Driver` protocol, a scenario's YAML shape, or what a condition wait polls for; each
is a redundant call removed or a fixed-cost internal reordered, verified against the driver
conformance suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md))
and a rerun of the tracer.

**Group 1 is complete.** Units 1 and 3–6 landed as described. Unit 2 landed as its `after.png`
half alone; *Progress* below records the measurement behind dropping its `elements.json` half.

### Group 1 — common to both backends (orchestrator)

1. **Reuse the previous step's `after.png` as the next step's `before.png`.** No actuation happens
   between one step's end and the next step's start, so the two observe identical device state; the
   first step in a scenario still reads once. Saves one screenshot per step (90 milliseconds on iOS,
   103 on Android) — [`loop.py:1274-1329`](../../bajutsu/common/orchestrator/loop.py).
2. **Move `after.png` and the `elements.json` write off the critical path.** Capture and write both
   asynchronously once the step's own result is known, rather than blocking the next step on the
   write completing —
   [`loop.py:1601`](../../bajutsu/common/orchestrator/loop.py),
   [`evidence/core.py:174-186`](../../bajutsu/common/evidence/core.py).
3. **Write `elements.json` once, after the step, not before and after.** Drop the pre-action read
   entirely except where a capture policy actually requires it —
   [`loop.py:1712-1727`](../../bajutsu/common/orchestrator/loop.py),
   [`evidence_rules.py:190-194`](../../bajutsu/common/orchestrator/evidence_rules.py).
4. **Drop the hidden first-step read inside the evidence sink.** The sink issues its own query before
   the scenario's first step runs; fold it into that step's regular read instead of paying it twice —
   [`evidence/core.py:170`](../../bajutsu/common/evidence/core.py).
5. **Stop polling during the BE-0310 transition-signal settle window.** Once the screen-change signal
   ([BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md))
   fires, the 0.3-second post-transition quiet period only needs to keep reading when a guard or an
   interrupt handler is registered for the step; otherwise the signal alone is the condition —
   [`waits.py:1042-1061`](../../bajutsu/common/orchestrator/waits.py).
6. **Fold `drain_interruptions` into the `/tap` or `/elements` response instead of its own round
   trip.** iOS pays a fresh TCP connection for this every step
   ([`xcuitest.py:591,1170`](../../bajutsu/common/drivers/xcuitest.py)); Android already keeps it
   in memory, so this unit is iOS-only.

### Group 2 — iOS driver internals

7. **Batch the tap-path attribute reads into one `el.snapshot()` call.** `POST /tap` currently resolves
   the element from its path, then reads roughly eleven attributes one at a time — `exists`,
   `identifier`, `label`, `type`, `enabled`, `selected`, `frame` (twice), `isHittable` — before
   synthesizing the tap
   ([`XcuitestElementProvider.swift:127-151,410-430`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)).
   Cache `app.frame` across calls instead of re-reading it. Expected saving: 150–300 milliseconds per
   tap.
8. **Tap the cached frame's center by coordinate, re-verifying identity only when staleness is
   suspected.** [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)
   already takes this route for Safari content; this unit generalizes it. Needs review before landing
   — a coordinate tap trades one kind of correctness risk (a moved element) for latency, so the
   fallback to identity re-verification has to be right. Expected saving: another 50–150 milliseconds.
9. **Skip the `safariViewService.state` cross-process check unless the snapshot shows a remote-view
   boundary node.** Every query pays this Cross-Process Communication (XPC) probe today regardless of
   whether the app under test embeds a web view —
   [`XcuitestElementProvider.swift:50`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift).
   Expected saving: 5–50 milliseconds per query. [#2117](https://github.com/bajutsu-e2e/bajutsu/pull/2117) reverts this unit. On iOS 26 the
   boundary node reaches the service's snapshot alone, never the app's. The gate never opens there.
10. **Make the `/zorder` second round trip lazy.** Fetch z-order only when a selector is actually
    ambiguous and needs it resolved —
    [`xcuitest.py:744-768`](../../bajutsu/common/drivers/xcuitest.py). Expected saving: 5–30
    milliseconds per query it currently runs unconditionally for.
11. **Enable HTTP keep-alive on both ends of the XCUITest channel** —
    [`xcuitest.py:591`](../../bajutsu/common/drivers/xcuitest.py),
    [`HTTPServer.swift:326`](../../BajutsuKit/Sources/BajutsuRunner/HTTPServer.swift). Expected
    saving: 1–3 milliseconds per round trip, compounding across a scenario's many reads.
12. **Re-query immediately on the first stale-element retry instead of sleeping first.** The runner
    currently sleeps a fixed 0.5 seconds before its first retry and 1.0 seconds before its second
    ([`xcuitest.py:141-149,146,848-853`](../../bajutsu/common/drivers/xcuitest.py)), because every
    exception during actuation maps to `stale`, including a transient failure mid-animation. The
    re-query is itself a wait, so removing the sleep does not weaken the retry's determinism.
13. **Check `alerts.firstMatch.exists` before enumerating SpringBoard alert buttons.** The current
    probe lists every button on every check; test existence first and only enumerate when an alert is
    actually present —
    [`XcuitestElementProvider.swift:302-320`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift).
    Expected saving: 100–400 milliseconds per probe.
14. **Raise `BAJUTSU_XCUITEST_MAX_WARM_REUSES` above its current 3, and skip reinstall when the app
    bundle's digest is unchanged.** Saves 4–10 seconds per scenario boundary —
    [`environments/xcuitest.py:259-306`](../../bajutsu/common/platform_lifecycle/environments/xcuitest.py).
15. **Type text via `simctl pbcopy` and a paste keystroke instead of per-character `typeText`.** Needs
    review — a paste bypasses input handlers some text fields rely on, so this unit needs a fallback
    for fields that reject paste —
    [`simctl.py:842-878`](../../bajutsu/common/backend_cli/simctl.py). Expected saving: roughly 40
    milliseconds per character typed.

### Group 3 — Android driver internals

16. **Confirm, then remove, the `POSTDATE_BUDGET_MS` wait that a settled screen can never satisfy.**
    Highest-priority unit in this group — the investigation traces most of the 2204-millisecond
    `POST /act` average to this single fixed 2000-millisecond budget, more than any other item
    in this proposal. `respondAct` waits, before injecting a tap, for an accessibility event that
    postdates the `since` mark the host captured immediately before sending the request
    ([`ResidentServerTest.kt:184-188,642`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt),
    [`drivers/adb.py:812-831,1299`](../../bajutsu/common/drivers/adb.py)). When the screen is
    already still, as it is at every tap target in `controls.yaml`, no event later than that mark
    exists to satisfy the wait before the tap's own injection — so the budget is spent every time
    rather than only when actually needed. **This must be confirmed against the resident server's own
    logs before implementing a fix** — the investigation's account is built from code reading plus a
    measurement correlation, not a direct log trace. Once confirmed, a fix (for example, answering
    immediately when only a `VIEW_CLICKED` event with no accompanying layout event is expected) needs
    review against the read-lag barrier (`_READ_LAG_S`,
    [`adb.py:536`](../../bajutsu/common/drivers/adb.py)) that this same postdate mark also
    protects, so the fix does not reopen a race the barrier currently closes.
17. **Skip `settledDump`'s second read when no accessibility event fired between the two.** The
    `ReadMark` already knows whether one arrived —
    [`ResidentServerTest.kt:482-490,558-587`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt).
    Expected saving: 100–200 milliseconds per read.
18. **Make the `nativeZ` full-node walk opt-in per app**, rather than running on every read —
    [`ResidentServerTest.kt:427-457`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt).
    Expected saving: 20–100 milliseconds per read.
19. **Carry the already-settled tree in `/act`'s own response**, so the host's `_settle` can skip its
    first read when the server already confirmed the tree matched twice on-device —
    [`adb.py:1015-1031,1341`](../../bajutsu/common/drivers/adb.py),
    [`ResidentServerTest.kt:516-524`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt).
    Expected saving: 400–600 milliseconds.
20. **Take screenshots from the resident server with `UiAutomation.takeScreenshot()`**, removing the
    `adb exec-out screencap` subprocess spin-up (measured 103 milliseconds today) —
    [`backend_cli/adb.py:975-990`](../../bajutsu/common/backend_cli/adb.py).
21. **Keep the resident server's socket open across reads instead of answering `Connection: close`
    on every one** —
    [`adb_resident.py:125,183,269`](../../bajutsu/common/backend_cli/adb_resident.py),
    [`ResidentServerTest.kt:83,544`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt).
    Expected saving: 30–150 milliseconds per read.
22. **Reuse the resident server's Android Application Package (APK) across a whole run**, skipping
    reinstall when its signature and version already match the installed one —
    [`adb_resident.py:395-399`](../../bajutsu/common/backend_cli/adb_resident.py),
    [`environments/android.py:367`](../../bajutsu/common/platform_lifecycle/environments/android.py). Expected saving:
    6–12 seconds per scenario boundary — the investigation's measured 1.5 seconds is a reinstall onto
    an emulator that was already booted with the package already present once, not a cold-start
    figure.
23. **Stop parsing the hierarchy XML twice on the host** —
    [`adb_resident.py:78-102`](../../bajutsu/common/backend_cli/adb_resident.py),
    [`drivers/adb.py:404-437`](../../bajutsu/common/drivers/adb.py). Expected saving: 10–40
    milliseconds per read.
24. **Add a `swipe` variant to `/act` with the same publish confirmation a tap gets.** The
    investigation measured a `scroll` step at 7.1 seconds — heavier than a tap — because the swipe
    itself and the confirming read each separately exhaust the same fixed budget unit 16 targets —
    [`drivers/adb.py:1477-1552`](../../bajutsu/common/drivers/adb.py). This unit shares its root
    cause with unit 16, and also reduces the cost of the off-screen tap-recovery path, which retries a
    `scroll` internally when a tap target starts outside the visible frame
    ([`bajutsu/common/orchestrator/loop.py`](../../bajutsu/common/orchestrator/loop.py), tap
    recovery), so a `tap` step that needs recovery benefits from this unit too, not only a `scroll`
    step.

## Alternatives considered

- **Wait for the device-side step executor and skip this incremental pass.** Rejected: the executor
  is a protocol-level change tracked as its own, larger set of proposals, and every scenario run pays
  today's redundant reads and fixed waits in the meantime. The units above ship independently of that
  work and do not compete with it — several (the evidence-capture dedup, the keep-alive changes) stay
  useful even after an executor lands, since a device-side executor still returns evidence to the host
  over the same channel.
- **Skip straight to the executor instead of tuning the current boundary.** Rejected for the same
  reason [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) phased its own fix:
  the internal-tuning units are lower-risk, ship as small independent pull requests, and prove out the
  measurement tooling — [`trace_run.py`](misc/step-performance/trace_run.py) and
  [`bench_orchestrator.py`](misc/step-performance/bench_orchestrator.py) — that a later
  executor item will also depend on to show its own win.
- **Split the Android `POSTDATE_BUDGET_MS` unit (16) into its own item, since it needs server-log
  confirmation first.** Considered and rejected for now: it shares this item's driver/runner surface,
  its "no protocol change" scope, and its measurement yardstick with every other Android unit here.
  The checklist below marks it explicitly unconfirmed rather than done, so it cannot ship silently
  ahead of that confirmation. If the fix turns out to need its own design discussion once confirmed,
  splitting it out at that point remains available.
- **One roadmap item per unit.** Rejected as too fragmented for 24 units that share one motivation
  (today's measured step latency) and one yardstick (the tracer's before/after numbers). Each unit
  still ships as its own focused pull request; grouping them under one item avoids maintaining
  near-duplicate Motivation sections and cross-links across two dozen files for what is one initiative.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

**Sequence status: no predecessor — this item can begin immediately** (see *Implementation order* in
*Detailed design*).

- [x] Measure the baseline and build the yardstick — real-device tracing of both backends
  (2026-09-03), recorded in [`misc/step-performance/`](misc/step-performance/README.md)
  in this item's own directory.
- [x] Group 1, units 1, 3, 4, 5 — reuse the previous step's `after.png` as this step's
  `before.png` (Unit 1), stop writing `elements.json` before a step acts (Units 3–4), and stop
  polling the device during the BE-0310 settle quiescence window when no guard or interrupt
  handler is registered (Unit 5).
- [x] Group 1, unit 2 — overlap the step's mandatory `after.png` with the post-step tree read.
  Two deviations from the literal design, both settled by measurement. We dropped the
  `elements.json` half. That write costs 0.36ms on a real device tree, and 0.9–8.7ms across
  100–1200 synthetic elements. Under 2 percent of a step does not pay for a thread pool on the
  path that scrubs secrets. The other half, `after.png`, turned out to be a *device round trip*
  rather than a write, so the run loop overlaps that shot instead of deferring a write. The
  backend's own channel has to admit a second call in flight. Where it does not, the shot stays
  synchronous. The new `base.BackgroundScreenshotProvider` declares that property: adb has it,
  XCUITest does not, because `APIHandler` funnels every XCUITest operation onto the runner's main
  thread — the non-re-entrancy BE-0323 records. The shot never outlives its own step, which
  answers the three design questions this unit sat parked over: a `finally` joins the shot before
  the step returns, so nothing waits at a scenario boundary and nothing waits before the report.
- [x] Group 1, unit 6 — fold iOS's `drain_interruptions` into `/tap`'s own reply. The design named
  "`/tap` or `/elements`". This covers `/tap` alone, the higher-frequency of the pair, rather than
  every actuation. The driver accumulates whatever a tap's own fold already carried. When another
  driver call — a query or a stale retry's re-resolve — intervenes in the meantime, the driver
  merges that accumulation with an explicit `/interruptionPolicy/drain`, so a tap's reply loses
  nothing even where the fast path does not apply.
- [x] Group 2, unit 7 — batch the tap-path attribute reads into one `el.snapshot()` call; cache
  `app.frame` for the life of a resident lease (guarded against caching a transient `.zero` read).
- [x] ~~Group 2, unit 8 — generalize BE-0396's coordinate tap beyond Safari.~~
  - Attempted, then reverted after review: `el.isHittable` checks XCUITest's own hit point for the
    element (which honours a custom `accessibilityActivationPoint` and can differ from the
    frame's geometric center under partial occlusion), while a coordinate tap always lands on
    that geometric center — a silent mis-tap on an element whose real hit point was clear. A
    follow-up investigation confirmed XCUITest's public API exposes neither the resolved
    activation point nor a point-to-element hit test, so the two points cannot be reconciled
    without either accepting that risk or re-querying the tree at tap time — which would undo the
    round trip this unit exists to save. Decided: keep the Safari-only route; not pursued further
    within this item.
- [x] ~~Group 2, unit 9 — gate the `safariViewService.state` XPC probe on the app's snapshot.~~
  - Landed in #1912; [#2117](https://github.com/bajutsu-e2e/bajutsu/pull/2117) reverts it. On an iOS 26.5 Simulator the boundary node
    appears in the service's snapshot alone. The app's own snapshot never carries it. The gate
    never opened, and every `SFSafariViewController` element dropped out of the tree. A scenario
    could not drive the in-app browser. The guard could not see the Save Password alert over it.
    A cheaper gate needs a signal the iOS 26 app snapshot carries. We have found none yet.
- [x] Group 2, unit 10 — make `/zorder` lazy. Deviation from the literal design: `nativeZ` is
  diagnostic only and is never read for selector-ambiguity resolution
  (`resolve_unique`'s `_collapse_identical_duplicates` deliberately excludes it), so "only when a
  selector is ambiguous" never fires as written against today's code. Implemented as "skip on the
  internal handle-resolution queries that discard the tree immediately; keep on the public
  `query()` path evidence and `serve` actually consume" instead — the intent the stale text was
  reaching for.
- [x] Group 2, unit 11 — HTTP keep-alive on both ends. One persistent connection reused across a
  driver's whole lease, discarded and reconnected when a proactive liveness check (a zero-timeout
  `select` plus a non-consuming peek) finds it already closed, an actual failure says so, or it has
  simply sat idle too long (past a conservative fraction of the runner's own idle timeout — the
  peek alone leaves a residual race against that timeout that a PR review round later closed);
  `HTTPServer.swift` loops per connection until the peer goes idle, sends something malformed, or a
  reply to it could not be fully written (ending the connection rather than desynchronizing it).
- [x] Group 2, unit 12 — re-query immediately on the first stale-handle retry; the second retry
  keeps its 1.0s backoff.
- [x] Group 2, unit 13 — check `alerts.firstMatch.exists` before enumerating SpringBoard alert
  buttons.
- [x] Group 2, unit 14 — half shipped. Skipping reinstall when the app bundle's digest is
  unchanged landed, scoped to `reinstall: overwrite` (never `clean`, whose uninstall-then-install
  is a deliberate data wipe the digest check must not skip). ~~Raising
  `BAJUTSU_XCUITEST_MAX_WARM_REUSES` above 3 did not.~~
  - Not pursued further within this item: that default is BE-0291's own empirical finding for
    when the resident runner starts crashing, and nothing in this pass measured a device that
    tolerates more reuses to justify moving it.
- [x] ~~Group 2, unit 15 — type text via `simctl pbcopy` and a paste keystroke.~~
  - Attempted, then reverted after an on-device run of `text_editing.yaml`:
    `app.typeKey("v", modifierFlags: .command)` triggers iOS's cross-app "Allow Paste" consent
    alert on every paste ("\"BajutsuRunnerUITests-Runner\" would like to paste from \"Showcase
    SwiftUI\" — Do you want to allow this?"), which blocks the runner's main thread indefinitely —
    no button its interruption monitor is registered to answer — timing out `POST /type` and
    crashing the runner. A follow-up investigation found no dismissal mechanism in this codebase
    can reach that alert: every existing alert handler runs only between XCUITest calls or right
    before XCUITest synthesizes the next interaction, and XCUITest's non-reentrancy (BE-0323)
    forbids a second, concurrent call from another thread while the blocked `typeKey` call holds
    the main thread; no `simctl privacy`-style suppression exists for the pasteboard consent
    either. Decided: keep plain per-character `typeText`; not pursued further within this item.
- [x] Group 3, unit 16 — the mechanism confirmed by direct measurement, and the fix is a
  one-line correction rather than a redesign. `_device_act` sent one value for two purposes: the
  mark the device's own pre-injection read must postdate, and the anchor for the barrier armed
  after the gesture. Both took the clock as of building the request, so the device was asked to
  wait for an event newer than the request itself, which a settled screen can never produce.
  Measured on an API 34 emulator, driving the channel directly against a still screen: `POST /act`
  took 2217ms and 2042ms with that mark against 64ms and 62ms with none. The wait falls before the
  staleness check, so a `stale` reply paid it too. `since` now carries the pending barrier's own
  mark, which `_settle` has usually already drained, and the barrier keeps anchoring on the clock.
  Nothing the read-lag barrier protects moves: the host still settles before it sends, and the
  device still settles its own bounds read.
- [x] Group 3, unit 18 — the `nativeZ` walk is now asked for, by `targets.<name>.android`'s own
  `nativeZ: true`, rather than run on every read.
- [x] Group 3, unit 19 — `/act` carries the caught-up tree, so the read the host opens `_settle`
  with is already answered. Carried only alongside a publish confirmation, because that
  confirmation is the whole safety argument: an event postdates the injection, so the dump cannot
  be the pre-gesture screen. The driver re-checks the tree's own mark against the gesture too, so
  one mislabelled header cannot seed a stale screen.
- [x] Group 3, unit 21 — one connection per lease on both ends, retired by the same proactive
  staleness check unit 11 uses on the XCUITest channel.
- [x] Group 3, unit 22 — the resident APK pair is reused across a run's leases, keyed on the two
  APKs' own digests (which pin the signing key the uninstall-first rule was guarding) and on a `pm
  path` check that the device still carries both.
- [x] Group 3, unit 23 — the resident channel hands the driver the tree it already parsed to strip
  the decor windows, and the narrowed body is serialized only when a `rawTree` capture asks for it.
- [x] ~~Group 3, unit 17 — skip `settledDump`'s second read when no accessibility event fired
  between the two.~~
  - Implemented, then reverted after measurement. The premise is sound in isolation: a node's
    bounds reach a dump by way of an accessibility event, so no event means no change. What it
    misses is that the confirming dump is also what *keeps the event stream flowing to the
    server's own listener*, and the read mark that listener maintains is what the host's
    read-lag barrier releases on. Without the second dump the mark stalls: an API 34 emulator's
    `controls.yaml` scroll step went to 7.5, 7.7 and 10.0 seconds against 5.7, 5.9 and 6.0 with
    the dump restored, because the pan's barrier spent its whole budget on a mark that never
    advanced. A follow-up investigation found no lighter-weight platform call that keeps the mark
    live without the dump — the server's own comments describe `waitForIdle` as resting on the
    same event stream the listener observes, but name no substitute for whatever effect the dump
    itself has on that stream. Decided: keep the two-dumps-agree settle; not pursued further
    within this item.
- [x] ~~Group 3, unit 20 — take screenshots from the resident server with
  `UiAutomation.takeScreenshot()`.~~
  - Not implemented: the design predates unit 2, which landed the day before
    ([#1925](https://github.com/bajutsu-e2e/bajutsu/pull/1925)) and already moved the
    `adb exec-out screencap` subprocess off the critical path by overlapping it with the tree
    read. Moving the shot onto the resident channel would undo that overlap rather than deepen
    it: the server serves one connection at a time, so the shot would serialize behind the read
    it currently runs beside. Unit 2 measured that pair at 3361ms serialized against 2182ms
    overlapped, against the 103ms subprocess spin-up this unit targets. A concurrent server would
    be the way in, and whether `UiAutomation` tolerates a screenshot beside a dump is undocumented
    — a determinism question, not a performance one. Decided: not pursued further within this
    item; unit 2 already closed the gap this unit targeted.
- [x] ~~Group 3, unit 24 — add a `swipe` variant to `/act`.~~
  - Implemented twice, reverted both times after on-device measurement. With `UiDevice.swipe` the
    `controls.yaml` scroll step went to 12.8 seconds against the coordinate path's 4.2:
    UiAutomator paces its drag through `UiAutomation.executeAndWaitForEvent`, which consumes the
    events it sees, and a pan's events all land *during* the drag — so none reached the read
    mark, the reply confirmed no publish, and the host's barrier spent its full four seconds.
    Rebuilding the drag from its own `MotionEvent`s (the shape `injectDoubleTap` already uses for
    the same reason) brought it to 7.5 seconds, still worse than the coordinate path, and a
    server-side probe showed the read mark unchanged across the whole pan — so that drag was not
    moving the content either. The unit's stated root cause was unit 16's fixed budget, and unit
    16 has now removed it; what remains of a `scroll` step is the drag's own duration, which
    BE-0400 fixes deliberately, plus one confirming read. Decided: not pursued further within
    this item; its root cause is already closed.
- [x] Rerun [`trace_run.py`](misc/step-performance/trace_run.py) against `controls.yaml` after
  each group lands, and record the resulting per-step wall-clock here. iOS, after Group 1 unit 6
  and the shipped half of Group 2 (2026-09-06, iPhone 17 Pro Simulator, `controls.yaml`): `POST
  /tap` mean 690ms → 446ms across 3 taps, and only 6 of 9 `drain_interruptions` calls reached the
  wire (the rest answered from a tap's own fold). Real, measured progress against the baseline,
  short of this item's own 0.3–0.6s per-tap target now that units 8 and 15 are deferred. Android,
  after Group 3 (2026-09-07, API 34 arm64 emulator, `controls.yaml`, three runs each side on a
  freshly booted device with the runs serialized, step wall-clock from the run manifest rather than
  the tracer — the step is what this item's target is stated in, and the emulator's own noise swamps
  a per-call cut): a `tap` step went from 3.46–5.67s to 2.32–4.57s, roughly a third off its median,
  and a `scroll` step from 5.99–6.02s to 5.97–8.24s, unchanged within that noise. The whole scenario
  went from 25.3–28.1s to 19.2–26.7s. That is short of the 0.6–1.2s this item targets for Android —
  the rest of that gap is the device-side executor's ([BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md)).
- [x] Backfill reciprocal `Related` links between this item and the device-side protocol, iOS
  executor, and Android executor items, in both languages — done after the `roadmap-id` workflow
  allocated the four ids on `main`, since a new item may not cross-reference another new item by
  `BE-XXXX` before allocation, so none of the four could carry this on merge.
- [x] Replace each "companion item" mention with a link to the now-numbered item, in both languages.

Log:

- [#1897](https://github.com/bajutsu-e2e/bajutsu/pull/1897) — Group 1, units 1, 3-5. Dropped the pre-step
  baseline's `elements.json` write (the post-step capture always overwrote it anyway), except in the one
  path that never reaches that post-step capture — a step failing on an uncovered `handleSystemAlert`
  locale — which now writes its tree explicitly. Stopped polling the device during the BE-0310 settle
  quiescence window when no system-alert guard or `interrupts` handler is registered. Reused the previous
  step's `after.png` as the next step's `before.png` when nothing has actuated the device in between,
  except on a recovery step, a `handleSystemAlert` step, or any scenario declaring `interrupts` — an
  asynchronous interstitial could have arrived on exactly those, so they always shoot fresh instead. Units
  2 (async evidence writes) and 6 (iOS `drain_interruptions` fold) remain for a follow-up PR.
- [#1912](https://github.com/bajutsu-e2e/bajutsu/pull/1912) — Group 1 unit 6, Group 2 units 7, 9, 10, 11, 12, 13, and half of unit 14 (the digest-skip half).
  Folded the interruption drain into `/tap`'s own reply and had the driver accumulate what a
  tap's fold already carried, merging it with an explicit drain whenever another call intervened.
  Batched the tap-path attribute reads into one `el.snapshot()` call and cached `app.frame` for a
  lease's life. Made the `safariViewService.state` XPC probe and the `/zorder` round trip
  conditional (the latter's trigger reshaped — see the Progress note on unit 10's stale premise).
  Reused one persistent HTTP connection per lease on both ends, backed by a proactive
  before-reuse staleness check rather than a guess from a failure's exception type. Dropped the
  fixed sleep before the first stale-handle retry, and the enumerate-first path in the SpringBoard
  alert probe. Skipped app reinstall on warm resume when the bundle's digest is unchanged, scoped
  to `reinstall: overwrite`. Verified end to end on an iPhone 17 Pro Simulator across `smoke`,
  `controls`, `alert`, `text_editing`, and `permission_system_alert`; `trace_run.py` against
  `controls.yaml` showed `POST /tap`'s mean drop from the baseline's 690ms to 446ms and 6 of 9
  `drain_interruptions` calls answered from a tap's own fold rather than the wire. Units 8
  (generalized coordinate tap) and 15 (paste-based text entry) were attempted and reverted after
  review and on-device verification found each unsafe as designed — see the Progress notes on
  both. Unit 14's `MAX_WARM_REUSES` half, unit 16, units 17–24, and the two Group 1 units already
  deferred remain for later PRs.
- [#1925](https://github.com/bajutsu-e2e/bajutsu/pull/1925) — Group 1 unit 2, which completes Group 1.
  Overlapped the step's `after.png` with the post-step tree read rather than deferring a write.
  Measurement put the cost in the device round trip, not the write. That write comes in at 0.36ms on
  a real device tree. Two narrow opt-ins gate the overlap, one per side.
  `base.BackgroundScreenshotProvider` names the driver's half, `DeferredScreenshotSink` the sink's.
  The run loop asks those two rather than branching on a backend name. A backend that declares
  neither takes its shot synchronously, where it always did. adb alone declares the driver half. The
  iOS runner serializes every XCUITest operation onto its main thread, so nothing overlaps there. A
  `finally` joins the pending shot before the step returns. That answers the three design questions
  this unit sat parked over. Tests pin three consequences the design left unlisted. A failing shot
  never stands in for the fault that caused it. The reservation gets its owner-only mode before the
  recorder writes into it. A shot that never wrote leaves no zero-byte husk. Measured on an API 34
  emulator, through the real driver and sink. The shot-then-read pair fell from 3361ms to 2182ms.
- [#1938](https://github.com/bajutsu-e2e/bajutsu/pull/1938) — Group 3, units 16, 18, 19, 21, 22 and 23. Stopped asking the device to wait for an event
  newer than the request that asked for it, which a settled screen can never produce and which cost
  every gesture the server's whole 2s postdate budget. Made the `nativeZ` node walk something a
  target asks for in its own config rather than something every Android read pays for. Had `/act`
  carry the tree the device dumped once it had confirmed the gesture published, so the read the
  host opens `_settle` with is already answered — gated on that confirmation, and on the tree's own
  mark postdating the gesture, so no unconfirmed reply can seed a stale screen. Kept one HTTP
  connection per lease on both ends. Stopped reinstalling the resident APK pair on every lease when
  the run had already put those exact bytes on that device and `pm path` still finds them. Stopped
  parsing the hierarchy XML twice on the host, and moved the narrowed body's serialization onto the
  `rawTree` capture's own accessor so a run that never takes it never pays. Verified on an API 34
  arm64 emulator across the eighteen scenarios the Android e2e lane runs, all passing; a `tap` step
  fell from 3.46–5.67s to 2.32–4.57s across three runs each side, with `scroll` unchanged within
  the emulator's noise. Units 17 and 24 were each implemented and reverted after measurement found
  them slower than what they replace; unit 20 was never implemented — see the Progress notes on all
  three.
- [#1944](https://github.com/bajutsu-e2e/bajutsu/pull/1944) — Closed units 8, 14
  (`MAX_WARM_REUSES` half), 15, 17, 20, and 24 as decided against, not left open. A fresh
  investigation found no safe path for any of them. Unit 8 cannot reconcile a coordinate tap's
  landing point with `isHittable`'s activation point. XCUITest exposes no public API for the
  resolved activation point, and none for a point-to-element hit test. Unit 15's paste alert cannot
  be dismissed mid-call. XCUITest's non-reentrancy (BE-0323) forbids a concurrent dismissal from
  another thread. The blocked call holds the main thread throughout. Unit 17's dump-skip has no
  known lighter-weight substitute for keeping the Android read mark advancing. Unit 14's reuse
  ceiling was already closed by a lack of data. Unit 20 was already closed by unit 2. Unit 24 was already
  closed by unit 16. Every Progress box is now checked. Units landed as designed stay plain. Units
  closed by decision carry a strikethrough, with the reason nested beneath. Status moves to
  Implemented. The remaining gap toward this item's own latency targets is the device-side
  executor, tracked in BE-0408–BE-0410.
- [#2117](https://github.com/bajutsu-e2e/bajutsu/pull/2117) — Reverted the unit 9 gate.
  An on-device bisect traced an iOS 26 regression to that gate. Under the gate, no in-app browser
  element reached the tree. `save_password_browser.yaml` broke on every run. The tree read again
  probes `safariViewService.state` on every query, as before #1912.

## References

[BE-0105 — Single-snapshot XCUITest query](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0234 — Speed up adb scenario runs](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md),
[BE-0259 — Assert/query snapshot reuse](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md),
[BE-0310 — iOS accessibility screen-change readiness](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md),
[BE-0341 — Pre-action evidence capture](../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture.md),
[BE-0396 — iOS SFSafariViewController tree](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md),
[`misc/step-performance/README.md`](misc/step-performance/README.md) — the full
measurement writeup this item summarizes,
[`bajutsu/common/orchestrator/loop.py`](../../bajutsu/common/orchestrator/loop.py),
[`bajutsu/common/drivers/xcuitest.py`](../../bajutsu/common/drivers/xcuitest.py),
[`bajutsu/common/drivers/adb.py`](../../bajutsu/common/drivers/adb.py),
[`bajutsu/common/backend_cli/adb_resident.py`](../../bajutsu/common/backend_cli/adb_resident.py)
