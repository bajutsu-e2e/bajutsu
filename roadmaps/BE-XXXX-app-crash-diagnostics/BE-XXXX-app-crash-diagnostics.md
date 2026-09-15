**English** · [日本語](BE-XXXX-app-crash-diagnostics-ja.md)

# BE-XXXX — Detect a crash in the app under test and capture its stack trace as scenario evidence

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-app-crash-diagnostics.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md), [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0066](../BE-0066-web-crawl/BE-0066-web-crawl.md) |
<!-- /BE-METADATA -->

## Introduction

When the app under test itself terminates abnormally mid-scenario, `bajutsu run` reports the same
kind of failure it would report for a missing element: an `ElementNotFound` (or a similar driver
failure), naming a selector that could not be found. Nothing in that message says the app went down.
Nothing in the scenario's own run directory (`runs/<run_id>/<sid>/`) holds the operating system's own
report for it either — on iOS, the `.ips` file macOS's `ReportCrash` writes for a faulting process; on
Android, the Java stack trace `logcat` prints for an uncaught exception, or the native tombstone the
device writes under `/data/tombstones`. A contributor debugging a red scenario has to notice the
pattern by elimination first — a selector that should exist and normally does. Only then can they go
find the platform's own report by hand, outside anything `bajutsu` itself produced.

This item adds both pieces: the detection and the capture. A scenario whose app goes down during a
step gets a distinct failure message that names the event. Its run directory gains an `app-crash/`
subdirectory next to its other evidence, holding the platform's own report or the closest counterpart
the platform offers.

This item covers `bajutsu run` first, on the iOS (XCUITest) and Android (adb) backends. It extends the
same underlying capture to `bajutsu crawl`'s existing detection of the same event. The web
(Playwright) backend and its own signals for it are left to a follow-up item, noted under
*Alternatives considered*.

## Motivation

