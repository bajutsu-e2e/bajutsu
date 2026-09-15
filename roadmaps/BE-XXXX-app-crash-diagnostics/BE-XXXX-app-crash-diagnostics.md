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

This item covers `bajutsu run` first, on the iOS (XCUITest) and Android (adb) backends — iOS scoped to
the Simulator, where a real device's own gaps in evidence (see *iOS: `app.state`*) leave
`app_crash_signal()` answering `None`, the same "cannot confirm" answer a backend with no signal at all
gives. It extends the same underlying capture to `bajutsu crawl`'s existing detection of the same
event. The web (Playwright) backend and its own signals for it are left to a follow-up item, noted
under *Alternatives considered*.

## Motivation

`bajutsu` already distinguishes a kind of failure it did not cause from one it did.
`base.BackendCrashError` names the backend's own driver process going down — the resident XCUITest
runner's host, an adb resident server, a browser process
([`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py)).
The run pipeline recovers from it: it discards the dead lease and retries the whole scenario on a
fresh device.
[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md)
copies that dying runner's own log, and on iOS its `.ips` report, into the failed scenario's evidence.
Both of these name a fault in the *backend* — the test infrastructure, not the app a team is testing.

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
the `.ips` report `ReportCrash` wrote, under its own name, on iOS; `logcat-crash.txt` on Android
(and `tombstone.txt` too, where the device allows it). A contributor opens the right file first,
instead of ruling out two wrong explanations before finding it.

## Detailed design

### A new, distinct failure: `AppCrashedError`

`bajutsu/common/drivers/base/app_crashed_error.py` (new file) defines:

```python
class AppCrashedError(RuntimeError):
    """The app under test itself terminated abnormally mid-scenario.

    Distinct from BackendCrashError: the backend's own driver process (the resident XCUITest
    runner, an adb resident server) is healthy and answering — only the app being tested is gone.
    Raised only where a driver has positively confirmed this event, never inferred from an ordinary
    ElementNotFound. Constructed and consumed entirely within the reactive check described below,
    for its message text alone: it never escapes to `pipeline.py`, so it carries none of
    `BackendCrashError`'s recovery semantics and needs no shared base class with it.
    """
```

Earlier drafts of this item raised `AppCrashedError` out of `run_scenario`, with a new
`pipeline.py` `except` branch mirroring `BackendCrashError`'s crash-retry handling. Review rejected
that shape (see *Alternatives considered*). A scenario whose backend crashed has no state left to
salvage. A scenario whose *app* crashed still has a driver, a backend process, and a video recording
running — the only thing gone is the app. Ending such a scenario through a side channel that bypasses
`run_scenario`'s own `RunResult` assembly would throw all three away, for no reason tied to why the
app crashed.

The design below keeps the classification in-band instead. `AppCrashedError` never leaves the one
call site that raises it. That call site turns it into an ordinary terminal step failure, the shape
the existing pipeline already knows how to finish correctly, and sets a new `app_crashed: bool = False`
field on `StepOutcome`
([`bajutsu/common/orchestrator/types/step_outcome.py`](../../bajutsu/common/orchestrator/types/step_outcome.py))
to `True` — the signal `pipeline.py` reads afterward (see *Wiring the capture*) to decide whether to
copy the app's own evidence in, the same way `RunResult.steps[-1]` already carries everything else a
report needs to know about how a scenario ended.

### Detecting the event: a reactive signal, checked once a step has already failed

Checking whether the app is still running on every step would add a query to every scenario, green
runs included, to catch a failure mode that is rare by construction. Instead, this item defines a
narrow, opt-in capability protocol, following the shape
[`InterruptionPolicyTarget`](../../bajutsu/common/drivers/base/interruption_policy_target.py) and
[`SettledReadProvider`](../../bajutsu/common/drivers/base/settled_read_provider.py) already establish
for a driver feature only some backends have:

```python
@runtime_checkable
class AppCrashSignal(Protocol):
    """A backend that can positively confirm the app under test has crashed.

    A narrow opt-in, like `InterruptionPolicyTarget`: a backend that does not implement it is simply
    never asked, and the run is otherwise unchanged. Only XCUITest and adb need it today.
    """

    def app_crash_signal(self) -> str | None:
        """A short description of the app's crash, if this driver can confirm one right now.

        Called only once a step's own action, wait, or assertion has already failed — never polled
        proactively. Answers `None` when the driver cannot tell "the app went down" from "the app is
        merely not showing what was expected".
        """
        ...
```

Not a new member of the monolithic `Driver` protocol
([`bajutsu/common/drivers/base/driver.py`](../../bajutsu/common/drivers/base/driver.py)). `Driver` is
`@runtime_checkable`, so every one of its members is required for `isinstance(x, base.Driver)` to
answer `True` at all. This repository has four full implementers of it today: `XcuitestDriver`,
`AdbDriver`, `PlaywrightDriver`, and
[`XcuitestLiveDriver`](../../bajutsu/common/drivers/xcuitest_live/xcuitest_live_driver.py) — a fourth
backend, added by BE-0238, that drives a real iOS device over W3C WebDriver — plus the fake test
backend. It also has narrower wrappers that intentionally implement only part of `Driver`'s surface,
such as [`WebContextDriver`](../../bajutsu/common/drivers/webview/web_context_driver.py), the driver
the run loop swaps in for a `web` block. Adding a required member to `Driver` would force every one
of those to grow a stub, `WebContextDriver` included, for a check this item never intends to run
inside a `web` block. `AppCrashSignal` sidesteps all of it: only `XcuitestDriver` and `AdbDriver`
implement it, and the call site below asks for it with `isinstance` — the same way BE-0406 already
asks for `InterruptionPolicyTarget`.

`TracingDriver`
([`bajutsu/common/drivers/tracing.py`](../../bajutsu/common/drivers/tracing.py)), the `--trace-driver`
proxy, installs each protocol in its own `_PROTOCOLS` tuple as real instance attributes, gated on
`isinstance(wrapped, protocol)`, so that `isinstance` reads correctly on the proxy itself. `AppCrashSignal`
joins that tuple, the same way `InterruptionPolicyTarget` already does: wrapping `XcuitestDriver` or
`AdbDriver` installs it, wrapping anything else — `PlaywrightDriver`, the fake backend,
`XcuitestLiveDriver`, `WebContextDriver` — simply does not, and every other capability the proxy
tracks is unaffected either way.

`bajutsu/common/orchestrator/loop/_step_runner.py` dispatches every step to one of four handlers by
kind — `_handle_if`, `_handle_for_each`, `_handle_web`, or `_handle_action` (the last one covers
`wait`, `assert`, `handleSystemAlert`, and every actuating step) — and each one settles its own
`outcome.ok` differently. `_handle_action`'s own `outcome.ok, outcome.reason,
outcome.assertion_results = ok, reason, results` line, once its tip-dismiss and alert-guard retries
are done, is not the step's last word either: an undeclared interruption or a failed `extract` can
still flip `outcome.ok` to `False` afterward, inside the same handler. A check keyed to that one line
alone — the shape an earlier draft of this item used — sees `_handle_action`'s own terminal failures
late, and never sees `_handle_if`'s or `_handle_for_each`'s condition-query failure or
`_handle_web`'s `within`-selector failure at all, since neither reaches that line.

What every handler *does* share is `self.state.outcomes.append(outcome)` — but not once each.
`_handle_if`, `_handle_for_each`, and `_handle_web` call it exactly once, the last thing they do with
`outcome` before returning. `_handle_action` calls it twice: once at its own end, and once more on an
early return, its `UncoveredSystemAlertLocale` branch (`_step_runner.py:457`), which sets
`outcome.ok = False` and returns well before that end is ever reached. A check keyed to one call site
per handler — the shape an earlier draft of this item used — would miss that second `_handle_action`
append: the one exit the file's own comments already single out as the exit that skips every other
shared post-step step, for the same reason. This item adds one more shared step,
`self._finish_outcome(active_driver, outcome)`, called at all five call sites — one apiece for the
other three handlers, two for `_handle_action` — in place of the bare `append`. It does the append
itself, so a step kind added later still needs no wiring here, the same property
`_drain_step_interruptions` already gives the interruption check it shares across the same four
handlers. A fast-suite assertion that no `self.state.outcomes.append` survives outside
`_finish_outcome` keeps a sixth call site, added later, from reopening the same gap silently.
`_finish_outcome` checks `isinstance(active_driver, base.AppCrashSignal)` and `outcome.ok is False`,
together, right before that append — plus a scenario-level exemption, described next, for the one
action that deliberately terminates the app itself.

A nested step's failure reaches `_finish_outcome` more than once by construction, not only at the
handler that first observed it. `_run_if` and `_run_for_each` both run their body through
`self.exec_steps` — the same re-entrant loop an interrupt's own recovery uses — so a crash three
levels deep, an action inside a `forEach` inside an `if`, settles three wrapping outcomes in turn: the
action's own, the `forEach`'s, and the `if`'s, each a separate call to `_finish_outcome`. The `after`
phase adds one more for every cleanup step it runs against an app that is already gone — including a
step that runs *because* an earlier `relaunch` failed, against the app that `relaunch` itself just
terminated — dispatched through `_dispatch_after`'s own `run_phase(steps, after_outcomes, "after", …)`
call ([`_functions.py:824-828`](../../bajutsu/common/orchestrator/loop/_functions.py)), a route
separate from the main steps' own loop, building its own fresh `StepLoopState`
(`_functions.py:994`) — not through `_run_recovery`
([`_step_runner.py:70`](../../bajutsu/common/orchestrator/loop/_step_runner.py)), which is BE-0314's
own re-entrant path for an *interrupt's* recovery steps (`self.state.running_recovery = True` around
the same `exec_steps`, `_interrupt_guard.py:70`), not the `after` phase. `_run_recovery` is a third,
distinct probe multiplier this paragraph has to account for on its own terms: an interrupt whose
recovery steps run against an app that is already gone settles one outcome per recovery step, each an
ordinary (non-`relaunch`) failure neither latch bounds — the same cost the next paragraph already
prices in for depth and `after` rules, just from a third source. Keying the `relaunch`
exemption to `outcome.action` alone misses exactly that case: a failing `relaunch`'s own outcome skips
the probe, but the `forEach`/`if` outcomes wrapping it, and every `after: on: fail` step dispatched
afterward, carry a different `outcome.action` and would each probe fresh, reading `app.state`'s honest
`notRunning` as a fresh confirmed crash. The exemption therefore has to be a property of the
*scenario*, not of one outcome: the same mutable object `run_scenario` creates once and hands to every
`run_phase` call through its closure, the same way it already shares `live_bindings` across phases
(`_functions.py:690`, `:699`) — carried on `StepLoopState` next to `bindings`, not on `_LoopConfig`.
`_LoopConfig` is `@dataclass(frozen=True)`, documented as "the run-invariant inputs the step loop reads
but never mutates"; `mailbox` and `progress` are callables the loop only invokes, never state it
writes through, so they set no precedent for one. `StepLoopState` already is that precedent:
`_run_steps` builds a fresh one per phase, but hands each the same `bindings` object `run_scenario`
created once, which is exactly the scenario-scoped sharing this latch needs, at the cost of one more
parameter alongside `bindings` rather than a new field on a class documented never to hold one.
`_finish_outcome` sets it the moment it
sees `outcome.action == "relaunch"` and `outcome.ok is False`, before ever calling
`app_crash_signal()`, and checks it first on every later call: once set, no later outcome in the same
scenario probes at all, wrapping outcomes and `after`-phase steps included, and none is misread as a
crash the check itself caused.

That same object also latches a *confirmed* crash, the first time `_finish_outcome` raises and catches
`AppCrashedError`, so a later outcome in the same propagation folds the already-known signal into its
own `outcome.reason` instead of probing `app_crash_signal()` again. It does not latch on an
unconfirmed answer, though, and that asymmetry is deliberate: a `None` from one step teaches nothing
about whether the *next* step's own failure is a crash, so latching there would risk missing a real
one. The bound this bought is narrower than "once per scenario" for that reason — it holds for the
relaunch and confirmed-crash cases above, not for an ordinary failure with no crash behind it, which
still pays one probe per settling outcome (nesting depth, plus one per failing `after` step) the same
way an earlier draft of this item claimed to avoid entirely. That cost is still bounded by how deep a
scenario nests and how many `after` rules it dispatches on failure — figures usually small in
practice — and it only runs on a step that has already failed, unlike the proactive per-step polling
*Alternatives considered* rules out below for adding cost to every green run.

This reactive shape has one structural blind spot worth naming rather than discovering later: a crash
caused by a scenario's own *last* step, where that step's own actuation still reports `ok=True` — a
`tap` the runner delivered before the app died, an `assert` that read a tree captured a moment earlier
— is never probed. Nothing runs after it to fail and reach `_finish_outcome`, and scenario-level
`expect` does not close the gap either: `_evaluate_expect` produces `AssertionResult`s, not
`StepOutcome`s, so it never reaches `_finish_outcome`
([`_functions.py:742-752`](../../bajutsu/common/orchestrator/loop/_functions.py)). Closing it would
mean an unconditional end-of-scenario probe — the every-green-run cost this reactive design exists to
avoid — so this item accepts the gap by construction rather than widen the trigger, and instead
requires a scenario written to *exercise* this signal to end with a step after the crash-triggering
one (Unit 11's showcase scenarios each do: tap the crash trigger, then take one more step against the
now-dead app, so it is that later step — not the tap itself — that fails and gets probed). A scenario
whose last step is the crash trigger stays green by this design's own construction.

`_finish_outcome` calls `active_driver.app_crash_signal()` only when neither latch is set. A
non-`None` answer raises `base.AppCrashedError(signal)` immediately and catches it in the same
expression, folding its message into `outcome.reason` and setting both `outcome.app_crashed` and the
confirmed-crash latch to `True` — never letting the exception itself propagate past this one point.
`active_driver` is already whichever driver actuated this step, and a `web` block's inner steps and its
own wrapping outcome answer differently: `_handle_web` runs `step.web.steps` on a `WebContextDriver` it
constructs for the block (`self.exec_steps(step.web.steps, web_driver)`), so *those* inner steps'
`_finish_outcome` calls (through `_handle_action`) see `active_driver = web_driver`, and `isinstance`
answers `False` there — a genuine no-op, matching this item's `web`-backend scope from the
Introduction. `_handle_web`'s own wrapping outcome is a separate, fifth call to `_finish_outcome`,
deliberately passed the block's `active_driver` parameter instead
([`_step_runner.py:287-290`](../../bajutsu/common/orchestrator/loop/_step_runner.py), "not the inner
`web_driver`") — the *native* driver that was active before the block switched context. So a native
host crash that surfaces as the wrapping `web` step's own failure (an inner step failing because the
native host died out from under the `WebView` bridge) is not a no-op: `_finish_outcome` probes the
native driver exactly as it would for any other failing step.

### iOS: `app.state`, not the element tree

XCUITest already exposes a target app's process state through `XCUIApplication.state`, an enum whose
`notRunning` case answers "is it actually gone" directly. `crawl`'s `is_app_alive` instead infers the
same fact from an empty or unexpected element tree, which carries a real risk of a false positive: a
system alert covering the app's UI, or a deliberate `background` step in the scenario. `app.state`
sidesteps both. A backgrounded-but-alive app answers `runningBackgroundSuspended` or
`runningBackgroundActive`, never `notRunning`.

A `notRunning` answer is not, by itself, proof of a crash on every platform: on a real device an OS
memory-pressure kill or an unfinished launch could answer the same way. Neither applies to the
Simulator, but the XCUITest backend is not Simulator-only: `xcuitest.deviceType: device`
([`xcuitest_config.py:20`](../../bajutsu/common/config/schema/xcuitest_config.py)) drives a real
iPhone through this same `XcuitestDriver` and `XcuitestEnvironment`, where the Simulator's own
constraints do not hold — a real device *does* jetsam-kill a foreground app under memory pressure —
and where the `.ips` sweep below has nothing to read anyway (a real-device crash never lands in the
host's own `~/Library/Logs/DiagnosticReports`, and `self._bundle_id` is `None` on a real device
([`xcuitest_environment.py:319`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)),
since its build is installed out of band rather than through the `_prepare_simulator` path this item's
`Info.plist` read depends on). This item scopes to the Simulator only, the same way it already scopes
the web backend out to a follow-up item:
`XcuitestDriver.app_crash_signal()` answers `None` outright on a real device — the same "cannot
confirm" answer a backend with no signal at all gives — rather than misreading a real-device OS kill,
or a `deviceType: device` target's own gap in evidence, as a confirmed app crash. One scenario step
does deliberately terminate the app on either device type —
`relaunch`, whose iOS path is `device_relauncher`'s `e.terminate(bundle_id)` then `e.launch(...)`
([`relaunchers.py:64`, `:78`](../../bajutsu/common/platform_lifecycle/relaunchers.py)) — not
`_resume_warm`'s identical-looking pair at `xcuitest_environment.py:855-856`, BE-0291's *cross-lease*
warm-reuse path. `XcuitestEnvironment.relauncher()` does override `_DeviceEnvironment`'s implementation
(see *iOS: matching the `.ips` report*, for the unrelated reason of re-stamping `app_launched_at`), but
that override only wraps the `RelaunchFn` `device_relauncher` already returns — it never calls
`_resume_warm`, which stays a separate, cross-lease code path this step never touches either way. So
`_finish_outcome` skips the probe, and suppresses every later one this same scenario would otherwise
make (see *Detecting the event*), once a `relaunch` step itself fails. That guard matters less than it
looks, though: `relaunch`'s own closure calls `readiness.await_ready(...)`
([`relaunchers.py:79`](../../bajutsu/common/platform_lifecycle/relaunchers.py)) purely for its side
effect and discards the `ReadinessResult` it returns, and `await_ready` itself never raises — a timed-out
wait returns `ReadinessResult(False, "timeout", …)` just like a successful one returns `True`
([`readiness.py:170`](../../bajutsu/common/platform_lifecycle/readiness.py)). So a `fatalError()` during
the *new* launch — the exact case Unit 11's showcase affordance exists to trigger — never fails the
`relaunch` step at all: `await_ready` times out silently, the closure returns, and the step reports
`ok=True`. Nor does the surrounding tooling turn that into a failure on its behalf: `e.terminate` swallows
its own `CalledProcessError` outright
([`env.py:176`](../../bajutsu/common/backend_cli/simctl/env.py)), and on the rare device/tooling failure
where `e.launch`'s own `CalledProcessError` does raise, `_run_step_body`'s exception net does not name it
— it escapes `run_scenario` entirely rather than becoming this step's `outcome.ok = False`, so
`_finish_outcome` is never reached for it either. The only way a `relaunch` step's own outcome actually
carries `ok=False` today is the alert-guard/mid-wait-recovery-failure path (*Detecting the event*'s
nested-failure paragraph above) — genuinely unrelated to the app's own health, exactly the case the
exemption exists for. A crash during the new launch is not lost, only attributed one step later: it
fails the very next step that touches the app, an ordinary (non-`relaunch`) outcome neither latch has
any reason to skip, so `_finish_outcome` probes it exactly as it would any other failure. Every other
check also runs only once a step has already failed, *after* the app was observed running through every
earlier step of the same scenario, so a launch that never completed under a non-`relaunch` step is not a
case it meets. A `notRunning` answer anywhere else means the app that was running a moment ago is not
running now, on a host with no other way for that to happen.

A new route joins
[`BajutsuKit/Sources/BajutsuRunner/openapi.yaml`](../../BajutsuKit/Sources/BajutsuRunner/openapi.yaml),
mirroring `/systemAlert/query`'s shape (BE-0316): a request body, a JSON reply carrying the state. The
generated `APIHandler`
([`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift))
gains the matching method. Its provider implementation asks the runner's own `XCUIApplication` for
`.state`. `RunnerServer.swift`, not `Router.swift`, is what actually serves a request today: it
constructs an `APIHandler` and registers its generated routes. `Router.swift` is kept only for parity
tests and answers nothing in a real run. A route added only to `Router.swift`, as an earlier draft of
this item specified, would leave `XcuitestDriver`'s request 404ing against the real server.

