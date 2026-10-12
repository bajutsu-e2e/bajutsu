**English** · [日本語](BE-XXXX-ios-dropped-touch-retry-ja.md)

# BE-XXXX — Detect and re-issue a synthesized touch the iOS Simulator drops

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ios-dropped-touch-retry.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

On the iOS Simulator, a touch that XCUITest synthesizes can be dropped before it reaches the app
under test. XCUITest still reports the touch as delivered, so a `tap` step passes having done
nothing. We propose that the XCUITest backend detect each such drop from the Simulator's own log
and re-issue the touch, at most twice. A drop that survives both re-issues fails the step with a
reason that names it.

The drop has a single, observable cause. For every synthesized touch, XCUITest's test manager
(`testmanagerd`) attaches a fresh virtual touch digitizer to `backboardd`, the Simulator process
that routes touches to apps. Now and then, `backboardd` registers that one digitizer as two services.
The touch-down then lands in one service's touch state and the rest of the touch in the other.
`backboardd` discards the rest, logs that it did, and cancels the half-delivered touch when the
digitizer detaches. The app receives a cancelled touch, so no control fires. Those log lines are a
deterministic, per-touch signal, and this item turns them into a verdict for every touch the runner
synthesizes.

## Motivation

A dropped touch is a false pass, the most expensive outcome a deterministic gate can produce. The
step that dropped the touch reports success. Whatever the scenario checks next decides whether
anyone notices. A later `wait` that times out turns the drop into a failure that names the wrong
step. A scenario with no later check stays green while it tests nothing.