`bajutsu` already distinguishes a kind of failure it did not cause from one it did.
`base.BackendCrashError` names the backend's own driver process going down — the resident XCUITest
runner's host, an adb resident server, a browser process
([`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py)).
The run pipeline recovers from it: it discards the dead lease and retries the whole scenario on a
fresh device.
[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md)
proposes copying that dying runner's own log, and on iOS its `.ips` report, into the failed scenario's
evidence. Both of these name a fault in the *backend* — the test infrastructure, not the app a team is
testing.

Nothing today plays the equivalent role for the *app under test*. `bajutsu crawl`, the AI-driven
exploration path, already detects this event.
`is_app_alive` ([`bajutsu/crawl/core/_functions.py:281`](../../bajutsu/crawl/core/_functions.py))
checks whether the element tree still shows the app's own UI. A crawl that finds a collapse records a
`Crash` ([`bajutsu/crawl/core/crash.py`](../../bajutsu/crawl/core/crash.py)) — the action path that led
to it, turned into a deterministic repro scenario by
[`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py). That detection is a UI heuristic, though,
with no platform-level evidence behind it: a `Crash` record carries the tap sequence that produced the
collapse, never the report the operating system itself wrote for the dying process. `bajutsu run`, the
deterministic path every CI gate depends on, has no equivalent check at all. A mid-scenario app crash
surfaces only as whatever generic failure the next action or query happens to raise.

That gap costs a contributor real time on a genuine app defect — the case this item is written for.
`BackendCrashError`'s own recovery already handles a driver or environment fault; this item does not
touch that path. A scenario that fails because the app went down looks, in today's report, identical
to one that fails because a selector's `id` was renamed or a screen never loaded: the same
`ElementNotFound`, the same "look at the screenshot and guess" starting point.

This item's observable outcome: once landed, a scenario whose app crashes fails with a message that
names the event. Its `runs/<run_id>/<sid>/app-crash/` directory holds the platform's own evidence —
`crash-<bundle>-<pid>.ips` on iOS, `logcat-crash.txt` on Android (and `tombstone.txt` too, where the
device allows it). A contributor opens the right file first, instead of ruling out two wrong
explanations before finding it.

## Detailed design

### A new, distinct failure: `AppCrashedError`

`bajutsu/common/drivers/base/app_crashed_error.py` (new file) defines:

```python
class AppCrashedError(RuntimeError):
    """The app under test itself terminated abnormally mid-scenario.

    Distinct from BackendCrashError: the backend's own driver process (the resident XCUITest
    runner, an adb resident server) is healthy and answering — only the app being tested is gone.
    Raised only where a driver has positively confirmed this event, never inferred from an ordinary
    ElementNotFound. The run pipeline treats it as a likely defect in the app itself, not backend
    infrastructure: it fails the scenario right away, with no retry, rather than respawning and
    re-running the way it does for a BackendCrashError (BE-0049 — retrying a crash-inducing defect
    risks turning a real failure into an absorbed flake).
    """
```

`AppCrashedError` shares no base class with `BackendCrashError`. The two name unrelated faults — the
app, versus the test infrastructure — and nothing in the pipeline needs to catch both as one kind.

### Detecting the event: a reactive driver signal, not a proactive poll

Checking whether the app is still running on every step would add a query to every scenario, green
runs included, to catch a failure mode that is rare by construction. Instead, `Driver`
([`bajutsu/common/drivers/base/driver.py`](../../bajutsu/common/drivers/base/driver.py)) gains one new
method. It is called only at the moment a step's own action or query is *already* about to fail:

```python
def app_crash_signal(self) -> str | None:
    """A short description of the app's crash, if this driver can confirm one right now, else None.

    Called reactively — only when a step's action or query has already raised a failure the caller
    is about to surface as the step's terminal exception — never polled proactively. A backend that
    cannot distinguish "the app went down" from "the app is merely not showing what was expected"
    returns None unconditionally; the caller then raises the original failure unchanged.
    """
    ...
```

The reactive call site sits at one place:
`bajutsu/common/orchestrator/loop/_functions.py`'s `_run_step_body`, where a driver's
`base.ElementNotFound` or a comparable action/query failure becomes the step's own terminal exception.
It does not sit inside `wait_for`'s polling loop, which already returns a plain `bool` instead of
raising. Right before that exception propagates, the step runner calls `driver.app_crash_signal()`. A
non-`None` result replaces the original exception with `base.AppCrashedError(signal) from original` —
chained, so the original failure stays attached for anyone reading the traceback. One shared call site
keeps the detection backend-agnostic (prime directive 3): each backend's own signal lives entirely
inside its own `app_crash_signal()`. A backend that has none — the web and fake backends, for now —
returns `None` always, so the orchestrator call site is a no-op for them.

### iOS: `app.state`, not the element tree

XCUITest already exposes a target app's process state through `XCUIApplication.state`, an enum whose
`notRunning` case answers "is it actually gone" directly. `crawl`'s `is_app_alive` instead infers the
same fact from an empty or unexpected element tree, which carries a real risk of a false positive: a
system alert covering the app's UI, or a deliberate `background` step in the scenario. `app.state`
sidesteps both. A backgrounded-but-alive app answers `runningBackgroundSuspended` or
`runningBackgroundActive`, never `notRunning`.

`BajutsuKit/Sources/BajutsuRunner/Router.swift` gains a new route, mirroring the shape
`/systemAlert/query` already takes for BE-0316's alert-button read. It asks the runner's own
`XCUIApplication` instance for its `.state` and returns it as JSON. `XcuitestDriver`
([`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`](../../bajutsu/common/drivers/xcuitest/xcuitest_driver.py))
implements `app_crash_signal()` by calling that route once. A `notRunning` answer becomes the signal
string; every other state, or a channel error the existing `XcuitestRunnerCrashError` classification
already owns, answers `None`.

### iOS: matching the `.ips` report, generalizing BE-0421's own technique

[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md)
already solved "find the right `.ips` file among everything macOS wrote to
`~/Library/Logs/DiagnosticReports`" for the runner's own `xcodebuild` process. Its match is by name
and time, narrowed by `pid` when the report's own header parses, deferred until after the retry loop
gives up so `ReportCrash`'s asynchronous symbolication has time to finish. This item reuses that exact
technique for the app under test's own binary name, in place of `xcodebuild-*`.

`XcuitestEnvironment`
([`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py))
already launches, and relaunches, the target app. It gains an `app_launched_at` timestamp, recorded
next to each launch the way BE-0421 records `_runner_spawned_at` next to `_runner_proc`. A new
`app_crash_artifacts(signal: str) -> list[tuple[str, bytes]]` joins the `RunEnvironment` protocol
([`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py)),
defaulting to `[]` — the same no-op shape `crash_artifacts()` (BE-0421) and
`request_device_replacement()` (BE-0354) already establish. It sweeps
`~/Library/Logs/DiagnosticReports` for a report named after the target's own executable, modified at or
after `app_launched_at`. The sweep stays best-effort throughout: a missing report, an unreadable
directory, or a non-macOS host all resolve to an empty list, never a raise.

### Android: `logcat`'s crash buffer first, a root-gated tombstone pull second

`AdbDriver`
([`bajutsu/common/drivers/adb/adb_driver.py`](../../bajutsu/common/drivers/adb/adb_driver.py))
implements `app_crash_signal()` with `adb shell pidof <package>`. An empty answer, where the app
should still hold a process, confirms the event without needing root or a parsed log line. The
scenario already knows the target's package name from its config — the same identifier the driver's
install and launch calls already use.

`AndroidEnvironment`
([`bajutsu/common/platform_lifecycle/environments/android/android_environment.py`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py))
implements `app_crash_artifacts()` in two layers, matching this item's own scoping decision to
capture both:

1. **`logcat`'s `crash` buffer**, always attempted, needing no elevated access. `adb logcat -b crash
   -d` dumps the ring buffer's retained content. The `FATAL EXCEPTION` block naming the target package
   is extracted and written as `logcat-crash.txt`. This is the one artifact guaranteed available on
   any AVD or real device the adb backend can already reach, and it already carries a complete Java
   stack trace for a managed-code crash — the common case.
2. **A tombstone pull**, best-effort and gated on root access. `adb root` — already a routine
   operation against the emulator images this backend targets — is followed by a pull of the most
   recent `/data/tombstones/tombstone_NN` whose modification time is at or after the app's last
   launch, matched the same name-and-time way as the iOS `.ips` sweep. A real device, a user build, or
   a refused `adb root` all resolve to skipping this layer silently. The event is still reported and
   `logcat-crash.txt` still lands; a device that refuses root loses only the native-frame detail a
   managed-code crash never needed in the first place.

### Wiring the capture into a failed scenario's run directory

`Lease` ([`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)) gains
`app_crash_artifacts: Callable[[str], list[tuple[str, bytes]]]`, defaulted through a module-level
no-op the same way `crash_artifacts` is (BE-0421), and wired in `pool.py`'s `lease()` closure alongside
it.