`XcuitestDriver`
([`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`](../../bajutsu/common/drivers/xcuitest/xcuitest_driver.py))
gains an `is_real_device: bool = False` constructor argument, threaded from `make_driver` the same way
`device_os` (BE-0358) already is. `app_crash_signal()` checks it first and answers `None` immediately
when `True`, before calling the route at all — the Simulator-only scope decided above. Otherwise it
implements `app_crash_signal()` by calling that route once. A `notRunning` answer becomes the signal
string. Every other state answers `None`. A channel error reaching this call is not swallowed into
`None`: it is the existing `XcuitestRunnerCrashError`, a `BackendCrashError`, and this item leaves it
to propagate unchanged, straight into the recovery path that already owns it. A route failing right
after the step's own selector failure is ordinary contention, not evidence the channel is unrelated to
this step.

### iOS: matching the `.ips` report, reusing BE-0421's landed sweep

[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md)
is `Implemented` (PR [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)): it already finds the
right `.ips` file among everything macOS wrote to `~/Library/Logs/DiagnosticReports` for the runner's
own crashed `xcodebuild` process, through
[`bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py)'s
`_diagnostic_reports_dir()` and `_reports_since(reports_dir, pattern, since)` — a generic name-and-time
match with no `xcodebuild` in its own signature — plus `_reported_pid()` to narrow further when a
report's header parses. This item reuses `_reports_since` directly for the app under test's own
binary, rather than writing a second sweep over the same directory: a fix to one match rule (a new
`.ips` header format, a `DiagnosticReports` relocation) would otherwise silently leave the other wrong.

Narrowing by PID is not available here the way it is for BE-0421's own report: XCTest's public
`XCUIApplication` surface has no PID accessor, and nothing in `BajutsuKit/` reads one today. This item
narrows by the Simulator's UDID instead. A Simulator app's `.ips` report carries the executable's full
install path in its header (`.../CoreSimulator/Devices/<udid>/data/Containers/Bundle/Application/…`),
which names the specific Simulator the crashed process ran on. `Lease`
([`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)) already records the leased
device's own `udid`, so a new `_app_crash_reports(launched_at, udid)` — a sibling of BE-0421's own
`_crash_reports(spawned_at, pid)`, in the same module — accepts a report from `_reports_since` only
when its path names that same `udid`, in place of `_reported_pid`'s check. That disambiguates two
Simulators running the identical target binary in the same window, the case a CI host running two
lanes in parallel (`--workers 2`) can produce, without needing a PID at all.