The drop is not hypothetical. The `app-crash (xcuitest)` CI job runs a scenario that taps a button
which crashes the app, then expects the next step to fail. It failed twice in a row on
[#2154](https://github.com/bajutsu-e2e/bajutsu/pull/2154): the crash button's tap never reached
the app, so the expected-to-fail scenario passed. In a later run, the `actuation (xcuitest)` job
dropped the tap that opens a tab in `paste_system_alert`, and the step's `wait` timed out 20 seconds
later. Every drop we captured shows the same lines in the Simulator's unified log:

| Run | Dropped touch | `backboardd` lines |
|---|---|---|
| 38198619020, attempt 1 | crash button (first tap) | `digitizer event … from unsupported service`, then `skipping event` |
| 38198619020, attempt 2 | crash button (first tap) | `unknown digitizer`, then `skipping event` |
| 38241318813, attempt 1 | `perm` tab (62 s into the run) | `unknown digitizer`, then `skipping event` |

The 82 healthy taps in the third run show none of those lines. The signal and the drop line up one
to one. The full log analysis lives in [#2157](https://github.com/bajutsu-e2e/bajutsu/issues/2157).

The race is not tied to the first touch of a session, a reading the first two runs invite. The
third drop came after an earlier warm-up touch, and its digitizer attached in 32 ms. A warm-up
touch cannot prevent the race ([#2160](https://github.com/bajutsu-e2e/bajutsu/pull/2160)
tried one and was closed). Nor can bajutsu avoid synthesizing touches: XCUITest offers no other way
to drive an app.

Re-issuing a dropped touch is safe. `backboardd` cancels the half-delivered touch when the digitizer
detaches, so the app never sees a completed touch. No control fires, and no gesture recognizer
completes. A second touch cannot double an action the first one performed, provided each verdict
covers exactly one synthesized touch. The design below guarantees that.

The change should be visible in two places once it ships. A run's evidence records each re-issued
touch against the actuation that needed it. And the false passes above stop recurring across
repeated iOS CI runs: `app-crash (xcuitest)` no longer passes with the crash button untapped.

## Detailed design

### Scope

Every runner endpoint that synthesizes a touch is in scope. That covers `/tap` (by handle, by point,
with a tap count, and as a long press), `/gesture` (pinch and rotate), `/swipe`, `/scroll`, the
picker-wheel adjustment, and the system-alert button tap. Typing goes through key events, not the
touch digitizer, and stays out of scope.

Two existing behaviors would put more than one touch inside one verdict, and the design removes
both. First, `/gesture` actuates a pinch or rotate inside the runner up to four times (three re-issues) when a
scenario sets `BAJUTSU_GESTURE_RETRY=1`. Unit 4 retires that loop once the driver's re-issue is in
place, so the driver's re-issue becomes the only retry. Second, the runner's interruption monitor taps an alert button or swipes away a
notification banner in the middle of an outer actuation. Those touches get markers of their own,
described below.

The picker-wheel adjustment may synthesize several drags in one call. Re-issuing it stays safe,
because the call targets a value: a second adjustment toward the same value changes nothing that
the first one already set.

The mechanism runs on every iOS Simulator run with the XCUITest backend, with no configuration. A
false pass happens where nobody is looking for it, so the protection cannot wait for someone to opt
in. A physical device stays out of scope: its unified log does not reach the host the same way, and
no drop has been observed there.

### The runner marks each synthesis

Each in-scope handler in the runner (`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`)
writes two `os_log` lines around its touch synthesis. The lines go to a dedicated subsystem and
category:

```
bajutsu.synthesis begin <id>
bajutsu.synthesis end <id>
```

`<id>` is a counter the runner owns, unique for the life of the runner process. The interruption
monitor wraps each of its own touches in the same pair of markers, with an id of its own. Its
markers therefore nest inside the window of the outer actuation it interrupted. The reply of each
in-scope endpoint gains an optional `synthesisId` field (`BajutsuKit/Sources/BajutsuRunner/openapi.yaml`).
Wherever the runner returns drained `labels` and `banners` (the `/interruptionPolicy/drain` reply,
and the `tap` and `resolveAlerts` replies that fold the same store in), it adds parallel arrays, as
`tappedAlerts` already does. Each holds, per entry, the synthesis ids of the monitor touches behind
it. A reply without the field means the runner never synthesized the touch (a stale handle, for
example), and the driver has nothing to verify.

### The host watches the Simulator's log

The XCUITest environment (`bajutsu/common/platform_lifecycle/environments/xcuitest/`) starts one
`xcrun simctl spawn <udid> log stream` process per device session, next to the runner. The process
filters to the runner's subsystem and to `backboardd`'s `TouchEvents` category. The `deviceLog`
evidence capture already builds the same command (`device_log_cmd` in
`bajutsu/common/evidence/intervals/_functions.py`). A parser turns the stream into a small table: the
begin and end time of each synthesis id, and the time of each drop line.

The parser attributes each drop line to the innermost window open at its timestamp. A dropped
monitor touch therefore never marks the outer actuation as dropped.

The parser classifies three `backboardd` messages as a drop:

- `unknown digitizer`
- `digitizer event for display <main> from unsupported service`
- `didn't see a previous touch down or range-in event … skipping event`

### A verdict for each synthesis

After an in-scope endpoint replies with a `synthesisId`, the driver asks the monitor for that id's
verdict. The monitor waits for the id's end marker, a condition wait with a bound, never a fixed
sleep. It then answers one of three verdicts:

| Verdict | Meaning | Driver response |
|---|---|---|
| `delivered` | No drop line between the id's begin and end markers. | Continue. |
| `dropped` | At least one drop line inside that window. | Re-issue the same request. |
| `unverified` | The end marker did not arrive within the bound. | Continue, and record the verdict. |

An `unverified` verdict leaves the run as it behaves today, so a stalled log stream can
never fail a healthy scenario. Unit 1 sets the bound from measurement.

A dropped monitor touch is not re-issued. The banner swipe already confirms clearance before it
claims the interruption, and declines when it cannot. An alert the monitor claimed but that is still
up makes XCUITest re-invoke the monitor on the next interaction, so a dropped alert press is pressed
again. What changes is the
report: an alert answer or banner dismissal is reported as
unconfirmed only when every touch the monitor synthesized for that showing dropped, so the run never claims an answer the app did not receive.

### Re-issue, then fail by name

`XcuitestDriver` (`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`) re-issues a `dropped`
request at most twice. Each re-issue goes through the same path as the original, including the
handle re-resolution that a `stale` reply already triggers (BE-0289). A third `dropped` verdict
raises a new `TouchNotDelivered` error. Its reason names the Simulator race, not the selector, so the
failure is not mistaken for a missing element.

Each actuation record in the run's evidence gains the verdicts it went through. A re-issued tap
shows up in the report with its drop, not as an ordinary tap.

### Prime directives

- **AI never judges.** The verdict comes from fixed log messages and timestamps, with no large
  language model (LLM) anywhere on the path.
- **Determinism first.** The monitor waits on a condition, the end marker, with a bound. The bounded
  re-issue mirrors BE-0289's bounded stale-handle retry.
- **App-agnostic.** The signal comes from `backboardd` and the runner, never from the app under test.

### Work breakdown

1. **Measure the marker path on the CI host.** Emit the markers from a throwaway runner build and
   record two numbers on the iOS CI host: the delay between an end marker and its arrival on the
   host, and whether `backboardd` lines ever arrive after a later runner marker. The first number
   sets the bound. The second decides whether the verdict may compare arrival order or must compare
   timestamps. It also confirms that a dropped two-finger pinch or rotate emits the same
   `backboardd` lines.
2. **Runner markers and `synthesisId`.** Add the `os_log` markers to every in-scope handler and to
   the interruption monitor's touches, and the optional `synthesisId` field to `openapi.yaml` and
   each in-scope reply, and the monitor touches' ids, as parallel arrays, to every reply that drains `labels` and `banners`
   (`/interruptionPolicy/drain`, `tap`, `resolveAlerts`). Cover the id counter
   and the reply field in `swift test`.
3. **Host log monitor.** Start and stop the `log stream` process with the device session, parse it,
   and answer verdicts. Attribute each drop line to the innermost window, and report a monitor
   answer as unconfirmed only when every touch it synthesized for that showing dropped. Cover the parser, the nesting, and all three verdicts
   in the fast suite with a recorded stream.
4. **Driver re-issue and `TouchNotDelivered`.** Check the verdict after each in-scope actuation,
   re-issue at most twice, and raise the new error after that. Record the verdicts in the actuation
   evidence. Retire the `BAJUTSU_GESTURE_RETRY` loop in `/gesture`, and drop the opt-in and its
   comment from `demos/showcase/scenarios/gestures_multitouch.yaml`. Cover the retry bound and the error in the fast suite with a fake monitor.
5. **Documentation.** Describe the verdicts, the re-issue, and the new error in `docs/` and its
   `docs/ja/` mirror, and in `DESIGN.md` or `docs/architecture.md` wherever the XCUITest actuation
   path is described.
6. **Verify on CI.** Confirm that repeated `app-crash (xcuitest)` and `actuation (xcuitest)` runs
   record re-issues instead of false passes or misattributed timeouts.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A warm-up touch before the runner serves | The race is not tied to the first touch. [#2160](https://github.com/bajutsu-e2e/bajutsu/pull/2160) built the warm-up, and a later tap in the same CI run still dropped. |
| Correlate by host time, plus a fixed grace period | A fixed wait breaks prime directive 2. A slow host could also deliver the drop line after the grace period ends, so the drop would go unseen. |
| Check the log once per step, at the step boundary | Cheaper per touch, but the drop surfaces after later actuations have run. Re-issuing the touch at that point is no longer safe, so the step could only fail. |
| Fail on a drop, without re-issuing | Removes the false pass but keeps a flake in every scenario the race hits. The re-issue is safe, because the app received a cancelled touch. |
| Record drops in the evidence only | Leaves the false pass in place; the drop becomes visible only to someone who reads the evidence after a run. |
| An opt-in configuration switch | The false pass happens where nobody expects it, so an opt-in protects only users who already know about the race. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — measure the marker path on the CI host
- [ ] Unit 2 — runner markers and `synthesisId`
- [ ] Unit 3 — host log monitor
- [ ] Unit 4 — driver re-issue and `TouchNotDelivered`
- [ ] Unit 5 — documentation
- [ ] Unit 6 — verify on CI

## References

- [#2157](https://github.com/bajutsu-e2e/bajutsu/issues/2157) — the issue, with the unified-log
  analysis of every captured drop.
- [#2160](https://github.com/bajutsu-e2e/bajutsu/pull/2160) — the warm-up-touch attempt, closed
  once a later dropped tap refuted it.
- [#2154](https://github.com/bajutsu-e2e/bajutsu/pull/2154) — where the `app-crash (xcuitest)`
  false pass first appeared.
- [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md) —
  the bounded stale-handle retry that this item's re-issue mirrors.
- [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) — the
  CI diagnostics that captured the unified logs this item relies on.