`pipeline.py`'s `_run_one_impl` gains a new `except base.AppCrashedError as crash:` branch, placed
before the existing `except BackendCrashError as crash:` branch (`pipeline.py:600`). The two name
disjoint faults, so neither branch can catch the other's case by mistake. Unlike that existing branch,
this one never enters the crash-retry loop. It builds the scenario's terminal `RunResult(ok=False,
failure=...)` right away, calls `lz.app_crash_artifacts(str(crash))`, and writes each `(name, content)`
pair through `writer.write_text(f"{sid}/app-crash/{name}", ...)` — the same per-scenario write shape
BE-0421 and
[BE-0415](../BE-0415-driver-call-trace-per-scenario/BE-0415-driver-call-trace-per-scenario.md) already
use. The failure string names the subdirectory directly, so a contributor reading it never has to
already know the evidence exists. A write problem is logged, never raised, matching BE-0421's own
posture: a diagnostic capture must never turn an already-decided failure into a different one. The
crashed lease itself is released back to the pool unchanged. The *device* is not at fault here, only
the app process that ran on it, so nothing here forces a device replacement the way a backend crash
does.

### Extending `crawl`'s own crash recording

`bajutsu/crawl/core/_coordinator.py`'s `record_crash` already appends a `Crash` when `is_app_alive`
reports a collapse. Its caller
([`bajutsu/crawl/core/_functions.py:662-667`](../../bajutsu/crawl/core/_functions.py)) already holds
the live environment reference the crawl loop drives. It gains one more call, right beside
`coord.record_crash(path)`, to the same `environment.app_crash_artifacts(signal)` this item adds for
`run` — reusing the identical capture, not a second implementation. The crawl CLI's own
`RunArtifactWriter` ([`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py)) writes the result under
`crash-<n>/app-crash/`, `<n>` the crash's own index in `ScreenMap.crashes`, alongside the repro
scenario [`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) already emits for that same path. A
crawl's own detection stays the UI-tree heuristic it already uses: `crawl` has no scenario step to hang
a reactive check off, unlike `run`. Only the capture, not the detection, is shared between the two
entry points.

### Proving the capture on a real crash, not only a stubbed one

A unit test can stub `~/Library/Logs/DiagnosticReports`, or a fake `logcat`/tombstone pull. Neither
proves the underlying platform mechanism this item depends on — `ReportCrash`'s own `.ips` write, or
`logcat`'s `crash` buffer — still behaves the way the design above assumes, on a real Simulator or
emulator. The showcase apps ([`demos/showcase/`](../../demos/showcase)) gain a debug-only "force a
crash" affordance: a button gated behind the same debug-build convention
[`ConformanceView.swift`](../../demos/showcase/ios/swiftui/Sources/ConformanceView.swift) already uses
for its own on-device diagnostics, calling `fatalError()` on iOS and throwing an uncaught exception on
Android's main thread. One new scenario per platform taps it.

Both new scenarios are *expected* to fail. The CI wrapper around them asserts the failure carries the
new `AppCrashedError` classification, and that `app-crash/` holds the expected file — the same way
`fault-injection (xcuitest)` already asserts a diagnosed failure shape rather than a green run
([`docs/ci.md`](../../docs/ci.md#the-ios-lane)). They land as a non-gating per-PR signal in
`ios-e2e.yml` / `android-e2e.yml`, next to `fault-injection (xcuitest)` and `network (adb)`. Newly
wired on-device coverage earns its stability there first, before any promotion into the required `E2E`
check — the same path every other new signal in those lanes has taken.

### Cost on the web backend and the fake backend

`PlaywrightDriver` and the fake test backend implement `app_crash_signal()` returning `None`
unconditionally, and `app_crash_artifacts()` returning `[]` unconditionally. The reactive call site and
the pipeline's write step both become no-ops for them, unchanged from today. Nothing in this item
changes what a web or fake-backend run captures.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Retry through the existing `BackendCrashError` recovery loop | Rejected during scoping: this event is a likely defect in the app itself, not a transient infrastructure blip. Respawning and retrying would spend the crash-recovery budget on a scenario likely to fail again, and risks absorbing a real regression as flakiness (BE-0049) instead of failing loudly. |
| Poll `app.state` / process liveness proactively, before every step | Rejected during scoping: it adds one driver round-trip to every step of every scenario, green runs included, to catch a failure mode that is rare by construction. The reactive design still catches the event at the exact step it happened, since that step's own action or query is already failing. |
| Android: `logcat`'s crash buffer only, no tombstone pull | Rejected: it loses the native (NDK) crash's full backtrace, keeping only the abridged summary `logcat` itself prints. Kept as the always-available baseline; the tombstone pull layers richer detail on top where the device allows it, rather than replacing it. |
| Android: a rooted tombstone pull only, no `logcat` fallback | Rejected: a real device, a user build, or an emulator image that refuses `adb root` would then capture nothing at all. `logcat`'s crash buffer needs no elevated access and already carries a complete stack trace for the common managed-code case. |
| A new scenario assertion (for example, `assert: appCrashed: false`) | Rejected: the event already ends the scenario through the step's own action/query failure. There is no later point in the scenario where an assertion could still run to check for it. The showcase's own test scenario instead asserts the failure's *shape* from outside the run, the way `fault-injection (xcuitest)` already does. |
| Gate the capture behind an opt-in `capturePolicy` rule, matching `video` / `deviceLog` | Rejected for the reason BE-0421 gave for its own artifact: the capture runs once, only on a scenario already ending in failure. Its cost is one bounded sweep or log read, not a standing per-step overhead worth gating behind an explicit ask. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `base.AppCrashedError` (new file); `Driver.app_crash_signal()` added to the protocol
      shape, returning `None` unconditionally on the web and fake backends.
- [ ] Unit 2 — iOS: a new BajutsuRunner route reading `XCUIApplication.state`;
      `XcuitestDriver.app_crash_signal()` calling it reactively and classifying `notRunning` as the
      signal.
- [ ] Unit 3 — iOS: `XcuitestEnvironment.app_launched_at`, recorded at each app launch/relaunch;
      `app_crash_artifacts()`'s name-and-time `.ips` sweep, generalizing BE-0421's own technique to
      the target app's binary name.
- [ ] Unit 4 — Android: `AdbDriver.app_crash_signal()` via `adb shell pidof <package>`.
- [ ] Unit 5 — Android: `AndroidEnvironment.app_crash_artifacts()` — the always-attempted `logcat`
      crash-buffer extraction, plus the best-effort, root-gated tombstone pull.
- [ ] Unit 6 — `RunEnvironment.app_crash_artifacts()` protocol shape and no-op defaults;
      `Lease.app_crash_artifacts` wired through `pool.py`'s `lease()` closure.
- [ ] Unit 7 — The reactive call site in `bajutsu/common/orchestrator/loop/`: catching a step's own
      action/query failure, asking `driver.app_crash_signal()`, and re-raising as `AppCrashedError`
      when it answers.
- [ ] Unit 8 — `pipeline.py`'s new `except base.AppCrashedError` branch: no retry, the crashed lease
      released unchanged, artifacts written under `f"{sid}/app-crash/"`, the failure string naming
      that subdirectory.
- [ ] Unit 9 — `crawl`'s own integration: the additional `environment.app_crash_artifacts()` call
      beside `record_crash`, written under `crash-<n>/app-crash/` by the crawl's own
      `RunArtifactWriter`.
- [ ] Unit 10 — Showcase fixtures: a debug-only "force a crash" affordance on iOS (SwiftUI) and
      Android (Compose), one scenario per platform exercising it, wired as a non-gating per-PR signal
      in `ios-e2e.yml` / `android-e2e.yml`.
- [ ] Unit 11 — Docs: `docs/evidence.md` (+ `docs/ja/`) gains this artifact kind; `docs/ci.md`
      (+ `docs/ja/`) notes the showcase signal lane; `docs/architecture.md` (+ `docs/ja/`)
      cross-references the no-retry app-crash path against the existing backend-crash retry section.
- [ ] Unit 12 — Tests: `app_crash_signal()` returning `None` on an ordinary `ElementNotFound` (no
      false positive on an ordinary missing selector) for both backends; the iOS `.ips` sweep and the
      Android `logcat`/tombstone capture against stubbed directories and stubbed `adb` output; a
      `pipeline.py` test asserting no retry, the `app-crash/` directory, and the failure-string shape;
      a web/fake-backend test asserting the no-op defaults are unchanged.

## References

- [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md) —
  the runner's own crash-report capture this item complements; the source of the name-and-time `.ips`
  matching technique this item reuses for the app under test
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) — the
  crawl `Crash` record and `is_app_alive` heuristic this item's `crawl` integration builds on
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) —
  the existing backend-crash retry semantics this item's no-retry design deliberately diverges from
- [BE-0066](../BE-0066-web-crawl/BE-0066-web-crawl.md) — the web (Playwright) backend, whose own
  signals for this event (a `page.on("crash")` renderer event, an uncaught `pageerror`) are left to a
  follow-up item
- [`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py) —
  `BackendCrashError`, the sibling fault this item's `AppCrashedError` is deliberately not a subclass
  of
- [`bajutsu/common/drivers/base/driver.py`](../../bajutsu/common/drivers/base/driver.py) — the
  `Driver` protocol `app_crash_signal()` joins
- [`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py) —
  `_run_step_body`, whose action/query failure boundary is this item's one reactive call site
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py) — `_run_one_impl`,
  gaining the new `except base.AppCrashedError` branch beside the existing `BackendCrashError` one
- [`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py) —
  the protocol `app_crash_artifacts()` joins, next to `crash_artifacts()` (BE-0421)
- [`bajutsu/crawl/core/_coordinator.py`](../../bajutsu/crawl/core/_coordinator.py) — `record_crash`,
  next to which this item's crawl-side capture call is added
- [`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py) — `RunArtifactWriter`,
  the single write boundary (BE-0331) this item's artifacts cross
- [`docs/ci.md`](../../docs/ci.md#the-ios-lane) — `fault-injection (xcuitest)`, whose non-gating,
  failure-shape-asserting placement this item's showcase scenarios follow