`_reports_since(reports_dir, pattern, since)` still matches by filename, though, and a `.ips`
report's filename names the process that crashed (`Showcase-2026-…ips`), never the bundle id —
BE-0421 passes the literal `"xcodebuild-*.ips"` for its own known process. `XcuitestEnvironment`
holds `ios.bundle_id` (`self._bundle_id`, `com.example.Showcase`), which is not that name and never
matches a report's filename. This item instead reads `CFBundleExecutable` from the installed app's
own `Info.plist`, at `Path(ios.app_path) / "Info.plist"`, once at launch time — the one plist key
every iOS bundle is required to declare — and builds the sweep's pattern from it, the same way
`ios.app_path` already names the bundle `e.install` installs from.

`XcuitestEnvironment`
([`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py))
already launches the target app, and already knows its own `udid`. It gains an `app_launched_at`
timestamp, recorded next to that launch — but a `relaunch` step's own launch, the one this section just
distinguished from `_resume_warm`'s, runs through `device_relauncher`'s closure over
`(udid, run, extra_env)`, with no `XcuitestEnvironment` in scope to update the marker on. Left alone,
`app_launched_at` would stay frozen at the lease's original cold launch, so a crash following a
mid-scenario `relaunch` would sweep `DiagnosticReports` with a `since` reaching back before the
relaunch — wide enough to attach a `.ips` from a crash that `relaunch` itself already superseded.
`XcuitestEnvironment` overrides `relauncher()` — `_DeviceEnvironment`'s own implementation, unchanged
otherwise — to wrap the `RelaunchFn` `device_relauncher` returns: call it, then record
`app_launched_at` the same way the cold-launch site already does. `AndroidEnvironment` needs no
matching override: it already overrides `relauncher()` itself
([`android_environment.py:326`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)),
and its `e.launch` there is one of the three sites Unit 5 already names.

`_DeviceEnvironment.crawl_reset()`
([`ios.py:124`](../../bajutsu/common/platform_lifecycle/environments/ios.py)) is a third iOS launch
site with the identical shape — `e.terminate(bundle_id)` then `e.launch(...)` — that `crawl`'s own
`reset` callable runs on every frontier revisit
([`cli.py:300`](../../bajutsu/crawl/cli.py)), not through `relauncher()` at all. Left unpatched, the
same staleness the `relauncher()` override just closed reopens on this path: a crawl records several
crashes per run (`current_fp = None; continue`,
[`_functions.py:668-669`](../../bajutsu/crawl/core/_functions.py)), so the second crash's sweep would
reach back before its own `crawl_reset` and accept the *first* crash's `.ips` report instead — the
wrong crash, in a directory whose whole purpose is attributing the report correctly.
`XcuitestEnvironment` overrides `crawl_reset()` too, the same shape as the `relauncher()` override:
call `_DeviceEnvironment`'s version, then record `app_launched_at` next to it. `AndroidEnvironment`
needs no matching override here either — its own `crawl_reset()`'s `e.launch`
([`android_environment.py:410`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py))
is the third of the three sites Unit 5 already names.

A new `app_crash_artifacts() -> list[tuple[str, bytes]]` joins the `RunEnvironment` protocol
([`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py)),
next to `take_crash_snapshot()`, but plainer: `take_crash_snapshot()` returns a *thunk*, because a
backend crash is captured where it is first observed and then deferred until the pool releases the
lease, so a concurrent worker's next launch on the same warm environment cannot overwrite the frozen
match criteria first. This item's capture has no such race to defer past — `pipeline.py` calls it
directly (see *Wiring the capture*), still holding this same scenario's own lease, well before that
lease is ever released — so `app_crash_artifacts()` reads `app_launched_at` live and returns the
finished list directly, no thunk needed. `RunEnvironment` is a structural protocol no concrete class
subclasses, so `take_crash_snapshot()` has no inherited default either: `WebEnvironment`,
`AndroidEnvironment`, and `_DeviceEnvironment` (which `FakeEnvironment` inherits) each already declare
their own one-line `return` for it. `app_crash_artifacts()` follows the same shape, minus Android:
`WebEnvironment` and `_DeviceEnvironment` (which `FakeEnvironment` inherits) each gain their own
`return []`, while `XcuitestEnvironment` and `AndroidEnvironment` each override it with the real
capture this section and the next describe. Every leased environment needs one of the two, since
`pool.py`'s `lease()` reads the method off every leased environment, on every lease, not only a
crashed one.

`ReportCrash` may not have finished writing the report the instant the app dies. `_app_crash_reports`
polls `~/Library/Logs/DiagnosticReports` for up to a few seconds for a report matching the target's
executable name and this environment's own `udid`, modified at or after `app_launched_at`. That poll
is a short, bounded wait inside this one method, not a retry of the scenario: the scenario still fails
once, immediately, regardless of whether the sweep finds anything. The sweep, and everything it does,
stays wrapped in one `try`/`except Exception` for its whole body, not only its final write. A failure
in the directory scan or the read is exactly as unable to change the app's own crash verdict as a
missing report is, so it resolves to an empty list the same way. A non-macOS host resolves to `[]` the
same way, immediately.

### Android: `logcat`'s crash buffer first, a root-gated tombstone pull second

`AdbDriver`
([`bajutsu/common/drivers/adb/adb_driver.py`](../../bajutsu/common/drivers/adb/adb_driver.py))
implements `app_crash_signal()` with `adb shell pidof <package>`. `AdbDriver` is constructed from a
serial and a handful of injected callables today. It holds no package name, and neither does
`backends.make_driver`, which builds it. The target's package name already reaches
`AndroidEnvironment` — its `install` / `pm clear` / `force_stop` / `launch` calls all take it from the
target's own config (`targets.<name>.android.package`) — but not the driver. `make_driver`
([`bajutsu/common/backends.py`](../../bajutsu/common/backends.py)) gains a `package: str | None = None`
keyword, threaded into `AdbDriver.__init__` the same way it already threads `device_os` (BE-0358): a
plain constructor argument rather than a `Driver` member. `Driver` is `@runtime_checkable`, with no
shared base class, so a data member there would be a declaration every backend and every inline test
double has to repeat.

An empty `pidof` answer, where the app should still hold a process, is necessary but not sufficient.
It also matches a launch that never completed, or a termination this item has no scenario-level cause
for: Android has no jetsam-style OS kill under normal test conditions, but an ordinary process exit
answers `pidof` identically to a crash. `adb shell dumpsys activity exit-info <package>` reports the
platform's own `ApplicationExitInfo` history for the package, each entry timestamped, distinguishing a
`CRASH` or `CRASH_NATIVE` reason from `ANR`, `LOW_MEMORY`, or `USER_REQUESTED`. It is available from
API 30 onward, so it is available on the API 34 AVD this repository's CI already boots. The history
persists across process lifetimes, though, so its most recent entry alone is not reliable: an earlier
scenario's crash on the same package can still be the newest entry `dumpsys` reports if the current
one exited for an unrelated reason with no `ApplicationExitInfo` recorded yet. `AdbDriver` gains a
`launched_at: Callable[[], float | None] | None = None` constructor argument, an injected callable
reading `AndroidEnvironment.app_launched_at` live, the same seam `fetch_clock` already uses for a
per-call read rather than a value frozen at construction. `app_crash_signal()` reads the exit-info
history once, right after `pidof` answers empty, and confirms the event only when its *newest* entry
reports `CRASH`/`CRASH_NATIVE` *and* that entry's own timestamp is at or after `launched_at()` — ruling
out a stale entry from before this launch. Either condition failing answers `None`, the same "cannot
confirm" answer a backend with no signal at all gives. This is the corroboration `app.state`'s
`notRunning` gets for free from the Simulator's own constraints above; Android's own platform-reported,
time-bound exit reason gives adb the equivalent positive confirmation.

`AndroidEnvironment`
([`bajutsu/common/platform_lifecycle/environments/android/android_environment.py`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py))
gains the same `app_launched_at` tracking as the iOS environment, recorded at each of its three launch
call sites (`e.launch(package, launch_env)`). It reads from the device's own clock (`adb shell date`)
at launch time rather than the host's, so a launch marker compared only against later device-clock
reads never needs host/device clock reconciliation. It implements `app_crash_artifacts()` in two
layers, matching this item's own scoping decision to capture both, each independently wrapped so that
a failure in one layer never drops the other:

1. **`logcat`'s `crash` buffer**, always attempted, needing no elevated access. `logcat -b crash` is a
   device-global ring buffer that persists across launches and across scenarios sharing the same
   device — [`scripts/collect_android_diagnostics.sh`](../../scripts/collect_android_diagnostics.sh)
   dumps it whole (`-b main,system,crash,events,radio`) at the end of every failed CI job, so clearing
   it per launch would destroy evidence that end-of-job sweep still needs. Nothing here clears it: a
   later `adb logcat -b crash -d -t "<launch marker>"` dump uses the device-relative launch marker
   already recorded above as `logcat`'s own time filter, so it holds only content from the launch this
   scenario is running — never a stale crash the same package left behind on an earlier run sharing
   the device — while leaving everything before that marker intact for the end-of-job sweep to still
   find. The dump is parsed two ways: a `FATAL EXCEPTION` block for a managed-code (Java/Kotlin)
   crash, and, when none is found, the native crash buffer's own `Fatal signal <n>` header line for an
   NDK crash — the two formats `logcat`'s crash buffer actually carries. Whichever matches is
   extracted and written as `logcat-crash.txt`. This is the one artifact guaranteed available on any
   AVD or real device the adb backend can already reach.
2. **A tombstone pull**, best-effort and gated on root access, run last, after the `logcat` layer
   above. `adb root` is already a routine operation against the emulator images this backend targets,
   but it restarts `adbd` (`scripts/collect_android_diagnostics.sh:98-102` already covers this with its
   own `adb wait-for-device` right after `adb root`, noting why: "adbd restarting as root"). That
   restart kills the resident server's own `am instrument -w` session outright, not merely its `adb
   forward` mapping (`instrument_cmd`'s `-w` is what "keeps the instrumentation attached … holding the
   `UiAutomation` session warm",
   [`adb/_functions.py:604-616`](../../bajutsu/common/backend_cli/adb/_functions.py)), and drops
   BE-0283's network-collector `adb reverse` tunnel
   (`android_environment.py:298-306`) the same way. Re-forwarding a port onto a session that no longer
   exists would not recover it — restarting the resident server itself is the only fix, a heavier
   operation this layer does not attempt. Nothing after this point in `_run_on_lease` needs either
   channel, though: the scenario has already ended, and everything left before `lz.release()` — the
   network snapshot write, the progress line — reads from `lz.collector`'s own already-captured data,
   never back through the driver. `AndroidEnvironment.start()` rebuilds the resident server and the
   reverse tunnel from scratch on every lease regardless (`_begin_resident`, `bridge_collector`), a
   routine "no warm resident kept" teardown-and-rebuild this design already relies on, so the next
   lease on this device is unaffected by what this layer leaves broken. It then pulls the most recent
   `/data/tombstones/tombstone_NN` whose modification time is at or after the launch marker above,
   compared as device-relative timestamps, so no clock reconciliation is needed here either. A real
   device, a user build, or a refused `adb root` all resolve to skipping this layer silently. The event
   is still reported and `logcat-crash.txt` still lands; a device that refuses root loses only the
   native-frame detail a managed-code crash never needed in the first place, and every scenario after
   this one gets a resident server exactly as fresh as it would have anyway.

### Wiring the capture into a failed scenario's run directory

The reactive check runs in-band, inside the same step loop every other terminal failure already goes
through. `run_scenario`'s ordinary `RunResult` assembly therefore runs unchanged after it. The failing
step's own screenshot, the steps already completed, and any scenario-level `after: on: fail` rule all
land exactly as they would for an `ElementNotFound` failing the same step. The video recording already
running for the whole scenario stops and attaches the same way too. `AppCrashedError` never escapes
`run_scenario` as a raised exception, so `pipeline.py`'s existing crash-retry loop — which only
triggers on an escaping `BackendCrashError` — never sees it. The scenario fails once, the same way any
other terminal step failure already does, with no special-casing needed to keep it from retrying.

The evidence copy stays outside that in-band path, though, for the same reason BE-0421's own copy
does: `_step_runner`'s sink
([`bajutsu/common/orchestrator/loop/_loop_config.py`](../../bajutsu/common/orchestrator/loop/_loop_config.py))
is an `EvidenceSink`, whose whole surface is `capture` / `wait_diagnostic` / the interval start/finish
pair — no arbitrary named write — and it is scoped to the running scenario, not to the crash evidence
that needs the run-scoped `RunArtifactWriter` and the `sid` `pipeline.py` already holds. `Lease`
([`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)) gains
`app_crash_artifacts: Callable[[], list[tuple[str, bytes]]]`, defaulted through a module-level no-op
the same way `crash_artifacts` already is, and wired in `pool.py`'s `lease()` closure alongside it —
reading the environment's method directly, with no snapshot-and-thunk indirection, since (unlike a
backend crash) nothing tears this environment down or hands it to a new lease before this call.

`pipeline.py`'s `_run_on_lease` reads `result` for a crashed outcome right after `run_scenario`
returns, still holding the same lease it leased, before its own `finally` ever releases it — closing
the race an earlier draft of this item left open by reading artifacts after release. It scans
`(*result.before_outcomes, *result.steps, *result.after_outcomes)` for `app_crashed` rather than
reading `result.steps[-1]`: a nested crash settles its wrapping `if`/`forEach` outcomes *after* the
crashed one (`_step_runner.py:220`, `:238`), so the crashed outcome is not always last, and
`result.steps` alone omits the `before` and `after` phases entirely — `RunResult.steps` is the main
phase's own list (`_functions.py:851`), and it is `[]` outright when a `before` step fails
(`_functions.py:732-740` skips the main steps in that case). When the scan finds one, a new
`_write_app_crash_artifacts(lz, s, sid)` mirrors `_write_crash_artifacts` (BE-0421,
`pipeline.py:803`) almost exactly: it calls `lz.app_crash_artifacts()`, writes each `(name, content)`
pair through `writer.write_text(f"{sid}/app-crash/{name}", content.decode(errors="replace"))` — the
redacting text path, not `write_bytes`, for the same reason BE-0421's own copy uses it: a crash report
is text a crashing app can echo a secret into — and appends a trailer naming the directory to
`result.failure`, the same shape `_write_crash_artifacts` returns for `pipeline.py` to append. A write
problem is logged, never raised, matching that same posture: a diagnostic capture must never turn an
already-decided failure into a different one. This is a plain post-return check, not a new `except`
branch — the scenario's own retry behavior above is already settled by the time it runs.

### Extending `crawl`'s own crash recording

`crawl()` ([`bajutsu/crawl/core/_functions.py`](../../bajutsu/crawl/core/_functions.py)) itself holds
no environment. It takes only injected callables — `driver`, `reset`, `is_alive`, `recover`,
`on_event`, and the rest — which is what keeps the crawl core off `platform_lifecycle` and lets the
same loop drive every backend. Reaching into it for an environment reference, as an earlier draft of
this item proposed, would give every backend and every fake in the fast test suite one to grow — and
`is_alive` gives no signal to key that reference off in the first place: both `ios.py` and
`android_environment.py` answer `crawl_aliveness()` with `None`, "the engine reads the accessibility
tree for device crash detection" (their own comment), so `crawl()` runs with no `is_alive` callback at
all on either backend this item covers. There is a single `CrawlEnvironment` in scope in
[`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py) (`plan.environment`), but it is built with an
empty `udid` (`environment_for(actuator, "")`) purely to wire the health seams above — not the device
the crash actually happened on. A crawl runs one environment per lane instead, built in `_build_lane`
alongside that lane's own `driver`/`reset`, one per `--udid` (BE-0064): reaching for `plan.environment`
from a multi-lane crawl would capture the wrong device's diagnostics, or none.

The capture is therefore threaded the same way `driver` and `reset` already are: `_build_lane` gains
a third return value, that lane's own `env.app_crash_artifacts`, carried alongside its driver and
reset through `WorkerFactory` (`bajutsu/crawl/core/_functions.py`) for every extra lane and through
`crawl()`'s own primary-lane parameters for the first one. `record_crash`'s call site
(`bajutsu/crawl/core/_functions.py:667`) already computes `crashed = not alive(d, landed)` off the
coordinator's lock — "pure deterministic reads, off-lock", its own comment says — right before calling
`coord.record_crash(path)`. `alive` falls back to the UI-tree heuristic `is_app_alive` on both
backends this item covers, and this item's own Introduction already names its risk: a system alert or
a deliberate `background` step reads as a false positive. `run`'s own bounded `.ips`/tombstone poll
earns its wait because `app.state == notRunning` or a `pidof`-plus-exit-info check confirmed the event
first; `crawl` has no such confirmation, so a false positive here would poll to its full timeout on
every occurrence — the common case, not the rare one, since `crawl` does not stop after recording a
crash (`current_fp = None; continue` right after `record_crash`, `_functions.py:668-669`) and can walk
into the same false positive repeatedly in one run. This item's capture call therefore joins the
existing check in the same off-lock window, but only calls that worker's own lane-scoped
`env.app_crash_artifacts()` when the driver can positively confirm the event first: `isinstance(d,
base.AppCrashSignal)` and a non-`None` `d.app_crash_signal()`, the same capability both backends
already implement for `run`. `crawl`'s own detection is unchanged either way — a `Crash` is still
recorded on the UI-tree heuristic alone — only the artifact sweep is gated, so an unconfirmed crash
records a `Crash` with no `artifacts` rather than paying a full-timeout poll on what might be a
covering alert. Capturing here matters for concurrency, not only correctness: `record_crash` holds the
coordinator's `self._cond` for its whole body, and that same lock also serializes `on_event`
(`_coordinator.py`'s `_emit`) and every other worker's own `record_crash` / `record_edge` calls — a
multi-second `.ips` poll or tombstone pull run *inside* that lock would stall every other crawl lane
for its duration. Run first and handed in already resolved, the capture costs the lock nothing beyond
an ordinary list append.

`Crash` ([`bajutsu/crawl/core/crash.py`](../../bajutsu/crawl/core/crash.py)) gains an
`artifacts: tuple[tuple[str, bytes], ...] = ()` field, and `record_crash` takes and stores it alongside
`path`. [`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py)'s `write_repros` — not `cli.py`'s
`_finish`, which only calls it and echoes a count — already walks `screen_map.crashes` in order to
write each one's `crashes/crash-NNN.yaml` repro, `NNN` that crash's own zero-padded, one-based index
in the list (`enumerate(screen_map.crashes, start=1)`, `repro.py:154`). That loop owns the numbering,
so it gains the artifact-writing step rather than `_finish` re-deriving the same index in a second
file, a duplicated-logic shape that would drift silently if the numbering ever changed in one place
and not the other. `write_repros` also `continue`s past a crash whose path cannot be faithfully
replayed, writing no `.yaml` for it (a `tap_point` action has no selector to address) — the artifact
write sits *before* that `continue`, so a non-replayable crash, the kind whose platform report is
worth the most since there is no repro to run instead, does not lose its artifacts along with its
repro. It writes any non-empty `artifacts` under `crashes/crash-NNN/app-crash/` — a sibling of the
repro file, not a same-named top-level directory, so the two are found together and sort together. A
crawl's own detection stays the UI-tree heuristic it already uses: `crawl` has no scenario step to
hang a reactive check off, unlike `run`. The capture is shared between
the two entry points; the detection is not.

`Crash` is also the type [`serialize.py`](../../bajutsu/crawl/serialize.py) round-trips through JSON —
`screenmap_dict` dumps `screen_map.crashes` (`:131-134`) and `screenmap_from_dict` rebuilds it
(`:92-98`) — and raw `bytes` has no JSON encoding, so `artifacts` is deliberately left out of both
directions rather than base64-widening every other field's dump. `on_event`'s own `_write_screenmap`
call (`cli.py:186`) fires that dump live, after every recorded crash, well before `write_repros` ever
runs — `write_repros` walks the finished `screen_map.crashes` exactly once, at the very end of a
normally-completed crawl (`cli.py:405`) — so `artifacts` is already an in-memory-only field on its own
terms: nothing durably persists it before that one closing call, dump or no dump. Omitting it from the
dump costs a `--resume`/`--continue-crawl` reload nothing a normally-completed prior crawl already
wrote to disk (`write_repros` ran, so the bytes are on disk under that prior run's own
`crashes/crash-NNN/app-crash/`) and nothing an interrupted one could have kept either way (the process
died before `write_repros` reached it, so the bytes never reached disk in the first place, the same
loss a `--resume` already accepts for that prior crawl's own unwritten report). `screenmap_from_dict`
rebuilds every carried-forward `Crash` with `artifacts=()`, the dataclass default, exactly as it
already does for `actions` on a map saved before crashes carried that field.

### Proving the capture on a real crash, not only a stubbed one

A unit test can stub `~/Library/Logs/DiagnosticReports`, or a fake `logcat`/tombstone pull. Neither
proves the underlying platform mechanism this item depends on — `ReportCrash`'s own `.ips` write, or
`logcat`'s `crash` buffer — still behaves the way the design above assumes, on a real Simulator or
emulator. The showcase apps ([`demos/showcase/`](../../demos/showcase)) gain a "force a crash"
affordance gated behind a launch-env flag, not a debug build: there is no `#if DEBUG` anywhere in the
showcase iOS sources, and the closest existing precedent,
[`ConformanceView.swift`](../../demos/showcase/ios/swiftui/Sources/ConformanceView.swift), is itself
reached only when the `SHOWCASE_CONFORMANCE` launch env is set
([`AppModel.swift:92`](../../demos/showcase/ios/swiftui/Sources/AppModel.swift),
[`RootView.swift:7`](../../demos/showcase/ios/swiftui/Sources/RootView.swift)) — BE-0114's
driver-conformance screen, not on-device diagnostics. Gating behind a build configuration instead
would put the affordance's very existence at the mercy of whichever configuration the iOS lane's own
`build (app + runner)` job compiles: a Release build would compile it out, and the new
expected-to-fail scenario would then fail on a missing selector rather than on a crash — the exact
misdiagnosis this item exists to remove. A launch-env flag needs no build-configuration assumption and
rides the scenario schema's own `preconditions.launchEnv`, the same seam `SHOWCASE_CONFORMANCE`
already rides. The affordance calls `fatalError()` on iOS and throws an uncaught exception on
Android's main thread. One new scenario per platform taps it.

Both new scenarios are *expected* to fail. The CI wrapper around them asserts the failure carries the
new app-crash classification, and that `app-crash/` holds the expected file — the same way
`fault-injection (xcuitest)` already asserts a diagnosed failure shape rather than a green run
([`docs/ci.md`](../../docs/ci.md#the-ios-lane)). They land as a non-gating per-PR signal in
`ios-e2e.yml` / `android-e2e.yml`, next to `fault-injection (xcuitest)` and `network (adb)`. Newly
wired on-device coverage earns its stability there first, before any promotion into the required `E2E`
check — the same path every other new signal in those lanes has taken.

### Cost on the web backend and the fake backend

These are two separate seams, and neither backend needs meaningful work on either half. `PlaywrightDriver`
does not implement `AppCrashSignal`, so the reactive check's `isinstance` probe answers `False` and
skips it, the same way it would for any other driver that never declares the protocol. `WebEnvironment`
([`bajutsu/common/platform_lifecycle/environments/web.py`](../../bajutsu/common/platform_lifecycle/environments/web.py))
declares its own `app_crash_artifacts()` returning `[]` — a one-line addition alongside the identical
declaration it already carries for `take_crash_snapshot()`, since `RunEnvironment` is a structural
protocol no concrete class subclasses and so has no inheritable default either method could fall
through to. `FakeEnvironment`
([`bajutsu/common/platform_lifecycle/environments/fake.py`](../../bajutsu/common/platform_lifecycle/environments/fake.py))
inherits the same no-op from `_DeviceEnvironment`, and the fake test driver behaves the same way on
the `AppCrashSignal` seam. Nothing in this item changes what a web or fake-backend run captures.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Retry through the existing `BackendCrashError` recovery loop | Rejected during scoping: this event is a likely defect in the app itself, not a transient infrastructure blip. Respawning and retrying would spend the crash-recovery budget on a scenario likely to fail again, and risks absorbing a real regression as flakiness (BE-0049) instead of failing loudly. |
| Poll `app.state` / process liveness proactively, before every step | Rejected during scoping: it adds one driver round-trip to every step of every scenario, green runs included, to catch a failure mode that is rare by construction. The reactive design still catches the event at the exact step it happened, since that step's own action or query is already failing. |
| Raise `AppCrashedError` out of `run_scenario`, with a dedicated `except` branch in `pipeline.py` mirroring `BackendCrashError`'s | Rejected on review. An app crash leaves the driver, the backend process, and the running video recording all intact, unlike a backend crash — only the app is gone. A side-channel exception would build its own terminal `RunResult` from scratch, discarding the steps, artifacts, and `after: on: fail` dispatch `run_scenario` already produces for every other terminal failure. |
| Add `app_crash_signal()` as a required member of the `Driver` protocol | Rejected on review. `Driver` is `@runtime_checkable`, so every implementer would need a stub: `XcuitestDriver`, `AdbDriver`, `PlaywrightDriver`, `XcuitestLiveDriver`, the fake backend, and narrower wrappers like `WebContextDriver`, including backends this item never intends to check. A narrow, opt-in capability protocol, the shape this codebase already uses for `InterruptionPolicyTarget` and `SettledReadProvider`, reaches only the two backends that need it. |
| Android: `logcat`'s crash buffer only, no tombstone pull | Rejected: it loses the native (NDK) crash's full backtrace, keeping only the abridged summary `logcat` itself prints. Kept as the always-available baseline; the tombstone pull layers richer detail on top where the device allows it, rather than replacing it. |
| Android: a rooted tombstone pull only, no `logcat` fallback | Rejected: a real device, a user build, or an emulator image that refuses `adb root` would then capture nothing at all. `logcat`'s crash buffer needs no elevated access and already carries a complete stack trace for the common managed-code case. |
| A new scenario assertion (for example, `assert: appCrashed: false`) | Rejected: the event already ends the scenario through the step's own action/query failure. There is no later point in the scenario where an assertion could still run to check for it. The showcase's own test scenario instead asserts the failure's *shape* from outside the run, the way `fault-injection (xcuitest)` already does. |
| Gate the capture behind an opt-in `capturePolicy` rule, matching `video` / `deviceLog` | Rejected for the reason BE-0421 gave for its own artifact: the capture runs once, only on a scenario already ending in failure. Its cost is one bounded sweep or log read, not a standing per-step overhead worth gating behind an explicit ask. |
| Freeze the match criteria and defer the sweep with a `take_crash_snapshot()`-style thunk, the way BE-0421 defers a backend crash's own capture | Rejected: that indirection exists only to survive a concurrent worker reusing the same warm, pooled environment before the pool releases the lease. This event's own capture runs synchronously while `pipeline.py` still holds the very lease the crash happened on, well before any release or reuse — there is no such race to defer past. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `base.AppCrashedError` (new file); the `base.AppCrashSignal` capability protocol
      (`app_crash_signal() -> str | None`), separate from the `Driver` protocol; the new
      `StepOutcome.app_crashed: bool = False` field.
- [ ] Unit 2 — iOS: a new `openapi.yaml` route and generated `APIHandler` method reading
      `XCUIApplication.state`, served through `RunnerServer` (not `Router.swift`);
      `XcuitestDriver.app_crash_signal()` implementing `AppCrashSignal`, classifying `notRunning` as
      the signal and letting a channel error propagate as `XcuitestRunnerCrashError`; a new
      `is_real_device` constructor argument, threaded from `make_driver` the same way `device_os`
      already is, that makes `app_crash_signal()` answer `None` outright on `deviceType: device` —
      this item scopes to the Simulator only.
- [ ] Unit 3 — iOS: `XcuitestEnvironment.app_launched_at`, recorded at the cold launch; a new
      `XcuitestEnvironment.relauncher()` override wrapping `device_relauncher`'s `RelaunchFn` to
      record it again after a `relaunch` step's own launch, the one call site `_DeviceEnvironment`'s
      inherited `relauncher()` has no environment in scope to update; a matching
      `XcuitestEnvironment.crawl_reset()` override recording it a third time after `crawl`'s own
      per-frontier-revisit relaunch, the one other call site `_DeviceEnvironment`'s inherited
      `crawl_reset()` also has no environment in scope to update; reading `CFBundleExecutable`
      from `Path(ios.app_path) / "Info.plist"` for the sweep's own match pattern (`ios.bundle_id` is
      not this name); `app_crash_artifacts()`'s name-and-`udid`-matched `.ips` sweep (no PID accessor
      exists on `XCUIApplication`), with a bounded wait for `ReportCrash`'s asynchronous write,
      wrapped so any failure resolves to `[]`.
- [ ] Unit 4 — Android: a `package` keyword threaded through `backends.make_driver` into
      `AdbDriver.__init__`, the same way `device_os` already is; a `launched_at` injected callable
      reading `AndroidEnvironment.app_launched_at`; `AdbDriver.app_crash_signal()` via `adb shell
      pidof <package>` corroborated by a time-bound `adb shell dumpsys activity exit-info <package>`
      check (its newest entry only, at or after `launched_at()`).
- [ ] Unit 5 — Android: `AndroidEnvironment.app_launched_at` (device clock) at each launch site;
      `app_crash_artifacts()`'s always-attempted `logcat` extraction (managed *and* native crash
      formats) using a `-t "<launch marker>"` time filter rather than clearing the crash buffer, so
      `scripts/collect_android_diagnostics.sh`'s own end-of-job sweep still sees everything earlier;
      the best-effort, root-gated tombstone pull, run last, accepting that `adb root` kills the
      resident server's `am instrument -w` session and BE-0283's `adb reverse` tunnel outright — not
      re-establishing either, since nothing later in this lease needs them and the pool rebuilds both
      fresh on the next lease regardless — each layer independently wrapped so any failure resolves
      to `[]`.
- [ ] Unit 6 — `RunEnvironment.app_crash_artifacts()` protocol shape (returning
      `list[tuple[str, bytes]]`, read live with no snapshot-and-thunk indirection, since nothing
      tears the environment down before this scenario's own lease releases); a one-line `return []`
      on each of `WebEnvironment` and `_DeviceEnvironment` (inherited by `FakeEnvironment`), the same
      no-op shape `take_crash_snapshot()` already declares on all three, minus Android — Android has
      no counterpart no-op here, since `AndroidEnvironment` overrides `app_crash_artifacts()` with the
      real capture (Unit 5), not a no-op, the same way `XcuitestEnvironment` overrides it with its own
      real capture (Unit 3); `Lease.app_crash_artifacts` wired through `pool.py`'s `lease()` closure
      alongside `crash_artifacts`.
- [ ] Unit 7 — `run_scenario` / `_step_runner.py`: the new `_finish_outcome` helper, called at all
      five `self.state.outcomes.append(outcome)` call sites — `_handle_if` / `_handle_for_each` /
      `_handle_web` once each, `_handle_action` twice (its own end and its
      `UncoveredSystemAlertLocale` early return) — in place of the bare append, covering every step
      kind's true final outcome; a scenario-scoped object (`run_scenario` creates it once and shares
      it with every `run_phase` call the same way it already shares `live_bindings`, carried on
      `StepLoopState` next to `bindings`, not on the frozen `_LoopConfig`, so it survives `before`,
      the main steps, and every dispatched `after` rule) carrying two latches: a deliberate-termination flag, set the moment
      `outcome.action == "relaunch"` and `outcome.ok is False` (before any probe), that suppresses
      every later probe in the scenario — the failing `relaunch`'s own wrapping `if`/`forEach`
      outcomes and any `after: on: fail` cleanup step included, not only the `relaunch` step's own
      outcome; and a confirmed-crash latch, set the first time `_finish_outcome` raises and catches
      `AppCrashedError`, so a later outcome in the same propagation folds the known signal into its
      own `outcome.reason` without probing again — bounding `app_crash_signal()` calls for the
      relaunch and confirmed-crash cases specifically, not for an ordinary failure, which still pays
      one probe per settling outcome; raising and catching `AppCrashedError` in that one place to fold
      its message into `outcome.reason` and set the new `StepOutcome.app_crashed` field and the
      confirmed-crash latch; a fast-suite assertion that no `self.state.outcomes.append` survives
      outside `_finish_outcome`.
- [ ] Unit 8 — `pipeline.py`: `_run_on_lease` scanning
      `(*result.before_outcomes, *result.steps, *result.after_outcomes)` for an `app_crashed`
      outcome right after `run_scenario` returns, still holding the same lease, before its own
      `finally` releases it; the new `_write_app_crash_artifacts(lz, s, sid)` mirroring
      `_write_crash_artifacts` (BE-0421,
      `pipeline.py:803`), writing each artifact through the redacting `writer.write_text` path under
      `{sid}/app-crash/` and appending a directory-naming trailer to `result.failure`.
- [ ] Unit 9 — `TracingDriver`: add `base.AppCrashSignal` to `_PROTOCOLS` so `--trace-driver` installs
      it as a real attribute only on a wrapped driver that implements it.
- [ ] Unit 10 — `crawl`'s own integration: `_build_lane`'s per-lane `app_crash_artifacts`, threaded
      through `WorkerFactory` and `crawl()`'s primary-lane parameters the same way `driver`/`reset`
      already are; the capture call joining `record_crash`'s existing off-lock crash check, gated on
      the driver positively confirming the event (`isinstance`/`app_crash_signal()`) so a UI-tree
      false positive does not pay a full-timeout sweep; `Crash`'s new `artifacts` field, deliberately
      left out of `serialize.py`'s `screenmap_dict`/`screenmap_from_dict` round trip (raw `bytes` has
      no JSON encoding; a carried-forward `Crash` reload always gets `artifacts=()`);
      `repro.py`'s `write_repros` writing non-empty artifacts under `crashes/crash-NNN/app-crash/`
      before its own `continue` on a non-replayable crash, alongside that crash's own
      `crashes/crash-NNN.yaml` repro.
- [ ] Unit 11 — Showcase fixtures: a "force a crash" affordance gated behind a launch-env flag (not a
      build configuration) on iOS (SwiftUI) and Android (Compose), one scenario per platform
      exercising it via `preconditions.launchEnv` — each scenario takes one step after the crash
      trigger (against the now-dead app), not ending on the trigger itself, since a scenario whose last
      step is the crash trigger never fails by this item's own reactive-check design (see *Detecting
      the event*) — wired as a non-gating per-PR signal in `ios-e2e.yml` / `android-e2e.yml`.
- [ ] Unit 12 — Docs: `docs/evidence.md` (+ `docs/ja/`) gains this artifact kind; `docs/ci.md`
      (+ `docs/ja/`) notes the showcase signal lane; `docs/architecture.md` (+ `docs/ja/`)
      cross-references the no-retry app-crash path against the existing backend-crash retry section.
- [ ] Unit 13 — Tests: `app_crash_signal()` answering `None` on an ordinary `ElementNotFound` (no
      false positive on a missing selector), on a `wait`/`assert` failure, and on `deviceType: device`
      regardless of `app.state`, for both backends; `XcuitestEnvironment.app_launched_at` advancing
      past a `relaunch` step's own launch and past a `crawl`-driven `crawl_reset()`'s own launch, and
      the `.ips` sweep after a second crawl crash finding only the report from that crash's own reset,
      not the first crash's; a failing `relaunch` step never probing
      `app_crash_signal()`, and neither does its wrapping `if`/`forEach` outcome nor a dispatched
      `after: on: fail` step that also fails against the terminated app; an interrupt recovery step
      (BE-0314's `_run_recovery`, distinct from the `after` phase) that also fails against a
      terminated app probing once per recovery step, neither latch bounding it either; an ordinary
      (non-`relaunch`, non-crash) failure three levels deep still probing once per settling outcome,
      pinning that the latch does not bound this case; the iOS `.ips` sweep and the Android `logcat`/tombstone capture
      against stubbed directories and stubbed `adb` output, including the Android exit-info
      corroboration; a `_step_runner.py` test asserting the in-band failure, the new `app_crashed`
      field, and the confirmed-crash latch holding across a nested `if`/`forEach` failure; a
      `pipeline.py` test asserting the `app-crash/` directory with redacted text content, the scan
      finding a crash in `before_outcomes`/`after_outcomes` as well as `steps`, and no crash-retry
      loop trigger; a stubbed-`await_ready`-timeout test asserting a `relaunch` step whose new launch
      never becomes ready still reports `ok=True` (so the exemption's own premise holds) and that the
      crash is instead caught, `app_crashed=True`, on the very next step against the dead app; a
      web/fake-backend test asserting `isinstance` answers `False` and nothing changes (the standalone
      Playwright/fake target, not a `web` block on the XCUITest or adb backend); a `_handle_web` test on
      the XCUITest/adb backends asserting the opposite for that block: a failing inner web step's own
      outcome (`active_driver = web_driver`) never probes, but the wrapping `web` step's own outcome
      (`active_driver` = the native driver) does; a scenario whose *last* step crashes the app but still
      reports `ok=True` staying green with no `app_crashed` outcome and no `app-crash/` artifact,
      pinning the documented blind spot rather than leaving it to be rediscovered as a bug.

## References

- [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md)
  (Implemented, [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)) — the runner's own
  crash-report capture this item complements and reuses directly: `_reports_since()` and
  `_reported_pid()` in
  [`xcuitest/_functions.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py),
  and `pipeline.py`'s `_write_crash_artifacts()` (`_CRASH_DIAGNOSTICS_DIR`), the sibling this item's
  own `_write_app_crash_artifacts()` mirrors
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) — the
  crawl `Crash` record and `is_app_alive` heuristic this item's `crawl` integration builds on
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) —
  the existing backend-crash retry semantics this item's no-retry design deliberately diverges from
- [BE-0066](../BE-0066-web-crawl/BE-0066-web-crawl.md) — the web (Playwright) backend, whose own
  signals for this event (a `page.on("crash")` renderer event, an uncaught `pageerror`) are left to a
  follow-up item
- [`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py) —
  `BackendCrashError`, the sibling fault this item's `AppCrashedError` shares no base class with
- [`bajutsu/common/drivers/base/interruption_policy_target.py`](../../bajutsu/common/drivers/base/interruption_policy_target.py) —
  the narrow opt-in capability-protocol shape this item's `AppCrashSignal` follows
- [`bajutsu/common/drivers/tracing.py`](../../bajutsu/common/drivers/tracing.py) — `TracingDriver`,
  whose `_PROTOCOLS` tuple this item's `AppCrashSignal` joins
- [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py) —
  the per-step loop, whose four step-kind handlers share the new `_finish_outcome` helper this
  item's one reactive check lives in
- [`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py) —
  `run_scenario`'s `run_phase` closure sharing `live_bindings` across `before` / the main steps /
  every `after` rule, the precedent this item's cross-phase crash latch follows
- [`bajutsu/common/orchestrator/loop/step_loop_state.py`](../../bajutsu/common/orchestrator/loop/step_loop_state.py) —
  `StepLoopState`, where the latch lands next to `bindings` — the field that already carries a
  scenario-scoped mutable object into a freshly built `StepLoopState` each phase
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py) — `_run_on_lease`,
  scanning `result`'s `before_outcomes` / `steps` / `after_outcomes` for `app_crashed` while still
  holding the lease, and `_write_crash_artifacts` (BE-0421), the sibling the new
  `_write_app_crash_artifacts` mirrors
- [`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py) — `Lease.crash_artifacts`,
  the precedent `Lease.app_crash_artifacts` follows
- [`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py) —
  the protocol `app_crash_artifacts()` joins
- [`bajutsu/common/platform_lifecycle/relaunchers.py`](../../bajutsu/common/platform_lifecycle/relaunchers.py) —
  `device_relauncher`, the `relaunch` step's actual iOS launch path, distinct from
  `_resume_warm`'s cross-lease one
- [`bajutsu/common/platform_lifecycle/environments/ios.py`](../../bajutsu/common/platform_lifecycle/environments/ios.py) —
  `_DeviceEnvironment.crawl_reset()`, the third iOS launch site `XcuitestEnvironment` overrides to
  keep `app_launched_at` current, alongside `relauncher()`
- [`bajutsu/crawl/core/_functions.py`](../../bajutsu/crawl/core/_functions.py) — `record_crash`'s
  off-lock crash check, the join point for this item's crawl-side capture call
- [`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py) — `_build_lane`, the per-lane environment this
  item's capture reads
- [`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) — `write_repros`, which already walks
  `screen_map.crashes` and owns the `crash-NNN` numbering the captured artifacts are written under
- [`bajutsu/crawl/serialize.py`](../../bajutsu/crawl/serialize.py) — `screenmap_dict` /
  `screenmap_from_dict`, the JSON round trip `Crash`'s new `artifacts` field is deliberately left out
  of
- [`bajutsu/common/platform_lifecycle/readiness.py`](../../bajutsu/common/platform_lifecycle/readiness.py) —
  `await_ready`, whose timeout returns rather than raises — why a `relaunch` step's own readiness wait
  never fails that step
- [`bajutsu/common/backend_cli/simctl/env.py`](../../bajutsu/common/backend_cli/simctl/env.py) —
  `Env.terminate`/`Env.launch`, whose own `CalledProcessError` handling is why the relaunch exemption's
  `outcome.ok is False` case is the alert-guard path, not an app-health one
- [`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py) — `write_text` (redacting)
  versus `write_bytes` (unmasked, for content the sink cannot inspect), the distinction this item's
  artifacts follow by decoding to text first
- [`scripts/collect_android_diagnostics.sh`](../../scripts/collect_android_diagnostics.sh) — the
  end-of-job Android diagnostics sweep this item's `logcat` filtering follows the precedent of, and
  whose own `adb wait-for-device` names why `adb root` breaks a live connection
- [`bajutsu/common/backend_cli/adb_resident/resident_server.py`](../../bajutsu/common/backend_cli/adb_resident/resident_server.py) —
  `AdbDriver`'s own read channel, killed outright by the tombstone pull's `adb root` and left that way
  rather than restarted
- [`bajutsu/common/backend_cli/adb/_functions.py`](../../bajutsu/common/backend_cli/adb/_functions.py) —
  `instrument_cmd`, whose `-w` flag is what the tombstone pull's `adb root` actually kills
- [`bajutsu/common/backends.py`](../../bajutsu/common/backends.py) — `make_driver`, whose existing
  `device_os` keyword is the precedent this item's `package` keyword follows
- [`docs/ci.md`](../../docs/ci.md#the-ios-lane) — `fault-injection (xcuitest)`, whose non-gating,
  failure-shape-asserting placement this item's showcase scenarios follow
