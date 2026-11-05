**English** · [日本語](BE-0424-app-crash-diagnostics-ja.md)

# BE-0424 — Detect a crash in the app under test and capture its stack trace as scenario evidence

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0424](BE-0424-app-crash-diagnostics.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0424") |
| Implementing PR | [#2012](https://github.com/bajutsu-e2e/bajutsu/pull/2012) |
| Topic | Platform support |
| Related | [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md), [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0066](../BE-0066-web-crawl/BE-0066-web-crawl.md) |
<!-- /BE-METADATA -->

## Introduction

Suppose the app under test itself terminates abnormally mid-scenario. `bajutsu run` then reports the same kind of failure it would report for a missing element. The report is an `ElementNotFound` (or a
similar driver failure) that names a selector that could not be found. Nothing in that message says
the app went down.

The scenario's own run directory (`runs/<run_id>/<sid>/`) does not hold the operating system's own
report for the crash either. On iOS, that report is the `.ips` file that macOS's `ReportCrash`
writes for a faulting process. On Android, it is one of two things. The first is the Java stack trace that `logcat` prints for an uncaught exception. The second is the native tombstone that the device writes under `/data/tombstones`.

A contributor debugging a red scenario has to notice the pattern by elimination first. The pattern is a selector that should exist and normally does. Only then can the contributor go find the platform's own report
by hand, outside anything `bajutsu` itself produced.

This item adds both pieces: the detection and the capture. A scenario whose app goes down during a
step gets a distinct failure message that names the event. Its run directory gains an `app-crash/`
subdirectory next to its other evidence. That subdirectory holds the platform's own report, or the
closest counterpart the platform offers.

This item covers `bajutsu run` first, on the iOS (XCUITest) and Android (adb) backends. Each backend
has its own scope.

- **iOS:** scoped to the Simulator. On a real device, the gaps in evidence named under *iOS:
  `app.state`* leave `app_crash_signal()` answering `None`. That is the same "cannot confirm" answer
  that a backend with no signal at all gives.
- **Android:** scoped to API 30 and above. `ApplicationExitInfo`, the corroborating signal *Android:
  `logcat`'s crash buffer first…* depends on, does not exist below that level. `app_crash_signal()`
  answers `None` there too, rather than polling a signal that can never confirm anything.

The item extends the same underlying capture to `bajutsu crawl`'s existing detection of the same
event. The web (Playwright) backend and its own signals for the event are left to a follow-up item,
noted under *Alternatives considered*.

## Motivation

`bajutsu` already distinguishes a kind of failure it did not cause from one it did.
`base.BackendCrashError` names the backend's own driver process going down. That process can be the resident XCUITest runner's host, an adb resident server, or a browser process
([`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py)).
The run pipeline recovers from it. It discards the dead lease and retries the whole scenario on a
fresh device.
[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md)
copies the dying runner's own log, and on iOS its `.ips` report, into the failed scenario's evidence.
Both of these name a fault in the *backend*: the test infrastructure, not the app a team is testing.

Nothing today plays the equivalent role for the *app under test*. `bajutsu crawl`, the AI-driven
exploration path, already detects this event. `is_app_alive`
([`bajutsu/crawl/core/_functions.py:281`](../../bajutsu/crawl/core/_functions.py)) checks whether
the element tree still shows the app's own UI. A crawl that finds a collapse records a `Crash`
([`bajutsu/crawl/core/crash.py`](../../bajutsu/crawl/core/crash.py)). That record holds the action
path that led to the collapse, which
[`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) turns into a deterministic repro scenario.

That detection is a UI heuristic, though, with no platform-level evidence behind it. A `Crash`
record carries the tap sequence that produced the collapse. It never carries the report that the
operating system itself wrote for the dying process. `bajutsu run`, the deterministic path every CI
gate depends on, has no equivalent check at all. A mid-scenario app crash surfaces only as whatever
generic failure the next action or query happens to raise.

That gap costs a contributor real time on a genuine app defect, the case this item is written for.
`BackendCrashError`'s own recovery already handles a driver or environment fault, and this item does
not touch that path. In today's report, a scenario that fails because the app went down looks identical to two other failures. One is a renamed selector `id`. The other is a screen that never loaded. Both show
the same `ElementNotFound` and the same "look at the screenshot and guess" starting point.

This item's observable outcome: once landed, a scenario whose app crashes fails with a message that
names the event. Its `runs/<run_id>/<sid>/app-crash/` directory holds the platform's own evidence.

- **iOS:** the `.ips` report that `ReportCrash` wrote, under its own name.
- **Android:** `logcat-crash.txt`, and `tombstone.txt` too where the device allows it.

A contributor opens the right file first, instead of ruling out two wrong explanations before
finding it.

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

Earlier drafts of this item raised `AppCrashedError` out of `run_scenario`. They added a new
`pipeline.py` `except` branch that mirrored `BackendCrashError`'s crash-retry handling. Review
rejected that shape (see *Alternatives considered*).

A scenario whose backend crashed has no state left to salvage. A scenario whose *app* crashed still
has a driver, a backend process, and a video recording running. Only the app is gone. A side channel that bypasses `run_scenario`'s own `RunResult` assembly would throw all three away. It would do so for no reason tied to why the app crashed.

The design below keeps the classification in-band instead. `AppCrashedError` never leaves the one
call site that raises it. That call site turns the error into an ordinary terminal step failure. The existing pipeline already knows how to finish that shape correctly. It also sets a new
`app_crashed: bool = False` field on `StepOutcome`
([`bajutsu/common/orchestrator/types/step_outcome.py`](../../bajutsu/common/orchestrator/types/step_outcome.py))
to `True`.

`pipeline.py` reads that field afterward to decide whether to copy the app's own evidence in (see
*Wiring the capture*). It finds the field through the scan over `before_outcomes` / `steps` /
`after_outcomes` described there. It does not read `RunResult.steps[-1]`.

### Detecting the event: a reactive signal, checked once a step has already failed

Checking whether the app is still running on every step would add a query to every scenario, green runs included. The query would catch a failure mode that is rare by construction. Instead, this item defines a
narrow, opt-in capability protocol. The protocol follows the shape that
[`InterruptionPolicyTarget`](../../bajutsu/common/drivers/base/interruption_policy_target.py) and
[`SettledReadProvider`](../../bajutsu/common/drivers/base/settled_read_provider.py) already
establish for a driver feature that only some backends have:

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

The signal is not a new member of the monolithic `Driver` protocol
([`bajutsu/common/drivers/base/driver.py`](../../bajutsu/common/drivers/base/driver.py)). `Driver`
is `@runtime_checkable`, so every one of its members is required for `isinstance(x, base.Driver)`
to answer `True` at all. This repository has four full implementers of it today, plus the fake test
backend:

- `XcuitestDriver`
- `AdbDriver`
- `PlaywrightDriver`
- [`XcuitestLiveDriver`](../../bajutsu/common/drivers/xcuitest_live/xcuitest_live_driver.py), a
  fourth backend that BE-0238 added. It drives a real iOS device over W3C WebDriver.

The repository also has narrower wrappers that intentionally implement only part of `Driver`'s
surface. One example is
[`WebContextDriver`](../../bajutsu/common/drivers/webview/web_context_driver.py), the driver that
the run loop swaps in for a `web` block.

Adding a required member to `Driver` would force every one of those to grow a stub. That includes `WebContextDriver`, for a check this item never intends to run inside a `web` block. `AppCrashSignal` avoids
all of that. Only `XcuitestDriver` and `AdbDriver` implement it. The call site below asks for it
with `isinstance`, the same way BE-0406 already asks for `InterruptionPolicyTarget`.

`TracingDriver`
([`bajutsu/common/drivers/tracing.py`](../../bajutsu/common/drivers/tracing.py)) is the
`--trace-driver` proxy. It installs each protocol in its own `_PROTOCOLS` tuple as real instance attributes. Each install is gated on `isinstance(wrapped, protocol)`, so that `isinstance` reads correctly on the proxy itself. `AppCrashSignal` joins that tuple, the same way `InterruptionPolicyTarget` already
does.

- Wrapping `XcuitestDriver` or `AdbDriver` installs the protocol.
- Wrapping anything else does not: `PlaywrightDriver`, the fake backend, `XcuitestLiveDriver`, or
  `WebContextDriver`.
- Every other capability the proxy tracks is unaffected either way.

`bajutsu/common/orchestrator/loop/_step_runner.py` dispatches every step to one of four handlers by kind. The handlers are `_handle_if`, `_handle_for_each`, `_handle_web`, and `_handle_action`. The last one covers
`wait`, `assert`, `handleSystemAlert`, and every actuating step. Each handler settles its own
`outcome.ok` differently.

`_handle_action`'s own `outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results` line is not the step's last word. That line runs once the tip-dismiss and alert-guard retries
are done. An undeclared interruption or a failed `extract` can still flip `outcome.ok` to `False`
afterward, inside the same handler.

An earlier draft of this item keyed the check to that one line alone. That check sees
`_handle_action`'s own terminal failures late. It never sees two other failures at all, because
neither reaches that line:

- `_handle_if`'s or `_handle_for_each`'s condition-query failure
- `_handle_web`'s `within`-selector failure

What every handler *does* share is `self.state.outcomes.append(outcome)`, but not once each.
`_handle_if`, `_handle_for_each`, and `_handle_web` call it exactly once, as the last thing they do
with `outcome` before returning. `_handle_action` calls it twice. The first call is at its own end.
The second is on an early return, its `UncoveredSystemAlertLocale` branch (`_step_runner.py:457`). That branch sets `outcome.ok = False` and returns well before the end is ever reached.

An earlier draft of this item keyed the check to one call site per handler. That draft would miss
the second `_handle_action` append. The file's own comments already single out that exit. It skips every other shared post-step step, for the same reason.

This item adds one more shared step, `self._finish_outcome(active_driver, outcome)`. It is called at all five call sites, in place of the bare `append`. That is one call apiece for the other three handlers and two for `_handle_action`. It does the append itself. A step kind added later still needs
no wiring here. `_drain_step_interruptions` already gives the interruption check the same property
across the same four handlers.

A fast-suite test keeps a sixth settle point, added later, from reopening the same gap silently. The
test drives a failing step of every kind through the loop and asserts that each settled outcome
passed through `_finish_outcome`. It is a behavioural pin, not a grep of `_step_runner.py` for the
literal `self.state.outcomes.append`. It also catches a settle point spelled `insert`,
`extend`, or `+=`, or reached through a local alias.

`_finish_outcome` checks `isinstance(active_driver, base.AppCrashSignal)` and `outcome.ok is False`,
together, right before that append. It adds a scenario-level exemption, described next, for the one
action that deliberately terminates the app itself.

A nested step's failure reaches `_finish_outcome` more than once by construction, not only at the
handler that first observed it. `_run_if` and `_run_for_each` both run their body through
`self.exec_steps`, the same re-entrant loop that an interrupt's own recovery uses. Take a crash three levels deep, such as an action inside a `forEach` inside an `if`. It settles three outcomes in turn: the action's own, the `forEach`'s, and the `if`'s. Each is a separate call to
`_finish_outcome`.

The `after` phase adds one more call for every cleanup step it runs against an app that is already
gone. That includes a step that runs *because* an earlier `relaunch` failed, against the app that
`relaunch` itself just terminated. `_dispatch_after` dispatches these through its own
`run_phase(steps, after_outcomes, "after", …)` call
([`_functions.py:824-828`](../../bajutsu/common/orchestrator/loop/_functions.py)). That route is
separate from the main steps' own loop and builds its own fresh `StepLoopState`
(`_functions.py:994`).

The `after` phase does not go through `_run_recovery`
([`_step_runner.py:70`](../../bajutsu/common/orchestrator/loop/_step_runner.py)). `_run_recovery` is
BE-0314's own re-entrant path for an *interrupt's* recovery steps. It sets
`self.state.running_recovery = True` around the same `exec_steps` (`_interrupt_guard.py:70`).

`_run_recovery` is a third, distinct probe multiplier, and this design has to account for it on its
own terms. An interrupt whose recovery steps run against an app that is already gone settles one
outcome per recovery step. Each is an ordinary (non-`relaunch`) failure that neither latch bounds.
This is the same cost that the next paragraph already prices in for depth and `after` rules, from a
third source.

Keying the `relaunch` exemption to `outcome.action` alone misses exactly that case. A failing
`relaunch`'s own outcome skips the probe. The `forEach` / `if` outcomes wrapping it, and every
`after: on: error` step dispatched afterward, carry a different `outcome.action`. Each would probe
fresh and read `app.state`'s honest `notRunning` as a fresh confirmed crash.

The exemption has to be a property of the *scenario*, not of one outcome. It lives on a
mutable object that `run_scenario` creates once and hands to every `run_phase` call through its
closure. `run_scenario` already shares `live_bindings` across phases the same way
(`_functions.py:690`, `:699`). The object is carried on `StepLoopState` next to `bindings`, not on
`_LoopConfig`.

`_LoopConfig` is `@dataclass(frozen=True)`. It is documented as "the run-invariant inputs the step
loop reads but never mutates". `mailbox` and `progress` are callables that the loop only invokes, never state it writes through. They set no precedent for the latch. `StepLoopState` already is
that precedent. `_run_steps` builds a fresh one per phase, but hands each the same `bindings` object
that `run_scenario` created once. That is exactly the scenario-scoped sharing this latch needs. It
costs one more parameter alongside `bindings`, rather than a new field on a class documented never
to hold one.

`_finish_outcome` sets the latch the moment it sees `outcome.action == "relaunch"` and `outcome.ok is False`. It does so before it ever calls `app_crash_signal()`. It checks the latch first on every
later call. Once the latch is set, no later outcome in the same scenario probes at all, wrapping
outcomes and `after`-phase steps included. None is misread as a crash that the check itself caused.

The same object also latches a *confirmed* crash, the first time `_finish_outcome` raises and
catches `AppCrashedError`. A later outcome in the same propagation then folds the already-known signal into its own `outcome.reason`. It does not probe `app_crash_signal()` again.

Only the first, confirming outcome sets `outcome.app_crashed = True` and captures
`app_crash_artifacts`. A wrapping `if` / `forEach` outcome that settles afterward sees the latch
already set. It still gets the message folded into its own `reason`, for a contributor reading the
failure. It leaves its own `app_crashed` at the dataclass default (`False`) and calls
`capture_app_crash` nowhere.

This keeps `pipeline.py`'s later scan (*Wiring the capture*) unambiguous. A scenario carries at most
one `app_crashed` outcome by construction. The scan never has to choose among several competing
ones.

The latch does not set on an unconfirmed answer, and that asymmetry is deliberate. A `None` from one
step teaches nothing about whether the *next* step's own failure is a crash. Latching there would
risk missing a real one.

For that reason, the bound this bought is narrower than "once per scenario". It holds for the
relaunch and confirmed-crash cases above. It does not hold for an ordinary failure with no crash
behind it. Such a failure still pays one probe per settling outcome: the nesting depth, plus one per
failing `after` step. An earlier draft claimed to avoid that cost entirely.

That cost is still bounded by how deep a scenario nests and how many `after` rules it dispatches on
failure. Both figures are usually small in practice. The probe also runs only on a step that has
already failed. The proactive per-step polling that *Alternatives considered* rules out below would
instead add cost to every green run.

"Usually small" prices a single round trip per probe. That holds on iOS but not on Android, where
an unconfirmed probe polls to a multi-second bound rather than answering in one call. *Android:
`logcat`'s crash buffer first...* below bounds that cost per scenario instead.

This reactive shape has one structural blind spot, which is worth naming here rather than
discovering later. The blind spot is a crash caused by a scenario's own *last* step, where that
step's own actuation still reports `ok=True`. Two examples: a `tap` that the runner delivered before
the app died, and an `assert` that read a tree captured a moment earlier. That step's own outcome
never probes.

The scenario either ends up classified or ends up green with the gap this item accepts. Which one happens depends on what runs after the step. Nothing that follows is guaranteed to fail and reach
`_finish_outcome`.

- `scenario.after` is reached "on every path out of `steps`/`expect`"
  ([`_functions.py:821`](../../bajutsu/common/orchestrator/loop/_functions.py)), the cancelled path
  included. It is present only when the scenario declares one.
- `scenario.expect` runs only when one is declared *and* `failure is None`.

A scenario declaring neither behaves as this item intends. Nothing runs after the
crash-causing step, so the scenario stays green with no `app_crashed` outcome and no `app-crash/`
artifact. Closing the gap for every such scenario would mean an unconditional end-of-scenario probe.
That is the every-green-run cost this reactive design exists to avoid. This item accepts the gap by construction rather than widen the trigger.

The other two shapes do not land on that same "stays green" outcome. Each is worth naming on its
own:

- A scenario-level `expect` still runs after the last step, even against the now-dead app.
  `_evaluate_expect` produces `AssertionResult`s, not `StepOutcome`s, so it never reaches
  `_finish_outcome`
  ([`_functions.py:741-752`](../../bajutsu/common/orchestrator/loop/_functions.py)). The scenario
  goes **red, but unclassified**: an ordinary `expect:` failure with no `app_crashed` outcome and no
  `app-crash/` artifact, rather than green. This is a second, distinct blind spot that this item
  also accepts by construction. It is not a variant of the first. A contributor reading the failure
  sees an assertion mismatch, not a crash.
- An `after` rule whose trigger is not the failure one (`on: always` or `on: success`) still dispatches. It still actuates against the now-dead app. That teardown step's own outcome *does*
  reach `_finish_outcome` through the ordinary `run_phase` call that `_dispatch_after` makes. This
  shape is classified after all, with `app_crashed` set and `app-crash/` written. The
  crash is caught one step later than the triggering one, not missed.

A scenario written to *exercise* this signal ends with a step after the crash-triggering
one. It does not rely on `expect` or an unrelated `after` rule to surface the crash. Unit 11's
showcase scenarios each do this: tap the crash trigger, then take one more step against the now-dead
app. That later step, not the tap itself, fails and gets probed. Only one kind of scenario stays green by this design's own construction. Its crash-causing step is genuinely its last one, with no `expect` and no non-failure `after` rule to run afterward.

`_finish_outcome` calls `active_driver.app_crash_signal()` only when neither latch is set. A
non-`None` answer raises `base.AppCrashedError(signal)` at once and catches it in the same
expression. This is the one `raise`-within-`try` that this item deliberately keeps. It carries a
`# noqa: TRY301` with a suppression reason ([`CLAUDE.md`](../../CLAUDE.md)'s inline-comment rule),
rather than restructuring the raise away. `AppCrashedError`'s whole reason to exist is naming this
one raise/catch pair, as its own docstring above says.

The catch folds the message into `outcome.reason` and sets both `outcome.app_crashed` and the
confirmed-crash latch to `True`. In the same catch, when `self.cfg.capture_app_crash` is not `None`,
it also calls that callable synchronously. It stores the result on a new
`outcome.app_crash_artifacts: tuple[tuple[str, bytes], ...]` field.

Capturing right here, at confirmation, keeps a later teardown step from moving the evidence out from under the sweep. The capture does not wait for the scenario to finish (see *iOS: matching the
`.ips` report* and *Wiring the capture*). The exception itself never propagates past this one point.

`active_driver` is already whichever driver actuated this step. A `web` block's inner steps and its
own wrapping outcome answer differently. `_handle_web` runs `step.web.steps` on a `WebContextDriver`
that it constructs for the block (`self.exec_steps(step.web.steps, web_driver)`). The inner steps'
`_finish_outcome` calls (through `_handle_action`) see `active_driver = web_driver`, and
`isinstance` answers `False` there. That is a genuine no-op, matching this item's `web`-backend
scope from the Introduction.

`_handle_web`'s own wrapping outcome is a separate, fifth call to `_finish_outcome`. It is
deliberately passed the block's `active_driver` parameter instead
([`_step_runner.py:287-290`](../../bajutsu/common/orchestrator/loop/_step_runner.py), "not the inner
`web_driver`"). That parameter is the *native* driver that was active before the block switched
context.

A native host crash can surface as the wrapping `web` step's own failure. An inner step fails because the native host died out from under the `WebView` bridge. That case is not a no-op.
`_finish_outcome` probes the native driver as it would for any other failing step.

### iOS: `app.state`, not the element tree

XCUITest already exposes a target app's process state through `XCUIApplication.state`. This enum's
`notRunning` case answers "is it actually gone" directly. `crawl`'s `is_app_alive` instead infers
the same fact from an empty or unexpected element tree. That inference carries a real risk of a false positive. A system alert can cover the app's UI, or the scenario can contain a deliberate `background` step. `app.state` sidesteps both. A backgrounded-but-alive app answers `runningBackgroundSuspended`
or `runningBackgroundActive`, never `notRunning`.

A `notRunning` answer is not, by itself, proof of a crash on every platform. On a real device, an OS
memory-pressure kill or an unfinished launch could answer the same way. Neither applies to the
Simulator. The XCUITest backend is not Simulator-only, though.

`xcuitest.deviceType: device`
([`xcuitest_config.py:20`](../../bajutsu/common/config/schema/xcuitest_config.py)) drives a real
iPhone through this same `XcuitestDriver` and `XcuitestEnvironment`. The Simulator's own constraints
do not hold there. A real device *does* jetsam-kill a foreground app under memory pressure. The
`.ips` sweep below also has nothing to read there, for two reasons:

- A real-device crash never lands in the host's own `~/Library/Logs/DiagnosticReports`.
- `self._bundle_id` is `None` on a real device
  ([`xcuitest_environment.py:319`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)).
  The build is installed out of band, rather than through the `_prepare_simulator` path that this
  item's `Info.plist` read depends on.

This item scopes to the Simulator only, the same way it already scopes the web backend out to a
follow-up item. `XcuitestDriver.app_crash_signal()` answers `None` outright on a real device. That
is the same "cannot confirm" answer that a backend with no signal at all gives. The driver does not
misread a real-device OS kill, or a `deviceType: device` target's own gap in evidence, as a
confirmed app crash.

One scenario step does deliberately terminate the app on either device type: `relaunch`. Its iOS
path is `device_relauncher`'s `e.terminate(bundle_id)` then `e.launch(...)`
([`relaunchers.py:64`, `:78`](../../bajutsu/common/platform_lifecycle/relaunchers.py)). It is not
`_resume_warm`'s identical-looking pair at `xcuitest_environment.py:855-856`, which is BE-0291's
*cross-lease* warm-reuse path.

`XcuitestEnvironment.relauncher()` does override `_DeviceEnvironment`'s implementation. The reason is unrelated: it re-stamps `app_launched_at` (see *iOS: matching the `.ips` report*). That
override only wraps the `RelaunchFn` that `device_relauncher` already returns. It never calls
`_resume_warm`, which stays a separate, cross-lease code path that this step never touches.

`_resume_warm` is not exempt from this section's own `app_launched_at` concern, though. It is a
fourth launch site in its own right, with its own re-stamping fix, described below.

`_finish_outcome` skips the probe once a `relaunch` step itself fails. It also suppresses
every later probe that the same scenario would otherwise make (see *Detecting the event*). That
guard matters less than it looks, for four reasons:

1. `relaunch`'s own closure calls `readiness.await_ready(...)`
   ([`relaunchers.py:79`](../../bajutsu/common/platform_lifecycle/relaunchers.py)) purely for its
   side effect, and discards the `ReadinessResult` it returns.
2. `await_ready` itself never raises. A timed-out wait returns `ReadinessResult(False, "timeout", …)`
   just as a successful one returns `True`
   ([`readiness.py:170`](../../bajutsu/common/platform_lifecycle/readiness.py)).
3. A `fatalError()` during the *new* launch never fails the `relaunch` step at all. That
   is the exact case Unit 11's showcase affordance exists to trigger. `await_ready` times out
   silently, the closure returns, and the step reports `ok=True`.
4. The surrounding tooling does not turn that into a failure on the step's behalf. `e.terminate`
   swallows its own `CalledProcessError` outright
   ([`env.py:176`](../../bajutsu/common/backend_cli/simctl/env.py)). On the rare device or tooling failure, `e.launch`'s own `CalledProcessError` does raise. `_run_step_body`'s exception net does not name it. The error escapes `run_scenario` entirely rather than becoming this step's
   `outcome.ok = False`, so `_finish_outcome` is never reached for it either.

A `relaunch` step's own outcome carries `ok=False` today by one path alone: the alert-guard / mid-wait-recovery-failure path. The nested-failure paragraph in *Detecting the event* above covers it. That path is genuinely unrelated to the app's own health, which is exactly the case the
exemption exists for.

A crash during the new launch is not lost, only attributed one step later. It fails the very next
step that touches the app. That is an ordinary (non-`relaunch`) outcome that neither latch has any
reason to skip. `_finish_outcome` probes it as it would any other failure.

Every other check also runs only once a step has already failed. By then the app was observed running through every earlier step of the same scenario. That observation does not fully exclude a
launch that never completed. A scenario's very first failing step has no earlier step to have
observed the app running. That step is the first of `before`, or of `steps` when no `before` is
declared. Nothing between `env.start` and that step confirms that the app ever reached the
foreground.

`launch_driver` calls `await_ready` and carries its `ReadinessResult` straight through. It does not raise ([`launch.py:99`](../../bajutsu/common/runner/launch.py)), the same as `relaunch`'s own
closure above. A target actually reaches `readiness.ready is False` in one case alone. Its `readyWhen` / namespace signal never resolves against a process that already went away.

A target declaring neither falls through to `await_ready`'s weakest rung, the bare
`len(elements) >= 2` count
([`readiness.py:151-152`](../../bajutsu/common/platform_lifecycle/readiness.py)). That function's
own docstring already names this as a rung that a slow cold boot's SpringBoard icons can satisfy. They can do so before the app itself foregrounds. A target whose app exits on a bad `launchEnv` can still land on `ready=True, signal="count"`. That result carries no evidence that the target app itself ever rendered.

The design closes this gap in four moves.

**1. Carry the readiness result on the lease.** `Lease`
([`bajutsu/common/runner/types.py:44`](../../bajutsu/common/runner/types.py)) gains a
`readiness: ReadinessResult | None = None` field. The `Lease(...)` constructor
([`pool.py:563`](../../bajutsu/common/runner/pool.py)) sets it alongside `sink=sink`. The value comes
from the `readiness` that `launch_driver` already returns earlier in the same closure (`pool.py:408`).
Today that value reaches only the `FileSink` first-wait diagnostic. A `Lease`-side copy is what is
missing, not a first capture.

**2. Seed a third latch field.** `run_scenario` gains a matching
`readiness: ReadinessResult | None = None` parameter. It is threaded from `lz.readiness` the same
way `relaunch=lz.relaunch` already is ([`pipeline.py:898`](../../bajutsu/common/runner/pipeline.py)).
`run_scenario` seeds the scenario-scoped latch object (Unit 7, below) with a third field.

The field is seeded once, when `run_scenario` creates that object. It never seeded on each
phase's freshly built `StepLoopState`. A `StepLoopState` only *shares* the object through the
closure, the same way the other two latches already do. A success in one phase stays
cleared into the next.

The launch counts as unconfirmed when `readiness is None or not readiness.ready or
readiness.signal == "count"`. The bare-count rung cannot tell the app from SpringBoard. A `ready`
answer there is no evidence that the app ever foregrounded either. That would be exactly
the misdiagnosis this item exists to remove, inverted.

**3. Clear the flag on a positive observation only.** The count rung is the *ordinary* answer, not a
rare one. `launch_driver` never passes `id_namespaces` to `await_ready`
([`launch.py:99`](../../bajutsu/common/runner/launch.py)), so `namespace` never fires on this path.
Every target declaring no `readyWhen` lands on `count` at every launch. This repository's own
`showcase-*-noax` targets are among them
([`demos/showcase/showcase.config.yaml:90`](../../demos/showcase/showcase.config.yaml), `idNamespaces: []`
with no `readyWhen`, each carrying a full scenario suite).

The `relaunch` flag latches unconditionally for the rest of the scenario. Latching this flag the same way would disable the item outright for that whole class of targets. It would not merely protect
their first step. `_finish_outcome` instead clears the flag the moment it sees a settled outcome
whose own success required the app to answer.

An `if` / `forEach` wrapping outcome never clears the flag, because its own success proves nothing.

- `_run_if` takes the empty `else` branch and settles `ok=True` the instant its condition fails to
  match ([`_functions.py:922-926`](../../bajutsu/common/orchestrator/loop/_functions.py)).
- `_run_for_each` settles the same way over zero matched elements.
- Neither raises when the app behind that query is dead. `driver.query()` still answers something
  (SpringBoard's own tree, on iOS).
- A target that never foregrounded and opens with exactly such a step would clear the flag
  *because* the app is dead, on the very target class the flag exists to protect.

The same holds for a kind that never reaches the app at all: `http`, `generate`, `totp`, `email`,
and `push`. The clear is keyed positively rather than by exclusion. Two kinds of step supply the positive observation that the scenario's first step lacked. One is a genuine actuating step *other than `relaunch`*. The other is a `wait` / `assert` that matched.

`relaunch` is excluded for the reason this section gives above. Its closure discards `await_ready`'s
result and `await_ready` never raises, so its `ok=True` says nothing about the app. A settled
`relaunch` re-*sets* the flag rather than clearing it. The `relaunch` has just put the app back into
the same unconfirmed state that a launch it cannot verify leaves it in.

The protection holds for a first failing step. It does not silence every later step on a
target whose readiness rung just happens to be the weak one. And no no-op `if` / `forEach`, step
that never touches the app, or unverifiable `relaunch` can masquerade as that same observation.

**4. Skip the probe until then.** Until that first success, `_finish_outcome` skips the probe. It treats an unconfirmed launch the same way as a failed `relaunch`. A launch that this item cannot yet confirm leaves the scenario in the same "app state is not yet known" position. A failed `relaunch` leaves the scenario there for the rest of its run.

`ReadinessResult` documents itself as "Pure diagnosis: it never enters a verdict (prime directive 1)"
([`protocols/readiness_result.py:15-16`](../../bajutsu/common/platform_lifecycle/protocols/readiness_result.py),
repeated on `signal` at `:27`). That is true of every consumer before this one, which only ever
displayed the result on a wait-timeout diagnostic. This item makes it the first consumer that decides
behavior from the result rather than wording a message. The same change that adds `Lease.readiness`
also updates that docstring to name this new use, before the invariant goes stale.

A `notRunning` answer anywhere else means that the app is not running now, though it was a moment ago. On such a host, nothing else could cause that.

A new route joins
[`BajutsuKit/Sources/BajutsuRunner/openapi.yaml`](../../BajutsuKit/Sources/BajutsuRunner/openapi.yaml).
The route mirrors `/systemAlert/query`'s shape (BE-0316): a request body, and a JSON reply carrying
the state. The generated `APIHandler`
([`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift))
gains the matching method. Its provider implementation asks the runner's own `XCUIApplication` for
`.state`.

`RunnerServer.swift`, not `Router.swift`, is what serves a request today. It constructs an
`APIHandler` and registers its generated routes. `Router.swift` is kept only for parity tests and
answers nothing in a real run. An earlier draft of this item specified a route added only to
`Router.swift`. That route would leave `XcuitestDriver`'s request 404ing against the real server.

`XcuitestDriver`
([`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`](../../bajutsu/common/drivers/xcuitest/xcuitest_driver.py))
gains an `is_real_device: bool = False` constructor argument. `make_driver` threads it the same way
it already threads `device_os` (BE-0358). `app_crash_signal()` checks it first. When it is `True`,
the method answers `None` immediately, before calling the route at all. That is the Simulator-only
scope decided above.

Otherwise `app_crash_signal()` calls the route once. A `notRunning` answer becomes the signal
string. Every other state answers `None`.

A channel error reaching this call is not swallowed into `None`. It is the existing
`XcuitestRunnerCrashError`, a `BackendCrashError`. This item leaves it to propagate unchanged,
straight into the recovery path that already owns it. A route failing right after the step's own
selector failure is ordinary contention. It is not evidence that the channel is unrelated to this
step.

### iOS: matching the `.ips` report, reusing BE-0421's landed sweep

[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md)
is `Implemented` (PR [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)). It already finds
the right `.ips` file among everything that macOS wrote to `~/Library/Logs/DiagnosticReports` for
the runner's own crashed `xcodebuild` process. It does so through two functions in
[`bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py):

- `_diagnostic_reports_dir()`
- `_reports_since(reports_dir, pattern, since)`, a generic name-and-time match with no `xcodebuild`
  in its own signature

A third function, `_reported_pid()`, narrows the match further when a report's header parses.

This item reuses `_reports_since` directly for the app under test's own binary. It does not write a second sweep over the same directory. With two sweeps, a fix to one match rule would silently leave the other wrong. Examples are a new `.ips` header format or a `DiagnosticReports` relocation.

Narrowing by process ID (PID) is not available here the way it is for BE-0421's own report. XCTest's
public `XCUIApplication` surface has no PID accessor, and nothing in `BajutsuKit/` reads one today.
This item narrows by the Simulator's unique device identifier (UDID) instead.

A Simulator app's `.ips` report carries the executable's full install path in its *payload*. The payload is the second of the file's two JSON documents. The path is not in the one-line header.
[`_reported_pid`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py)'s own
docstring explains the split:

- The header carries `app_name` / `bundleID` / `timestamp` / `os_version` / `incident_id`. It is
  cheaper and more stable, but has no install path.
- The pid, and this item's own udid, live in the payload. `_reported_pid` already parses the payload
  for exactly that reason.

The install path has the form `.../CoreSimulator/Devices/<udid>/data/Containers/Bundle/Application/…`.
It names the specific Simulator that the crashed process ran on.

`Lease` ([`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)) already records
the leased device's own `udid`. A new `_app_crash_reports(pattern, launched_at, udid)`
does the narrowing. It is a sibling of BE-0421's own `XcuitestEnvironment._crash_reports(spawned_at, pid)`. That method needs no `pattern` argument, because it hardcodes `"xcodebuild-*.ips"` for its own known process.

`_app_crash_reports` reads each candidate that `_reports_since` returns. The function answers
`list[Path]`, and a report's own filename carries no udid. It accepts a candidate only when the *executable* path in the report's own payload names that same `udid`. This replaces `_reported_pid`'s check.

The header's own `bundleID` field is a cheaper and more stable narrowing. It is worth reading alongside the payload check, not instead of it. `XcuitestEnvironment` already holds that
value as `self._bundle_id`.

This narrowing disambiguates two Simulators that run the identical target binary in the same window.
A CI host running two lanes in parallel (`--workers 2`) can produce that case. The narrowing needs no
PID at all.

`_reports_since(reports_dir, pattern, since)` still matches by filename, though. A `.ips` report's
filename names the process that crashed (`Showcase-2026-…ips`), never the bundle id. BE-0421 passes
the literal `"xcodebuild-*.ips"` for its own known process.

`XcuitestEnvironment` holds `ios.bundle_id` (`self._bundle_id`, `com.example.Showcase`). That value
is not the process name and never matches a report's filename. This item instead reads `CFBundleExecutable` from the installed app's own `Info.plist`. The read happens inside `app_crash_artifacts()` itself, at `Path(self._app_path) / "Info.plist"`. Every iOS bundle is required to declare that
key. The sweep's pattern is built from it.

`app_crash_artifacts()` is a zero-argument method, per the `RunEnvironment` protocol shape (*iOS:
matching the `.ips` report*'s own introduction). It has no `eff` in scope to read
`ios.app_path` from live. `self._app_path` is a new field, stashed at `start()`. It sits right next to `self._bundle_id` (`self._bundle_id = ios.bundle_id if device_type != "device" else None`,
`xcuitest_environment.py:319`). It is stashed the same way that field already is, from the same
`ios` that this section already resolved `self._bundle_id` from.

`ios.app_path` is itself optional (`str | None`,
[`target_config.py:107`](../../bajutsu/common/config/schema/target_config.py)). A `deviceType:
simulator` target that names only `bundle_id`, against a Simulator that already has the app
installed, configures none. `_prepare_simulator` already gates its own install on the same case
(`if ios.app_path:`, `xcuitest_environment.py:902`). The replacement-device path raises its own
named error rather than assume the field is set (`xcuitest_environment.py:627-632`).

There is no PID accessor to derive the executable name from instead, which is the reason this reads
`Info.plist` at all. So there is no fallback pattern to build. This capture is scoped to targets
that configure `appPath`, and the scope is checked explicitly, before the read. `self._app_path is
None` resolves to `[]` immediately. That is the same named, up-front case that a non-macOS host is
below. It is not left to fall through into `Path(None)` raising inside the broad `except Exception` a few paragraphs down. There it would read as an accidental, unexplained miss.

`XcuitestEnvironment`
([`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py))
already knows its own `udid`. It gains an `app_launched_at` timestamp.

The cold launch itself has no Python-side `e.launch(...)` call to stamp the marker next to.
`_spawn_cold` installs the app and spawns `xcodebuild`. The runner's own `XCUIApplication.launch()`
launches the app *inside* the runner. This codebase's own comment says so outright
(`xcuitest_environment.py:322-324`). The only calls on the Python side around it are
`_prepare_simulator`, `_launch_params`, and `_spawn_cold_with_retry`.

Stamping the marker *after* `_spawn_cold_with_retry` returns would leave it later than the launch it
names. Consider an app that crashes during its own cold launch. The runner's own `/health` still
answers, so the spawn itself succeeds. The `.ips` mtime would precede a marker stamped only once the
spawn call returns. `_reports_since`'s `mtime >= since` would reject it. That is exactly the "the
report exists but `bajutsu` did not find it" outcome this item exists to remove.

`app_launched_at` is recorded in `_spawn_cold`, at once *before* the
`_spawn_cold_with_retry` call. It is the safer of the two available moments. A marker stamped too
early can only over-collect, by widening a window that the `udid` and executable-name checks already
narrow. It can never miss the crash it exists to catch, unlike a marker stamped too late.

A `relaunch` step's own launch is the one this section just distinguished from `_resume_warm`'s. It
runs through `device_relauncher`'s closure over `(udid, run, extra_env)`. No `XcuitestEnvironment` is
in scope there to update the marker on. Left alone, `app_launched_at` would stay frozen at the
lease's original cold launch. A crash following a mid-scenario `relaunch` would then sweep
`DiagnosticReports` with a `since` that reaches back before the relaunch. That window is wide enough
to attach a `.ips` from a crash that the `relaunch` itself already superseded.

`XcuitestEnvironment` overrides `relauncher()`. The override leaves `_DeviceEnvironment`'s
own implementation unchanged otherwise. It wraps the `RelaunchFn` that `device_relauncher` returns,
and records `app_launched_at` *before* calling it. That is the same ordering `_spawn_cold` already
uses.

`AndroidEnvironment` needs no matching override. It already overrides `relauncher()` itself
([`android_environment.py:326`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)).
Its `e.launch` there is one of the three sites that Unit 5 already names.

`_DeviceEnvironment.crawl_reset()`
([`ios.py:124`](../../bajutsu/common/platform_lifecycle/environments/ios.py)) is a third iOS launch
site with the identical shape: `e.terminate(bundle_id)` then `e.launch(...)`. `crawl`'s own `reset`
callable runs it on every frontier revisit ([`cli.py:300`](../../bajutsu/crawl/cli.py)). It does not
go through `relauncher()` at all.

Left unpatched, the same staleness that the `relauncher()` override just closed reopens on this
path. A crawl records several crashes per run (`current_fp = None; continue`,
[`_functions.py:668-669`](../../bajutsu/crawl/core/_functions.py)). The second crash's sweep would
reach back before its own `crawl_reset` and accept the *first* crash's `.ips` report instead. That
is the wrong crash, in a directory whose whole purpose is attributing the report correctly.

`XcuitestEnvironment` overrides `crawl_reset()` too, in the same shape as the `relauncher()`
override. `crawl_reset(eff)` is a *factory*: `_build_lane` calls it once and keeps the `Reset` that
it returns ([`cli.py:300`](../../bajutsu/crawl/cli.py)). The override wraps that returned
`Reset`, rather than stamping beside the factory call. It records `app_launched_at` *before* calling
`_DeviceEnvironment`'s `Reset`, once per frontier revisit.

`AndroidEnvironment` needs no matching override here either. The `e.launch` in its own
`crawl_reset()`
([`android_environment.py:410`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py))
is the third of the three sites that Unit 5 already names.

`_resume_warm` is BE-0291's *cross-lease* warm-reuse launch
([`xcuitest_environment.py:855-856`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)).
It is a fourth iOS launch site, on the same long-lived `XcuitestEnvironment` instance across leases.
`start()` returns through it whenever the runner is reusable (`:297`). `XcuitestEnvironment`
overrides `has_reusable_resident()`, so BE-0291 keeps that environment across leases rather than
tearing it down. Nothing on this path re-stamps `app_launched_at` either.

Left alone, the same staleness that the other two overrides close reopens here. It is worse here, because both disambiguators fail together. Here is the sequence:

1. Scenario 1 cold-launches on device X (marker = T0). Its app crashes, leaving `Showcase-…ips`.
2. Scenarios 2–5 resume warm on the same device, with the marker still at T0.
3. Scenario 5's app crashes and sweeps `DiagnosticReports` with `since = T0`.
4. Scenario 1's report matches the executable-name pattern (same app) *and* the `udid` check (same
   Simulator). `_app_crash_reports` accepts it, and scenario 5's `app-crash/` gets scenario 1's crash.

The fix has the same shape as the other two. `XcuitestEnvironment` records `app_launched_at` inside
`_resume_warm` itself, at once *before* its own `e.launch`. The marker then tracks whichever
launch the app is actually running under.

A new `app_crash_artifacts() -> list[tuple[str, bytes]]` joins the `RunEnvironment` protocol
([`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py)).
It sits next to `take_crash_snapshot()`, but is plainer. `take_crash_snapshot()` returns a *thunk*.
A backend crash is captured where it is first observed, then deferred until the pool releases the
lease. A concurrent worker's next launch on the same warm environment cannot overwrite the
frozen match criteria first.

This item's capture has a narrower version of the same problem, from inside the *same* scenario
rather than a concurrent one. A crash confirmed mid-scenario can still be followed by an `after` rule
as ordinary as a teardown `relaunch`. That `relaunch` re-stamps `app_launched_at` through this same section's own `relauncher()` override. It does so before the scenario's `RunResult` is even assembled.

Reading the marker live at any point *after* that would sweep with a `since` that the teardown
already advanced past the crash. That includes `pipeline.py`'s own post-return scan, an earlier
draft's chosen call site.

`app_crash_artifacts()` is called synchronously inside `_finish_outcome`. The call happens the moment the crash is confirmed. That is strictly before any later step in the same scenario runs (see *Detecting the event*). Its result is carried on the `StepOutcome` rather than re-read later. There
is no thunk, since nothing here is deferred past a point where the marker could move. The call
itself simply moves earlier, to where the reactive check already is.

`RunEnvironment` is a structural protocol that no concrete class subclasses. `take_crash_snapshot()`
has no inherited default either. `WebEnvironment`, `AndroidEnvironment`, and `_DeviceEnvironment` each already declare their own one-line `return` for it. `FakeEnvironment` inherits the `_DeviceEnvironment` one.

`app_crash_artifacts()` follows the same shape, minus Android.

- `WebEnvironment` and `_DeviceEnvironment` (which `FakeEnvironment` inherits) each gain their own
  `return []`.
- `XcuitestEnvironment` and `AndroidEnvironment` each override the method with the real capture that
  this section and the next describe.

Every leased environment needs one of the two. `pool.py`'s `lease()` reads the method off every leased environment, on every lease, not only a crashed one.

A second, narrower method joins the same protocol, for the reason that *Android: `logcat`'s crash
buffer first, a root-gated tombstone pull second* gives in full. The method is
`app_crash_tombstone() -> list[tuple[str, bytes]]`. Only `pipeline.py`'s post-return scan calls it,
never `_finish_outcome`.

The method exists because Android alone splits its capture across two call sites with different
timing constraints. No other backend needs a second method. But `pool.py`'s `lease()` closure still
reads it off every leased environment the same unconditional way it reads `app_crash_artifacts()`. A
structural protocol member needs a declaration everywhere, not only where the real work
happens.

`WebEnvironment` and `_DeviceEnvironment` each gain a matching one-line `return []`. It sits alongside their `app_crash_artifacts()` no-op or override. `_DeviceEnvironment` is the base that `FakeEnvironment` and `XcuitestEnvironment` both inherit. `XcuitestEnvironment` inherits that no-op rather than repeating it. Only
`AndroidEnvironment` overrides it with the tombstone pull itself.

`ReportCrash` may not have finished writing the report the instant the app dies.
`_app_crash_reports` polls `~/Library/Logs/DiagnosticReports` for up to a few seconds. It looks for a report that matches the target's executable name and this environment's own `udid`. The report must also be modified at or after `app_launched_at`.

That poll is a short, bounded wait inside this one method, not a retry of the scenario. The scenario
still fails once, immediately, regardless of whether the sweep finds anything.

The sweep stays wrapped in one `try` / `except Exception` for its whole body, not only its final
write. A failure in the directory scan or the read is as unable to change the app's own
crash verdict as a missing report is. It resolves to an empty list the same way. A
non-macOS host also resolves to `[]` the same way, immediately.

### Android: `logcat`'s crash buffer first, a root-gated tombstone pull second

`AdbDriver`
([`bajutsu/common/drivers/adb/adb_driver.py`](../../bajutsu/common/drivers/adb/adb_driver.py))
implements `app_crash_signal()` with `adb shell pidof <package>`. `AdbDriver` is constructed from a
serial and a handful of injected callables today. It holds no package name, and neither does
`backends.make_driver`, which builds it.

The target's package name already reaches `AndroidEnvironment`. Its `install`, `pm clear`,
`force_stop`, and `launch` calls all take it from the target's own config
(`targets.<name>.android.package`). The driver does not receive it.

`make_driver` ([`bajutsu/common/backends.py`](../../bajutsu/common/backends.py)) gains a
`package: str | None = None` keyword. It threads the keyword into `AdbDriver.__init__` the same way
it already threads `fetch_clock` and `act`. The keyword is a plain constructor argument rather than
a `Driver` member. `Driver` is `@runtime_checkable`, with no shared base class. A data member there
would be a declaration that every backend and every inline test double has to repeat.

`package` defaults to `None` at that same boundary. Here `None` does not fail closed the way
`ios.app_path is None` does on the iOS side (*iOS: matching the `.ips` report*). `dumpsys activity
exit-info` with no package argument is not an error. It reports `ApplicationExitInfo` for *every*
package on the device.

An `AdbDriver` built with `package=None` would run `pidof` with no argument, which prints
empty output. It would then read a device-wide exit-info history whose newest entry is some other
process's `CRASH`. That would confirm a crash that the app under test never had.

Every scenario-run `AdbDriver` is built inside `AndroidEnvironment.start()`, where the package is
always in scope. This case is unreachable on the `run` path as designed. But `make_driver`
has other callers that pass neither `package` nor `launched_at`:

- [`bajutsu/serve/operations/_common.py:92`](../../bajutsu/serve/operations/_common.py)
- [`bajutsu/common/doctor/_functions.py:194,210`](../../bajutsu/common/doctor/_functions.py)

A silent `None` default is what would let one of those later inherit the unsafe answer.

`app_crash_signal()` checks `package is None` first, before either `adb` call. It also
checks `launched_at is None`, and a `launched_at()` that itself answers `None`. In each case it
resolves to `None` immediately. That is the same up-front, named case that `ios.app_path is None`
already is.

An empty `pidof` reply never reaches Python as an empty string. `AdbDriver`'s default
`RunFn` is `adb.real_run`
([`adb/_functions.py:111-112`](../../bajutsu/common/backend_cli/adb/_functions.py)), a bare
`subprocess.run(..., check=True)`. Toybox `pidof` exits 1 on no match, which is the routine,
expected outcome that this whole probe exists to observe.

`app_crash_signal()` wraps the `pidof` call in `except subprocess.CalledProcessError as
exc`. The repository has a precedent for a device command whose failure is expected rather than exceptional. This is `AdbDriver._rooted()`'s `except (subprocess.CalledProcessError, OSError)`
around `adb shell id -u`
([`adb_driver.py:1234-1235`](../../bajutsu/common/drivers/adb/adb_driver.py)).

- An exit with empty `stdout` reads as "no process", as an empty `real_run` return would
  have.
- Any other `CalledProcessError`, or an `OSError` from `adb` itself misbehaving, resolves to `None`.
  That is the same "cannot confirm" answer that this section already gives `package is None` and a
  too-old `api_level`. The error does not escape as an unhandled fault on the first real crash that
  the probe is meant to catch.

The same wrapping covers the `dumpsys activity exit-info` call below, for a transport hiccup
mid-poll.

An empty `pidof` answer, where the app should still hold a process, is necessary but not sufficient.
It also matches a launch that never completed. It also matches a termination for which this item has
no scenario-level cause. Android has no jetsam-style OS kill under normal test conditions. But an ordinary process exit answers `pidof` identically to a crash.

`adb shell dumpsys activity exit-info <package>` reports the platform's own `ApplicationExitInfo`
history for the package. Each entry is timestamped. The history distinguishes a `CRASH` or
`CRASH_NATIVE` reason from `ANR`, `LOW_MEMORY`, or `USER_REQUESTED`.

It is available only from API 30 onward. It is available on the API 34 AVD that this
repository's CI already boots. But nothing in this codebase tracks a device's API level today.
`AdbDriver` gains an `api_level: int | None = None` keyword, threaded the same way `package` is. The
value is read once via `adb shell getprop ro.build.version.sdk` in `AndroidEnvironment.start()`. It is stashed alongside `self._package`.

`app_crash_signal()` folds `api_level is None or api_level < 30` into the same up-front check as
`package is None`. It fails closed the same named way, rather than polling a signal that cannot exist
yet on an older device or emulator image. This item's own *Introduction* names the same scope for
Android's exit-info corroboration that it already names for iOS's Simulator-only scope.

The exit-info history persists across process lifetimes, though, so its most recent entry alone is
not reliable. An earlier scenario's crash on the same package can still be the newest entry that
`dumpsys` reports. That happens when the current process exited for an unrelated reason with no
`ApplicationExitInfo` recorded yet.

`AdbDriver` gains a `launched_at: Callable[[], tuple[float, str] | None] | None = None`
constructor argument. It is an injected callable that reads `AndroidEnvironment`'s launch marker
live. The marker holds the epoch *and* the `'%Y-%m-%d %H:%M:%S'` device-clock rendering beside it.
The seam is the same one that `fetch_clock` already uses for a per-call read rather than a value
frozen at construction.

A single exit-info read, taken the moment `pidof` answers empty, races the very crash it
corroborates. `pidof` reports empty the instant the process dies. `system_server` records the
matching `ApplicationExitInfo` only after it reaps the death. For a native crash that happens later
still, after `crash_dump` finishes. The newest entry can still be a stale one from before
this launch, on the very read meant to confirm a fresh crash.

`app_crash_signal()` polls the exit-info history. The poll is short and bounded, like the one `_app_crash_reports` already runs on `DiagnosticReports` on iOS. The poll runs for up to a few seconds.
It re-reads until the *newest* entry meets both conditions:

- It reports `CRASH` or `CRASH_NATIVE`.
- Its own `timestamp=` field is at or after this launch. That field is `ApplicationExitInfo`'s
  wall-clock rendering in the *device's* own timezone. It never an epoch and carries no offset.

The comparison is made against the rendering half of `launched_at()`. That half is the tuple's
second element. It is the `'%Y-%m-%d %H:%M:%S'` device-clock rendering recorded alongside the epoch at
each launch site. The comparison is never made against the epoch half. Resolving a bare `timestamp=`
on the host would run it through the *host's* timezone. A UTC emulator driven from a host in any other zone would then place every fresh crash hours before the marker. The signal would answer `None` on every real crash.

The poll ends in one of two ways. Either such an entry appears, which rules out a stale one from
before this launch. Or the bound expires. If the bound expires, or if the newest entry never meets
both conditions, the signal answers `None`. That is the same "cannot confirm" answer that a backend
with no signal at all gives.

This poll is the corroboration that `app.state`'s `notRunning` gets for free from the Simulator's own
constraints above. Android's own platform-reported, time-bound exit reason gives adb the equivalent positive confirmation. The read must get the same room to catch up that the write needs.

Only the *first* such poll in a scenario needs the full bound, though. That first probe is the one
racing `system_server`'s reap. A later probe in the same scenario has nothing new to wait out if the
first probe already burned the bound without a match. Examples of a later probe are a crash three
levels deep inside `if` / `forEach`. Every failing `after` step is another (per *Detecting the event* above).

`AdbDriver` gains an `self._exit_info_exhausted: bool = False` instance field. It has the
same shape as its existing `_act_warned` / `_act_unavailable` latches. It is set the first time a
bounded poll ends without a matching entry.

A driver instance never outlives one scenario on `run`. `AndroidEnvironment.has_reusable_resident()` answers `False` unconditionally. BE-0291's cross-lease warm reuse is XCUITest-only (see *iOS: matching the `.ips` report* above). A fresh `AdbDriver` is constructed per lease there.

`crawl` differs. It builds one `AdbDriver` for the *entire* walk. `_build_lane` calls `launch_driver`
once, and `crawl()` never rebuilds it ([`crawl/cli.py:283-300`](../../bajutsu/crawl/cli.py)).
*Extending `crawl`'s own crash recording* below wires this same probe into every detection along
that walk. An implicit reset that only `run` gets for free would leave the flag latched after the
first unconfirmed crawl detection. The latch would then un-poll every *genuine* crash for the rest of
the run.

Folding the reset into `invalidate_settled_cache()` would be the wrong seam. That method's own
contract is "the screen changed, through something other than this driver's own actuators". `_act`,
`_device_act`, and `type_text` all call it on every ordinary gesture too
([`adb_driver.py:352`](../../bajutsu/common/drivers/adb/adb_driver.py), `:1119`, `:1145`, `:1462`).
Folding this latch into it would clear the flag before nearly every probe that it exists to bound. That holds on both backends that this item wires the probe into.

A genuinely fresh launch is the event that makes waiting on a fresh `system_server` reap worth paying
for again. "The screen moved" is not. `AdbDriver` gains a second, narrow method for the
launch event. The method is `reset_exit_info_poll()`, the same shape as `SettledCacheInvalidator` next
to it.

It is called from the same two closures that already call `invalidate_settled_cache()` for the
*other* reason:

- `AndroidEnvironment.relauncher()`'s `relaunch()`, right after `e.launch(...)`
  ([`android_environment.py:339-351`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py))
- `crawl_reset()`'s `reset()`, right after its own `e.launch(...)`

It never called from inside `AdbDriver`'s own actuators.

Once the latch is set (and not yet cleared), a later call skips the bounded poll. It reads the
exit-info history exactly once, and answers at once whether or not that single read finds a
match. That is the bound that this item's own accounting already assumes an unconfirmed probe costs.
It is not the multi-second poll that Android's signal takes without this latch.

`AndroidEnvironment`
([`bajutsu/common/platform_lifecycle/environments/android/android_environment.py`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py))
gains the same `app_launched_at` tracking as the iOS environment. It records the marker immediately
*before* each of its three launch call sites (`e.launch(package, launch_env)`), never after.

`e.launch` is `am start -W`. Its `-W` flag waits for the launch to complete
([`adb/_functions.py:634-637`](../../bajutsu/common/backend_cli/adb/_functions.py)). A marker stamped
once it returns is already stamped after a startup crash has happened. It would reject that
crash's own `logcat -t` block and `exit-info` entry, both of which precede the marker. That is the
same "report exists but `bajutsu` did not find it" outcome that `_spawn_cold`'s own reasoning above
already avoids for iOS. A marker stamped too early can only widen the window that `start()`'s own
`force_stop` / `pm clear` already narrowed. It can never miss the crash it exists to catch.

Unit 11's Android fixture taps its trigger mid-scenario, so it does not exercise this ordering. The
case at stake is a startup crash: an app that dies before `am start -W` returns. Unit 13 pins that
case directly.

The marker reads from the device's own clock at launch time rather than the host's. A launch marker
compared only against later device-clock reads never needs host/device clock
reconciliation. `app_launched_at` itself is the epoch field of that read (`adb shell date +%s`). The
tombstone mtime comparison below consumes that value directly, since it compares against an epoch
already.

Neither of the other two consumers can use that same epoch value. The two reasons differ, but both
trace back to one rule: never resolve a device-side rendering into an epoch on the host. That
conversion runs through the *host's* timezone, not the device's. A UTC emulator driven from a host in
any other zone would then place every fresh timestamp hours away from the marker. That would
silently reintroduce the exact host/device clock reconciliation that this design otherwise avoids
entirely.

- **The exit-info poll.** `dumpsys activity exit-info`'s `timestamp=` field is `ApplicationExitInfo`'s
  own wall-clock rendering in the device's timezone, carrying no offset. The exit-info poll above
  compares it against a second device-clock rendering of the same launch moment
  (`'+%Y-%m-%d %H:%M:%S'`). That rendering is the second half of the `launched_at()` tuple that
  `AdbDriver` receives, never the epoch half.
- **`logcat -t`.** It cannot consume the epoch value either, for a more mundane reason. `adb logcat
  -t` is overloaded. An integer argument is read as a *line count* ("the most recent N lines"), not
  a time bound. Only a quoted `'MM-DD hh:mm:ss.mmm'` string is read as a time bound. That is a
  different rendering from exit-info's. `logcat`'s carries no year and exit-info's does, so neither
  substitutes for the other.

Each launch site takes **one**
`adb shell "date '+%s|%Y-%m-%d %H:%M:%S|%m-%d %H:%M:%S.000'"` read. The format is quoted for the
*device* shell, which would otherwise read the `|` as a pipe and word-split the rest. The site splits
the three fields on the host. That is a plain string split, never a timezone resolution, so the rule
above still holds.

A single read replaces three separate `date` invocations. Three invocations would stamp three
different instants. The `logcat` one, stamped last, would then filter out a crash that landed in the
gap.

The third field is stashed alongside `app_launched_at` for `logcat -t` alone to consume. Threading
the epoch straight through would silently return the whole ring buffer with no time bound at all. The
process bound below is by package alone. It would then let an earlier scenario's crash on the *same*
package through as this scenario's own.

This item's Android capture takes two layers, matching its own scoping decision to capture both. But
the two layers do not share one call, and they do not run at the same time.

- `logcat` is safe to read the moment the crash is confirmed, mid-scenario, inside `_finish_outcome`
  (*Detecting the event*). It needs no elevated access and touches no channel that a later step still
  needs.
- The tombstone pull does need elevated access. Firing it that early would be actively harmful (see
  below), so it stays on the original post-return path.

`app_crash_artifacts()` returns the `logcat` layer alone. A second, separate
`app_crash_tombstone()` returns the tombstone layer, and is called later. Each layer is independently
wrapped, so that a failure in one never drops the other:

1. **`logcat`'s `crash` buffer**, always attempted, needing no elevated access. `logcat -b crash` is
   a device-global ring buffer. It persists across launches and across scenarios that share the same
   device.
   [`scripts/collect_android_diagnostics.sh`](../../scripts/collect_android_diagnostics.sh) dumps it
   whole (`-b main,system,crash,events,radio`) at the end of every failed CI job. Clearing the
   buffer per launch would destroy evidence that this end-of-job sweep still needs.

   Nothing here clears the buffer. A later `adb logcat -b crash -d -t "<MM-DD hh:mm:ss.mmm launch marker>"` dump filters by time. The filter is the `logcat`-formatted rendering recorded above.
   It does not use the epoch `app_launched_at`. The dump holds only content from the launch
   this scenario is running. It never holds a stale crash that the same package left behind on an
   earlier run sharing the device. It also leaves everything before that marker intact for the
   end-of-job sweep to still find.

   The dump is parsed two ways, matching the two formats that `logcat`'s crash buffer actually
   carries:

   - A `FATAL EXCEPTION` block, for a managed-code (Java/Kotlin) crash.
   - When no such block is found, the native crash buffer's own `Fatal signal <n>` header line, for
     an NDK crash.

   Both parses are bounded to the app under test's own process, not to the time window alone. The
   buffer is device-global across processes as well as across launches. A managed block is accepted
   only when its own `Process: <package>` line names the target's `self._package`. A native block is
   accepted only when its `>>> <process> <<<` header does. A system service or another app crashing
   in the same window is never written as this scenario's evidence.

   A single dump taken at once can still come up empty for a native crash. `crash_dump` writes the
   `>>> <process> <<<` block after the death that `pidof` already reported the detection gate off of.
   That is the same asynchrony that `app_crash_signal()`'s own exit-info poll above exists to close.
   `app_crash_artifacts()` re-dumps in the same short, bounded way, for up to a few seconds.
   It stops when a match appears or the bound expires, rather than trusting the first `-d` snapshot.

   Whichever block matches is extracted and written as `logcat-crash.txt`. This is the one artifact
   guaranteed available on any AVD or real device that the adb backend can already reach.

2. **A tombstone pull**, best-effort and gated on root access, run last, after the `logcat` layer
   above. The four paragraphs below cover why the pull runs after the scenario, how it is gated and
   what it pulls, how root is restored, and why `crawl` does not use it.

   **Why the pull runs after the scenario.** `adb root` is already a routine operation against the
   emulator images this backend targets. But it restarts `adbd`.
   `scripts/collect_android_diagnostics.sh:98-102` already covers this with its own `adb
   wait-for-device` right after `adb root`, noting why: "adbd restarting as root". The restart has two
   effects:

   - It kills the resident server's own `am instrument -w` session outright, not merely its `adb
     forward` mapping. `instrument_cmd`'s `-w` is what "keeps the instrumentation attached … holding
     the `UiAutomation` session warm"
     ([`adb/_functions.py:604-616`](../../bajutsu/common/backend_cli/adb/_functions.py)).
   - It drops BE-0283's network-collector `adb reverse` tunnel (`android_environment.py:298-306`) the
     same way.

   Re-forwarding a port onto a session that no longer exists would not recover it. Restarting the
   resident server itself is the only fix, a heavier operation that this layer does not attempt.

   Firing the pull mid-scenario would be actively harmful, not merely wasteful. The firing point would be inside `_finish_outcome`, alongside the `logcat` layer. These steps still actuate and screenshot through that same
   resident server:

   - the wrapping `if` / `forEach` outcomes still settling
   - a scenario-level `expect` (`_functions.py:741-752`)
   - every `after` rule the scenario dispatches (`_functions.py:821-831`)

   A channel error there raises `BackendCrashError`. `run_scenario` deliberately re-raises that error
   rather than folding it into a failure (`_functions.py:839-846`). The error goes straight into
   `pipeline.py`'s crash-retry loop. That discards the very `RunResult` that this item exists to
   produce, `app_crashed` and `app_crash_artifacts` included.

   `app_crash_tombstone()` is a second, separate method. Only `pipeline.py`'s post-return
   scan calls it (*Wiring the capture*). This time the scenario has genuinely ended, not merely been
   believed to have. Everything left before `lz.release()` reads from `lz.collector`'s own
   already-captured data, never back through the driver. That covers the network snapshot write and
   the progress line.

   `AndroidEnvironment.start()` rebuilds the resident server and the reverse tunnel from scratch on
   every lease regardless (`_begin_resident`, `bridge_collector`). This is a routine "no warm resident
   kept" teardown-and-rebuild that the design already relies on. The next lease on this device is
   unaffected by what this layer leaves broken.

   **How the pull is gated and what it pulls.** `adb root` is followed by `adb wait-for-device`
   before anything else runs against the device. `collect_android_diagnostics.sh:99-103` puts the
   same gate between its own `adb root` and its `adb pull`. Without the gate, everything below races
   `adbd`'s restart.

   The pull then takes the most recent `/data/tombstones/tombstone_NN` The file must be modified at or after the launch marker above. The comparison uses device-relative timestamps, so no clock
   reconciliation is needed here either.

   Reading the marker this late carries none of the mis-attribution risk that a teardown `relaunch` raises elsewhere in this item for iOS `.ips` and `logcat` (*iOS: matching the `.ips` report*). A teardown
   only moves `app_launched_at` *forward*. The worst this bound can do is miss a tombstone that was
   already there. It can never attach an older one under the wrong name.

   A real device, a user build, or a refused `adb root` all resolve to skipping this layer silently.
   The event is still reported and `logcat-crash.txt` still lands. A device that refuses root loses
   only the native-frame detail that a managed-code crash never needed in the first place.

   **How root is restored.** `adb root` is paired with an `adb unroot` (and a second `adb
   wait-for-device`) once the pull finishes, inside the same best-effort wrapper. The device is then
   handed back at the privilege level that every other lease already assumes. Otherwise `adb root`
   persists device-wide until `adb unroot` or a reboot, and nothing else in this repository restores
   it.

   The one existing `adb root` caller
   ([`scripts/collect_android_diagnostics.sh:101`](../../scripts/collect_android_diagnostics.sh))
   runs at end of job. It deliberately leaves nothing to run after it. This layer's mid-run pull is
   not in that position.

   Left unrestored, a later scenario sharing this device would run through a root `adb shell` instead
   of an ordinary one. That includes `AndroidEnvironment.start()`'s own `install` / `pm clear` /
   `force_stop` / `launch` calls
   ([`android_environment.py:122-146`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)).
   The switch would be silent. Device state would turn on whether an earlier, unrelated scenario
   happened to crash. Anything downstream of shell uid would turn on it too, such as a pulled
   artifact's file ownership. That is exactly the run-order dependence that a deterministic core
   exists to keep out.

   The leak is sharper still. `AdbDriver._rooted()` probes `adb shell id -u` once per driver and
   caches the answer
   ([`adb_driver.py:1230-1237`](../../bajutsu/common/drivers/adb/adb_driver.py)). Two actuation
   decisions read it:

   - `double_tap` picks the raw `sendevent` path over `input tap` when the answer is `True`.
   - A two-finger gesture raises `base.UnsupportedAction` when the answer is `False`, but runs when
     it is `True`.

   Leaked root would flip both silently. A two-finger-gesture scenario that should fail loudly with
   `UnsupportedAction` on an unrooted device would instead run and pass. An earlier, unrelated
   scenario that happened to crash would decide that outcome. This is the same class of run-order
   dependence, but it reaches a scenario's pass/fail verdict directly, not only file ownership.

   Swallowing `adb unroot`'s own failure inside the same best-effort wrapper is what would let this
   happen invisibly. The restore is *verified*, not just attempted. After the second `adb
   wait-for-device`, `adb shell id -u` is re-read. An answer still `0` is logged loudly. A diagnostic capture must never be free to silently change how every later scenario on that device actuates. This failure is therefore not swallowed the way the initial pull's own failures are. Every scenario after this
   one then gets a resident server, and an `adbd`, as fresh as it would have anyway.

   **Why `crawl` does not use the pull.** The justification that "nothing after this point needs
   either channel" is specific to `run`'s per-scenario lease. There, the environment is torn down and
   rebuilt fresh on every lease regardless (`pool.py`). `crawl` does not fit that justification.

   *Extending `crawl`'s own crash recording* threads `env.app_crash_artifacts` into the crawl loop.
   That is the `logcat` layer only. `crawl` never calls `app_crash_tombstone()` at all. But a crawl
   lane holds *one* environment for its entire walk (`_build_lane`,
   [`cli.py:294-300`](../../bajutsu/crawl/cli.py)). The walk has not ended when a crash lands
   mid-frontier (`current_fp = None; continue`,
   [`_functions.py:668-669`](../../bajutsu/crawl/core/_functions.py)). A tombstone pull on the first
   confirmed Android crash would break every later query on that same lane. Nothing is left
   to rebuild the resident server or reverse tunnel until the crawl itself ends.

   `crawl`'s own Android capture is `logcat`-only by construction, simply by never being
   wired to `app_crash_tombstone()`. An earlier draft of this item threaded a `run`-only crawl-lane
   flag through `environment_for`. That flag is no longer needed for this purpose. Gating by which
   method `crawl` calls is enough on its own.

### Wiring the capture into a failed scenario's run directory

The reactive check runs in-band, inside the same step loop that every other terminal failure already
goes through. `run_scenario`'s ordinary `RunResult` assembly runs unchanged after it. The
following land as they would for an `ElementNotFound` failing the same step:

- the failing step's own screenshot
- the steps already completed
- any scenario-level `after: on: error` rule

The video recording that is already running for the whole scenario stops and attaches the same way
too.

`AppCrashedError` never escapes `run_scenario` as a raised exception. `pipeline.py`'s existing
crash-retry loop only triggers on an escaping `BackendCrashError`, so it never sees the error. The
scenario fails once, the same way any other terminal step failure already does. No special-casing is
needed to keep it from retrying.

The evidence *capture* stays in-band, inside `_finish_outcome` (see *Detecting the event*). The
evidence *copy* to disk stays outside it, for the same reason that BE-0421's own copy does.
`_step_runner`'s sink
([`bajutsu/common/orchestrator/loop/_loop_config.py`](../../bajutsu/common/orchestrator/loop/_loop_config.py))
is an `EvidenceSink`. Its whole surface is `capture`, `wait_diagnostic`, and the interval start/finish
pair, with no arbitrary named write. It also scoped to the running scenario. The crash evidence
needs the run-scoped `RunArtifactWriter` and the `sid` that `pipeline.py` already holds.

`Lease` ([`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)) gains two matching
callables:

- `app_crash_artifacts: Callable[[], list[tuple[str, bytes]]]`
- `app_crash_tombstone: Callable[[], list[tuple[str, bytes]]]`

Each defaults through its own module-level no-op, the same way `crash_artifacts` already does. Both
are wired in `pool.py`'s `lease()` closure alongside it, and read the environment's two methods
directly.

Only the first callable travels any further than `pipeline.py` itself. `_run_on_lease`'s own
post-return scan calls `lz.app_crash_tombstone` directly (see below). It is not threaded into the step
loop at all. Firing it there is exactly the timing that this item's Android section rules out.

The first callable reaches the step loop the same way `relaunch` already does. `pipeline.py:898`
passes `relaunch=lz.relaunch` into `run_scenario`, which threads it into `_LoopConfig.relaunch`. This item adds a matching `capture_app_crash: Callable[[], list[tuple[str, bytes]]] | None` field. It is threaded from `lz.app_crash_artifacts` the same way. `_finish_outcome` calls it, not `pipeline.py`.

An earlier draft of this item had `pipeline.py` call `lz.app_crash_artifacts()` itself. The call came from a post-return scan, once `run_scenario` had already returned. That draft reads `app_launched_at` live at
a point that the scenario's own `after` phase has already had the chance to move past. The `after`
phase runs *inside* `run_scenario`, before it returns. *iOS: matching the `.ips` report* describes the
teardown-`relaunch` failure that this produces.

Calling the callable inside `_finish_outcome` instead closes that gap. The sweep runs at the exact moment the crash is confirmed. That is strictly before any later step in the same scenario can touch the marker, teardown included. Its result travels on the `StepOutcome` rather than being re-read later.

`pipeline.py`'s `_run_on_lease` reads `result` for a crashed outcome right after `run_scenario`
returns. It still holds the same lease it leased, before its own `finally` ever releases it. It scans
`(*result.before_outcomes, *result.steps, *result.after_outcomes)` for `app_crashed`. It does not read
`result.steps[-1]`, for two reasons:

- A nested crash settles its wrapping `if` / `forEach` outcomes *after* the crashed one
  (`_step_runner.py:220`, `:238`), so the crashed outcome is not always last.
- `result.steps` alone omits the `before` and `after` phases entirely. `RunResult.steps` is the main
  phase's own list (`_functions.py:851`). It is `[]` outright when a `before` step fails
  (`_functions.py:732-740` skips the main steps in that case).

When the scan finds one, a new `_write_app_crash_artifacts(lz, outcome, s, sid)` does the write. It
mirrors `_write_crash_artifacts` (BE-0421, `pipeline.py:803`) almost exactly. It takes one extra parameter that its sibling does not need. The sibling reads every artifact off `lz.crash_artifacts()` directly.
The new function starts from the found `outcome`'s own `app_crash_artifacts`. Confirmation time already captured them, so they are not re-swept here. The scan's own found outcome has
to reach it as an argument, not stay implicit.

The function then extends that list unconditionally with one more call, `lz.app_crash_tombstone()`. The call is made right here for the first time. This is the one piece of evidence that this item does *not* capture inside `_finish_outcome`. *Android: `logcat`'s crash buffer first, a root-gated tombstone pull second* gives the reason. The pull needs `adb root`, and firing that mid-scenario risks
losing the whole `RunResult` to an escaping `BackendCrashError`.

Calling it here instead is genuinely safe. `run_scenario` has already returned. Nothing later in the same scenario can still be actuating through the resident server that this call is about to restart.

`_run_on_lease` holds a `Lease`, not a backend identity. The call stays unconditional
rather than branching on `result.backend == "adb"`. Branching would put per-backend knowledge into
the deterministic core, which the "a platform is a backend" seam ([`CLAUDE.md`](../../CLAUDE.md))
keeps out. The unconditional call is also one fewer site to edit the day a second backend grows a
tombstone equivalent.

The combined list is what gets written. It holds the `logcat` entries first and the tombstone entry
appended after. It is empty on iOS, web, and fake, since their `app_crash_tombstone()` is a no-op.

- Each `(name, content)` pair is written through
  `writer.write_text(f"{sid}/app-crash/{name}", content.decode(errors="replace"))`. This is the
  redacting text path, not `write_bytes`. BE-0421's own copy uses it for the same reason: a crash
  report is text that a crashing app can echo a secret into.
- A trailer naming the directory is appended to `result.failure`. This is the same shape that
  `_write_crash_artifacts` returns for `pipeline.py` to append.

A write problem, or a failure inside `app_crash_tombstone()` itself, is logged and never raised. This
matches the same posture as BE-0421's copy. A diagnostic capture must never turn an already-decided
failure into a different one. A lost tombstone must never cost the `logcat` layer already on the
outcome.

This is a plain post-return check, not a new `except` branch. The scenario's own retry behavior above
is already settled by the time the check runs.

`StepOutcome.app_crash_artifacts` lives on the outcome, not only copied to disk above. That placement
gives it a second consumer that this item has to account for. `manifest_dict`'s `_scenario_dict(r)`
([`bajutsu/common/report/manifest.py`](../../bajutsu/common/report/manifest.py)) is a plain
`asdict(r)`. Every `StepOutcome` reachable through `steps` / `before_outcomes` / `after_outcomes`
lands in the manifest dict verbatim. The crashed one is included. `write_json`'s `json.dumps`
carries no `default=` ([`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py)).

Raw `bytes` has no JSON encoding. The first app-crash scenario would raise `TypeError`
writing `manifest.json`. That happens after the crash was correctly classified, and it takes the
whole run's manifest and HTML report down with it.

This item already makes the opposite call correctly for `crawl`'s own `Crash.artifacts` (*Extending
`crawl`'s own crash recording*, below). It deliberately leaves them out of `serialize.py`'s round trip
for the same reason. `_scenario_dict` needs the matching exclusion for `run`'s half of the same
hazard. It already pops `wall_offset_s`. Alongside that pop
([`bajutsu/common/report/manifest.py`](../../bajutsu/common/report/manifest.py)), it walks each outcome
dict in `steps` / `before_outcomes` / `after_outcomes` and pops `app_crash_artifacts` from it too.
`wall_offset_s` is a top-level `RunResult` field that a single pop reaches. `app_crash_artifacts` is
nested inside every `StepOutcome` that the three lists carry, so the exclusion has to walk them.

`report/load.py`'s inverse needs no matching change. `_step`'s `_kw(StepOutcome, d)` already
reconstructs any field missing from `d` at its dataclass default. It does the same for
`wall_offset_s` on `RunResult`. A stripped `app_crash_artifacts` simply reconstructs as `()`, exactly
like a normal step's own already-empty default.

The crash is still fully reported. `outcome.reason` and `outcome.app_crashed` (a plain `bool`, with no
serialization hazard) still round-trip. The redacted copy under `{sid}/app-crash/` that this section
already writes is the durable copy that a report reader follows. The in-memory bytes on the outcome
exist only to reach that write, never to reach the manifest.

`_kw`'s own docstring-comment at [`load.py:34-41`](../../bajutsu/common/report/load.py) currently
names `wall_offset_s` as "the one deliberate exception" that the round-trip guarantee does not cover.
This item's own second exclusion makes that claim stale the moment it lands. The comment exists
specifically to warn a future contributor away from "fixing" an exclusion that looks like an
oversight.

Left unstated, the same risk that the comment was written to prevent recurs on `app_crash_artifacts`
itself. Suppose a contributor notices that the field does not survive the round trip, and reads "the
*one* deliberate exception". If that contributor removes the `_scenario_dict` pop as a bug fix, the
`manifest.json` `TypeError` that this section exists to avoid returns. `test_round_trip_through_manifest_is_lossless`'s own fixture leaves the field at its default. That test would not catch the regression either.
Naming `app_crash_artifacts` alongside `wall_offset_s` in that same comment is part of this item's own
scope, not a follow-up.

### Extending `crawl`'s own crash recording

`crawl()` ([`bajutsu/crawl/core/_functions.py`](../../bajutsu/crawl/core/_functions.py)) itself holds
no environment. It takes only injected callables: `driver`, `reset`, `is_alive`, `recover`,
`on_event`, and the rest. That is what keeps the crawl core off `platform_lifecycle` and lets the same
loop drive every backend.

An earlier draft of this item proposed reaching into `crawl()` for an environment reference. That
would give every backend and every fake in the fast test suite one to grow. It would also have no
signal to key the reference off, because `is_alive` gives none. Both `ios.py` and `android_environment.py` answer `crawl_aliveness()` with `None`. Their own comment says "the engine reads the accessibility tree for device crash detection". `crawl()` runs with no `is_alive`
callback at all on either backend that this item covers.

A single `CrawlEnvironment` is in scope in [`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py)
(`plan.environment`). But it is built with an empty `udid` (`environment_for(actuator, "")`) purely to
wire the health seams above. It is not the device that the crash actually happened on.

A crawl instead runs one environment per lane. `_build_lane` builds it alongside that lane's own
`driver` / `reset`, one per `--udid` (BE-0064). Reaching for `plan.environment` from a multi-lane
crawl would capture the wrong device's diagnostics, or none.

The capture is threaded the same way that `driver` and `reset` already are. `_build_lane`
gains a third return value, that lane's own `env.app_crash_artifacts`. It is carried alongside the lane's driver and reset. For every extra lane it travels through `WorkerFactory` (`bajutsu/crawl/core/_functions.py`). For the first lane it travels through `crawl()`'s own primary-lane parameters.

`record_crash`'s call site (`bajutsu/crawl/core/_functions.py:667`) already computes `crashed = not alive(d, landed)`. It does so off the coordinator's lock. Its own comment calls this "pure
deterministic reads, off-lock". The computation runs right before the call to `coord.record_crash(path)`.

`alive` falls back to the UI-tree heuristic `is_app_alive` on both backends that this item covers.
This item's own Introduction already names the risk of that heuristic. A system alert or a deliberate `background` step reads as a false positive.

`run`'s own bounded `.ips` / tombstone poll earns its wait. `app.state == notRunning`, or a `pidof`-plus-exit-info check, confirmed the event first. `crawl` has no such confirmation. A false
positive here would poll to its full timeout on every occurrence. That is the common case, not the
rare one. `crawl` does not stop after recording a crash (`current_fp = None; continue` right after
`record_crash`, `_functions.py:668-669`). It can walk into the same false positive repeatedly in one
run.

This item's capture call joins the existing check in the same off-lock window. It calls
that worker's own lane-scoped `env.app_crash_artifacts()` only when the driver can positively confirm
the event first. The confirmation is `isinstance(d, base.AppCrashSignal)` and a non-`None`
`d.app_crash_signal()`. That is the same capability that both backends already implement for `run`.

`crawl`'s own detection is unchanged either way. A `Crash` is still recorded on the UI-tree heuristic
alone. Only the artifact sweep is gated. An unconfirmed crash records a `Crash` with no `artifacts`,
rather than paying a full-timeout poll on what might be a covering alert.

That `d.app_crash_signal()` call is wrapped in its own
`except (base.BackendCrashError, OSError, subprocess.CalledProcessError): pass`. The wrapper treats a
channel error the same as an unconfirmed answer. `crawl` records the `Crash` and keeps walking, with
no `artifacts`.

The wider catch here is a second, caller-side guard. It sits alongside the fix to `app_crash_signal()` itself in *Android: `logcat`'s crash buffer first...*. It does not substitute for that fix. `AdbDriver`'s
`RunFn` is `subprocess.run(..., check=True)`
([`adb/_functions.py:111`](../../bajutsu/common/backend_cli/adb/_functions.py)). A bare
`except base.BackendCrashError` alone would still miss a `pidof` / `exit-info` failure that
reached this call site unswallowed. Nothing under `backend_cli/adb/` raises `BackendCrashError` at
all.

*iOS: `app.state`* deliberately leaves a channel error unswallowed for `run`. The error propagates as
`XcuitestRunnerCrashError` into the recovery path that `pipeline.py` already owns. `crawl` has no such
path. Nothing under `bajutsu/crawl/` handles `BackendCrashError`.

This call also sits outside `_walk`'s own `try` (`_functions.py:597-655`, scoped to `action.perform` /
`_observe`). It sits in the "pure deterministic reads, off-lock" window. That window was pure Python before this item added the first raising round-trip to it.

Left unswallowed here, the error would take this path:

1. It propagates through `_run`'s `except Exception: coord.note_failure(exc)`.
2. It is re-raised on the main thread after join.
3. It reaches `cli.py`'s bare `_execute(...)` then `_finish(...)`, with no `try` / `finally` between
   them.

`_finish` would never run. `write_repros` would never walk `screen_map.crashes`. That discards every repro and every artifact that this item buffered for the *entire* walk. It does so on exactly the failure that this item exists to capture evidence for.

Capturing here matters for concurrency, not only correctness. `record_crash` holds the coordinator's
`self._cond` for its whole body. That same lock also serializes `on_event` (`_coordinator.py`'s
`_emit`) and every other worker's own `record_crash` / `record_edge` calls. A multi-second `.ips` poll
or tombstone pull run *inside* that lock would stall every other crawl lane for its duration. Run
first and handed in already resolved, the capture costs the lock nothing beyond an ordinary list
append.

`Crash` ([`bajutsu/crawl/core/crash.py`](../../bajutsu/crawl/core/crash.py)) gains an
`artifacts: tuple[tuple[str, bytes], ...] = ()` field. `record_crash` takes and stores it alongside
`path`.

[`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py)'s `write_repros` already walks
`screen_map.crashes` in order to write each one's `crashes/crash-NNN.yaml` repro. `NNN` is that
crash's own zero-padded, one-based index in the list (`enumerate(screen_map.crashes, start=1)`,
`repro.py:154`). `cli.py`'s `_finish` only calls `write_repros` and echoes a count.

The `write_repros` loop owns the numbering, so it gains the artifact-writing step. Having `_finish` re-derive the same index in a second file would duplicate logic. The two copies would drift silently if the numbering ever changed in one place and not the other.

`write_repros` also `continue`s past a crash whose path cannot be faithfully replayed, and writes no
`.yaml` for it. A `tap_point` action, for example, has no selector to address. The artifact write sits
*before* that `continue`. A non-replayable crash is the kind whose platform report is worth the most,
since there is no repro to run instead. It does not lose its artifacts along with its repro.

The step writes any non-empty `artifacts` under `crashes/crash-NNN/app-crash/`. It uses the same redacting `writer.write_text(…, content.decode(errors="replace"))` path that `run`'s own copy uses. `write_repros` already uses that path for the repro `.yaml` beside it. The directory is a sibling of
the repro file, not a same-named top-level directory. The two are found together and sort
together.

A crawl's own detection stays the UI-tree heuristic that it already uses. `crawl` has no scenario step
to hang a reactive check off, unlike `run`. The two entry points share the capture on iOS. On Android
they share it for the `logcat` layer only. The reason is that the root-gated tombstone pull is `run`-only (see
*Android: `logcat`'s crash buffer...*). The two entry points share the detection on neither backend.

`Crash` is also the type that [`serialize.py`](../../bajutsu/crawl/serialize.py) round-trips through
JSON. `screenmap_dict` dumps `screen_map.crashes` (`:131-134`), and `screenmap_from_dict` rebuilds it
(`:92-98`). Raw `bytes` has no JSON encoding. `artifacts` is deliberately left out of both
directions, rather than base64-widening every other field's dump.

`artifacts` is already an in-memory-only field on its own terms. `on_event`'s own `_write_screenmap`
call (`cli.py:186`) fires that dump live. It fires after every recorded crash, well before `write_repros` ever
runs. `write_repros` walks the finished `screen_map.crashes` exactly once, at the very end of a
normally-completed crawl (`cli.py:405`). Nothing durably persists the field before that one closing
call, dump or no dump.

Omitting it from the dump costs a `--resume` / `--continue-crawl` reload nothing, in both cases:

- **A normally-completed prior crawl.** It already wrote the bytes to disk. `write_repros` ran, so the
  bytes are under that prior run's own `crashes/crash-NNN/app-crash/`.
- **An interrupted prior crawl.** It could not have kept the bytes either way. The process died
  before `write_repros` reached them, so they never reached disk in the first place. A `--resume`
  already accepts the same loss for that prior crawl's own unwritten report.

`screenmap_from_dict` rebuilds every carried-forward `Crash` with `artifacts=()`, the dataclass
default. It does exactly what it already does for `actions` on a map saved before crashes carried that
field.

### Proving the capture on a real crash, not only a stubbed one

A unit test can stub `~/Library/Logs/DiagnosticReports`, or a fake `logcat` / tombstone pull. Neither
proves that the underlying platform mechanism this item depends on still behaves the way the design above assumes. Only a real Simulator or emulator can show that. That mechanism is `ReportCrash`'s own `.ips` write, or
`logcat`'s `crash` buffer.

The showcase apps ([`demos/showcase/`](../../demos/showcase)) gain a "force a crash"
affordance. A launch-env flag gates it, not a debug build.

- There is no `#if DEBUG` anywhere in the showcase iOS sources.
- The closest existing precedent is
  [`ConformanceView.swift`](../../demos/showcase/ios/swiftui/Sources/ConformanceView.swift). It is
  itself reached only when the `SHOWCASE_CONFORMANCE` launch env is set
  ([`AppModel.swift:92`](../../demos/showcase/ios/swiftui/Sources/AppModel.swift),
  [`RootView.swift:7`](../../demos/showcase/ios/swiftui/Sources/RootView.swift)). It is BE-0114's
  driver-conformance screen, not on-device diagnostics.

Gating behind a build configuration instead would put the affordance's very existence at the mercy of one thing. That thing is the configuration that the iOS lane's own `build (app + runner)` job compiles. A Release build would
compile it out. The new expected-to-fail scenario would then fail on a missing selector rather than on
a crash. That is the exact misdiagnosis that this item exists to remove.

A launch-env flag needs no build-configuration assumption. It rides the scenario schema's own
`preconditions.launchEnv`, the same seam that `SHOWCASE_CONFORMANCE` already rides. The affordance
calls `fatalError()` on iOS and throws an uncaught exception on Android's main thread. One new
scenario per platform taps it.

Both new scenarios are *expected* to fail. The CI wrapper around them asserts two things. The failure carries the new app-crash classification, and `app-crash/` holds the expected file. The
`fault-injection (xcuitest)` job already asserts a diagnosed failure shape rather than a green run in
the same way ([`docs/ci.md`](../../docs/ci.md#the-ios-lane)).

The scenarios land as a non-gating per-PR signal in `ios-e2e.yml` / `android-e2e.yml`, next to
`actuation (xcuitest)` and `golden (adb)`. Both of those are deliberately excluded from their lanes'
required aggregators. Newly wired on-device coverage earns its stability there first, before any
promotion into the required `E2E` check. Every other new signal in those lanes has taken the same
path.

### Cost on the web backend and the fake backend

These are two separate seams, and neither backend needs meaningful work on either half.

- **The driver seam.** `PlaywrightDriver` does not implement `AppCrashSignal`. The reactive check's
  `isinstance` probe answers `False` and skips it, the same way it would for any other
  driver that never declares the protocol.
- **The environment seam.** `WebEnvironment`
  ([`bajutsu/common/platform_lifecycle/environments/web.py`](../../bajutsu/common/platform_lifecycle/environments/web.py))
  declares its own `app_crash_artifacts()` and `app_crash_tombstone()`, both returning `[]`. These are
  two one-line additions alongside the identical declaration that it already carries for
  `take_crash_snapshot()`. `RunEnvironment` is a structural protocol that no concrete class
  subclasses. It has no inheritable default that any of the three methods could fall
  through to.

`FakeEnvironment`
([`bajutsu/common/platform_lifecycle/environments/fake.py`](../../bajutsu/common/platform_lifecycle/environments/fake.py))
inherits the same no-op from `_DeviceEnvironment`. The fake test driver behaves the same way on the
`AppCrashSignal` seam. Nothing in this item changes what a web or fake-backend run captures.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Retry through the existing `BackendCrashError` recovery loop | Rejected during scoping: this event is a likely defect in the app itself, not a transient infrastructure blip. Respawning and retrying would spend the crash-recovery budget on a scenario likely to fail again, and risks absorbing a real regression as flakiness (BE-0049) instead of failing loudly. |
| Poll `app.state` / process liveness proactively, before every step | Rejected during scoping: it adds one driver round-trip to every step of every scenario, green runs included, to catch a failure mode that is rare by construction. The reactive design still catches the event at the exact step it happened, since that step's own action or query is already failing. |
| Raise `AppCrashedError` out of `run_scenario`, with a dedicated `except` branch in `pipeline.py` mirroring `BackendCrashError`'s | Rejected on review. An app crash leaves the driver, the backend process, and the running video recording all intact, unlike a backend crash — only the app is gone. A side-channel exception would build its own terminal `RunResult` from scratch, discarding the steps, artifacts, and `after: on: error` dispatch `run_scenario` already produces for every other terminal failure. |
| Add `app_crash_signal()` as a required member of the `Driver` protocol | Rejected on review. `Driver` is `@runtime_checkable`, so every implementer would need a stub: `XcuitestDriver`, `AdbDriver`, `PlaywrightDriver`, `XcuitestLiveDriver`, the fake backend, and narrower wrappers like `WebContextDriver`, including backends this item never intends to check. A narrow, opt-in capability protocol, the shape this codebase already uses for `InterruptionPolicyTarget` and `SettledReadProvider`, reaches only the two backends that need it. |
| Android: `logcat`'s crash buffer only, no tombstone pull | Rejected: it loses the native (NDK) crash's full backtrace, keeping only the abridged summary `logcat` itself prints. Kept as the always-available baseline; the tombstone pull layers richer detail on top where the device allows it, rather than replacing it. |
| Android: a rooted tombstone pull only, no `logcat` fallback | Rejected: a real device, a user build, or an emulator image that refuses `adb root` would then capture nothing at all. `logcat`'s crash buffer needs no elevated access and already carries a complete stack trace for the common managed-code case. |
| A new scenario assertion (for example, `assert: appCrashed: false`) | Rejected: the event already ends the scenario through the step's own action/query failure. There is no later point in the scenario where an assertion could still run to check for it. The showcase's own test scenario instead asserts the failure's *shape* from outside the run, the way `fault-injection (xcuitest)` already does. |
| Gate the capture behind an opt-in `capturePolicy` rule, matching `video` / `deviceLog` | Rejected for the reason BE-0421 gave for its own artifact: the capture runs once, only on a scenario already ending in failure. Its cost is one bounded sweep or log read, not a standing per-step overhead worth gating behind an explicit ask. |
| Freeze the match criteria and defer the sweep with a `take_crash_snapshot()`-style thunk, the way BE-0421 defers a backend crash's own capture | Rejected: BE-0421's thunk exists to survive a concurrent worker reusing the same warm, pooled environment before the pool releases the lease. This item's own race is narrower and same-scenario — a teardown `relaunch` in the same scenario's `after` phase can re-stamp `app_launched_at` before `run_scenario` returns — and closing it needs the opposite move: calling `app_crash_artifacts()` *earlier*, synchronously inside `_finish_outcome` at confirmation, not deferring it past a point the marker could already have moved. |
| Android: capture the tombstone layer at confirmation time too, the same call as `logcat`, instead of splitting it into a second `app_crash_tombstone()` method | Rejected: an earlier draft of this item did exactly this, and it regresses the very race the confirmation-time move above exists to close. `adb root` restarts `adbd`, killing the resident server's `am instrument -w` session mid-scenario; any wrapping `if`/`forEach` outcome, `expect`, or `after` rule still to come in that same scenario then raises `BackendCrashError` against a dead channel, which `run_scenario` deliberately re-raises rather than folds into a failure — discarding the whole `RunResult`, `app_crashed` and the `logcat` layer already captured included. The tombstone pull's own risk is asymmetric in a way `logcat`'s is not: a teardown `relaunch` only moves `app_launched_at` *forward*, so reading it from `pipeline.py`'s genuine post-return scan risks a miss, never the wrong-attribution risk that drove `logcat`/`.ips` earlier in the first place — so only the tombstone layer benefits from staying on the original post-return timing. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `base.AppCrashedError` (new file); the `base.AppCrashSignal` capability protocol
      (`app_crash_signal() -> str | None`), separate from the `Driver` protocol; the new
      `StepOutcome.app_crashed: bool = False` field. `ruff`'s `TRY` family is selected with only
      `TRY003` ignored, so Unit 7's raise/catch (below) needs its own `# noqa: TRY301` with a
      suppression reason, as `CLAUDE.md`'s inline-comment rule requires — named here since it
      decides `AppCrashedError` stays a real, raised type rather than a plain string built from
      `signal` inline.
- [x] Unit 2 — iOS: a new `openapi.yaml` route and generated `APIHandler` method reading
      `XCUIApplication.state`, served through `RunnerServer` (not `Router.swift`);
      `XcuitestDriver.app_crash_signal()` implementing `AppCrashSignal`, classifying `notRunning` as
      the signal and letting a channel error propagate as `XcuitestRunnerCrashError`; a new
      `is_real_device` constructor argument, threaded from `make_driver` the same way `device_os`
      already is, that makes `app_crash_signal()` answer `None` outright on `deviceType: device` —
      this item scopes to the Simulator only.
- [x] Unit 3 — iOS: `XcuitestEnvironment.app_launched_at`, recorded in `_spawn_cold` immediately
      *before* the `xcodebuild` spawn — the cold launch is performed by the runner itself
      (`XCUIApplication.launch()`, `xcuitest_environment.py:323`), not by this environment, so there
      is no Python-side launch call to record beside, and a marker stamped once
      `_spawn_cold_with_retry` has returned would already be later than the launch it names and
      would reject the `.ips` of an app that crashed during that very launch; a new
      `XcuitestEnvironment.relauncher()` override wrapping `device_relauncher`'s `RelaunchFn` to
      record it again *before* a `relaunch` step's own launch, the one call site `_DeviceEnvironment`'s
      inherited `relauncher()` has no environment in scope to update; a matching
      `XcuitestEnvironment.crawl_reset()` override recording it a third time *before* `crawl`'s own
      per-frontier-revisit relaunch, the one other call site `_DeviceEnvironment`'s inherited
      `crawl_reset()` also has no environment in scope to update; a fourth record inside
      `_resume_warm` itself (BE-0291's cross-lease warm-reuse launch,
      `xcuitest_environment.py:855-856`, on the same long-lived `XcuitestEnvironment` `start()`
      returns through) immediately *before* its own `e.launch`, since a warm-reused lease's crash would otherwise
      match whichever earlier lease's `.ips` report happened to share the marker's stale timestamp;
      a new `self._app_path` field, stashed at `start()` next to `self._bundle_id` (`ios.app_path`,
      the same `ios` `self._bundle_id` already reads from) — `app_crash_artifacts()` takes no
      arguments, so it has no other way to reach it; reading `CFBundleExecutable`
      from `Path(self._app_path) / "Info.plist"` for the sweep's own match pattern (`ios.bundle_id` is
      not this name); `app_crash_artifacts()`'s name-and-`udid`-matched `.ips` sweep, reading and
      parsing each candidate report's own *payload* for the udid rather than its path or its header
      (no udid appears in a report's filename or its header, and no PID accessor exists on
      `XCUIApplication`), with a bounded wait
      for `ReportCrash`'s asynchronous write,
      wrapped so any failure resolves to `[]`.
- [x] Unit 4 — Android: a `package` keyword threaded through `backends.make_driver` into
      `AdbDriver.__init__`, the same way `fetch_clock` and `act` already are; an `api_level: int |
      None = None` keyword threaded the same way, read once via `adb shell getprop
      ro.build.version.sdk` in `AndroidEnvironment.start()` and stashed alongside `self._package`,
      since nothing in this codebase tracks it today; `app_crash_signal()` checking
      `package is None`, an unset/`None`-answering `launched_at`, or `api_level is None or api_level <
      30` first, resolving to `None`
      immediately — `dumpsys activity exit-info` with no package reports every package on the
      device, so a silent `None` default would confirm another process's crash, and
      `ApplicationExitInfo` itself does not exist before API 30, so polling for it on an older device
      or emulator image would time out on every crash instead of failing closed the same up-front way;
      both the `pidof` and `dumpsys activity exit-info` calls wrapped in `except
      (subprocess.CalledProcessError, OSError)`, the same shape as `AdbDriver._rooted()`'s existing
      guard around `adb shell id -u` (`adb_driver.py:1234-1235`) — `real_run`'s default `RunFn` is
      `check=True`, and toybox `pidof` exits 1 on no match rather than returning empty stdout, so an
      unguarded call would raise on the routine, expected case this probe exists to observe; a
      `pidof` exit with empty `stdout` reads as "no process", any other failure resolves to `None`;
      a `launched_at: Callable[[], tuple[float, str] | None] | None = None`
      injected callable
      reading `AndroidEnvironment`'s launch marker live — both the epoch and the exit-info rendering
      Unit 5's combined read produces, not the epoch alone, since the exit-info comparison below needs
      the rendering and a `float` cannot carry it; `AdbDriver.app_crash_signal()` via `adb shell
      pidof <package>` corroborated by a time-bound `adb shell dumpsys activity exit-info <package>`
      check (its newest entry only, its `timestamp=` field — a device-timezone wall-clock rendering,
      never an epoch — compared against the rendering half of `launched_at()`,
      never the epoch half: resolving
      `timestamp=` into an epoch on the host would run the conversion through the host's own
      timezone, reintroducing the clock reconciliation this design otherwise avoids), polled the same
      short, bounded way
      as the iOS `.ips` sweep rather than read once, since `ApplicationExitInfo` is recorded only
      after `system_server` reaps the death — but only the scenario's first such poll pays the full
      bound: a new `self._exit_info_exhausted: bool = False` instance field (the same shape as
      `AdbDriver`'s existing `_act_warned` / `_act_unavailable` latches), set once a bounded poll ends
      without a match, makes every later probe read the history once and answer
      immediately rather than re-polling. `run` gets this reset for free — a fresh `AdbDriver` is built
      per lease there, since `AndroidEnvironment.has_reusable_resident()` answers `False`
      unconditionally (BE-0291's cross-lease reuse is XCUITest-only) — but `crawl` builds one
      `AdbDriver` for the whole walk (`crawl/cli.py:283-300`), so this needs its own reset, not a fold
      into `invalidate_settled_cache()`: that method's contract is "the screen changed", and `AdbDriver`
      already calls it from every ordinary actuator (`_act`, `_device_act`, `type_text`), so folding
      this in would clear the flag before nearly every probe it exists to bound. A second, narrow method
      — `reset_exit_info_poll()`, the same shape as `SettledCacheInvalidator` — is called instead from
      `AndroidEnvironment.relauncher()`'s `relaunch()` closure right after `e.launch(...)`
      (`android_environment.py:339-351`) and `crawl_reset()`'s `reset()` closure right after its own
      `e.launch(...)`, the two genuinely-fresh-launch moments a poll bound becomes worth paying again,
      never from inside `AdbDriver`'s own actuators.
- [x] Unit 5 — Android: `AndroidEnvironment.app_launched_at`, recorded immediately *before* each launch
      site's `e.launch(...)` call, never after — `e.launch` is `am start -W`, which waits for the
      launch to complete, so a marker stamped once it returns has already missed a startup crash — from
      **one**
      combined `adb shell "date '+%s|%Y-%m-%d %H:%M:%S|%m-%d %H:%M:%S.000'"` read — the format quoted
      for the *device* shell, which would otherwise read the `|` as a pipe and word-split the rest —
      whose three
      pipe-separated fields are split on the host (a plain string split, not a timezone resolution) —
      the epoch (`app_launched_at` itself), a rendering for the exit-info poll (Unit 4), and a
      rendering for `logcat -t`, stashed together rather than taken from three separate `date`
      invocations, which would stamp three different instants and risk the `logcat` field (were it
      read last) filtering out a crash that landed in the gap between reads; `logcat -t` reads an
      integer argument as a
      line count, not a time bound, so the epoch value cannot be threaded straight through, and
      `dumpsys activity exit-info`'s own `timestamp=` is a different rendering still (device-timezone,
      carrying a year `logcat`'s format omits), so neither substitutes for the other; a
      new `self._package` field, stashed in `start()` alongside both (`android.package`, from the same
      `android = require_android(eff)` the launch marker already reads) — `app_crash_artifacts()`
      takes no arguments, so this is the only way it reaches the value its own `logcat` process bound
      needs;
      `app_crash_artifacts()`'s always-attempted `logcat` extraction (managed *and* native crash
      formats) using the `logcat`-formatted marker as a `-t` time filter rather than clearing the
      crash buffer, so
      `scripts/collect_android_diagnostics.sh`'s own end-of-job sweep still sees everything earlier —
      bounded to `self._package` (a managed block's `Process: <package>` line,
      a native block's `>>> <process> <<<` header), since the buffer is device-global across processes
      too, not only across launches — and, for the same reason as the exit-info poll, re-dumped the
      same short, bounded way rather than trusted on a single `-d` snapshot, since `crash_dump` writes
      a native block after the death `pidof` already reported;
      a separate `app_crash_tombstone()` method for the best-effort, root-gated tombstone pull,
      accepting that `adb root` kills the resident server's `am instrument -w` session and BE-0283's
      `adb reverse` tunnel outright — not re-establishing either, since nothing later in this lease
      needs them and the pool rebuilds both fresh on the next lease regardless — kept out of
      `app_crash_artifacts()` and off the `_finish_outcome` call site entirely, since firing `adb root`
      mid-scenario risks the escaping `BackendCrashError` *Android: `logcat`'s crash buffer first, a
      root-gated tombstone pull second* describes; an `adb unroot` (and a second `adb wait-for-device`)
      once the pull finishes, inside the same best-effort wrapper, so `adb root`'s device-wide
      privilege level — which nothing else in this repository restores — never outlives this one
      lease and leaks into a later, unrelated scenario's own `install`/`pm clear`/`force_stop`/
      `launch` calls; `crawl` needs no constructor-time flag to keep this
      layer off its own long-lived lane, since it simply never calls `app_crash_tombstone()` (Unit 10);
      each layer independently wrapped so any failure resolves to `[]`.
- [x] Unit 6 — `RunEnvironment.app_crash_artifacts()` and `RunEnvironment.app_crash_tombstone()`
      protocol shapes (each returning `list[tuple[str, bytes]]`); a one-line `app_crash_artifacts()`
      `return []` on `WebEnvironment` and `_DeviceEnvironment` (inherited by `FakeEnvironment`), and a
      one-line `app_crash_tombstone()` `return []` on those two — the same no-op shape
      `take_crash_snapshot()` already declares on `WebEnvironment`, `AndroidEnvironment`, and
      `_DeviceEnvironment`. Neither method gets a no-op on `AndroidEnvironment`, which overrides both
      `app_crash_artifacts()` (Unit 5's `logcat` layer) and `app_crash_tombstone()` (Unit 5's tombstone
      layer) with real captures; `app_crash_artifacts()` gets none on `XcuitestEnvironment` either,
      which overrides it alone with its own real capture (Unit 3) and inherits `_DeviceEnvironment`'s
      `app_crash_tombstone()` no-op; `Lease.app_crash_artifacts` and `Lease.app_crash_tombstone`
      both wired through `pool.py`'s `lease()` closure alongside `crash_artifacts`. Only
      `Lease.app_crash_artifacts` threads any further, into `_LoopConfig.capture_app_crash` the same way
      `Lease.relaunch` already threads into `relaunch` (`pipeline.py:898`) — called from inside the step
      loop, not from `pipeline.py`, so the sweep runs before a same-scenario teardown step can move
      `app_launched_at` (see Unit 7 and *iOS: matching the `.ips` report*). `lz.app_crash_tombstone`
      itself is called directly by `pipeline.py` (Unit 8), never threaded into `_LoopConfig` at all.
- [x] Unit 7 — `run_scenario` / `_step_runner.py`: the new `_finish_outcome` helper, called at all
      five `self.state.outcomes.append(outcome)` call sites — `_handle_if` / `_handle_for_each` /
      `_handle_web` once each, `_handle_action` twice (its own end and its
      `UncoveredSystemAlertLocale` early return) — in place of the bare append, covering every step
      kind's true final outcome; a scenario-scoped object (`run_scenario` creates it once and shares
      it with every `run_phase` call the same way it already shares `live_bindings`, carried on
      `StepLoopState` next to `bindings`, not on the frozen `_LoopConfig`, so it survives `before`,
      the main steps, and every dispatched `after` rule) carrying three latches: a deliberate-termination flag, set the moment
      `outcome.action == "relaunch"` and `outcome.ok is False` (before any probe), that suppresses
      every later probe in the scenario — the failing `relaunch`'s own wrapping `if`/`forEach`
      outcomes and any `after: on: error` cleanup step included, not only the `relaunch` step's own
      outcome; an unconfirmed-launch flag seeded once at construction from a new `readiness:
      ReadinessResult | None = None` parameter (threaded from a new `Lease.readiness` field, set in
      the `Lease(...)` constructor, `pool.py:563`, alongside `sink=sink`, from the `readiness`
      `launch_driver` already returns earlier in the same closure, `pool.py:408`, and which today
      reaches only the `FileSink` first-wait diagnostic — a `Lease`-side copy is what's missing, not a
      first capture), true when `readiness is None or not readiness.ready or readiness.signal ==
      "count"` — the bare-count rung cannot tell the app from SpringBoard
      ([`readiness.py:151-152`](../../bajutsu/common/platform_lifecycle/readiness.py)), so a `ready`
      answer there is not evidence the app ever foregrounded either, and `count` is also the *ordinary*
      signal for any target declaring no `readyWhen` (`launch_driver` never passes `id_namespaces`, so
      `namespace` never fires on this path; this repository's own `showcase-*-noax` targets are exactly
      this shape and each carries a full scenario suite) — closing the gap
      where a scenario's first failing step has no earlier step to have observed the app running, so an
      app that never reached the foreground would otherwise read as a confirmed crash on that first
      probe, *without* the unconditional, rest-of-scenario suppression the deliberate-termination flag
      uses, which would silence every later probe for that whole class of targets: `_finish_outcome`
      instead clears this flag the moment it sees a settled outcome whose own success required the app
      to answer — keyed positively, not by excluding `if`/`forEach` alone: that wrapping outcome
      settles `ok=True` on an empty `else`/zero matched elements without the app behind the query ever
      answering, so a dead app's own SpringBoard-only tree would otherwise clear the flag for the exact
      reason it should not, and the same holds for a step kind that never reaches the app at all
      (`http`, `generate`, `totp`, `email`, `push`), and for `relaunch` specifically: dispatched through
      the ordinary `_handle_action` path, so by a bare "actuating step" rule it would clear the flag,
      but its closure discards `await_ready`'s `ReadinessResult` and `await_ready` never raises, so a
      settled `relaunch`'s `ok=True` says nothing about the app — this Unit excludes it explicitly and
      instead *re-sets* the flag on a settled `relaunch`, since it just put the app back into the same
      unconfirmed state — the
      positive observation only a genuinely successful actuating (other than `relaunch`)/`wait`/`assert` step supplies; this same Unit also updates `ReadinessResult`'s own docstring
      (`protocols/readiness_result.py:15-16`, `:27`), which currently reads "Pure diagnosis: it never
      enters a verdict (prime directive 1)" — true of every consumer before this flag, which only ever
      displayed the value on a wait-timeout diagnostic — to name this new use before the invariant goes
      stale; and a confirmed-crash latch, set the first time `_finish_outcome` raises and catches
      `AppCrashedError`, so a later outcome in the same propagation folds the known signal into its
      own `outcome.reason` without probing again — bounding `app_crash_signal()` calls for the
      relaunch, unconfirmed-launch, and confirmed-crash cases specifically, not for an ordinary
      failure, which still pays one probe per settling outcome; raising and catching `AppCrashedError` in that one place —
      carrying its own `# noqa: TRY301` with a suppression reason (Unit 1) — to fold
      its message into `outcome.reason` and set the new `StepOutcome.app_crashed` field and the
      confirmed-crash latch; in the same catch, when `self.cfg.capture_app_crash` is set, calling it
      synchronously and storing the result on a new `StepOutcome.app_crash_artifacts` field — at
      confirmation time, not later, so a subsequent teardown step in the same scenario cannot move
      `app_launched_at` out from under the sweep; a later outcome that only folds the already-latched
      signal into `reason` leaving its own `app_crashed` at the dataclass default and never calling
      `capture_app_crash` again, so a nested crash's wrapping `if`/`forEach` outcomes never compete
      with the one confirming outcome for `pipeline.py`'s later scan; a fast-suite test driving a failing step of every
      kind through the loop and asserting each settled outcome passed through `_finish_outcome` — a
      behavioural pin, not a source-text grep for `self.state.outcomes.append`.
- [x] Unit 8 — `pipeline.py`: `_run_on_lease` scanning
      `(*result.before_outcomes, *result.steps, *result.after_outcomes)` for an `app_crashed`
      outcome right after `run_scenario` returns, still holding the same lease, before its own
      `finally` releases it; the new `_write_app_crash_artifacts(lz, outcome, s, sid)` mirroring
      `_write_crash_artifacts` (BE-0421,
      `pipeline.py:803`), starting from the found outcome's own `app_crash_artifacts` (captured at
      confirmation time by Unit 7, not re-swept here) and unconditionally extending it with
      `lz.app_crash_tombstone()` — called here, genuinely post-return, for the first time, empty on
      every backend but Android since the other three declare it a no-op (Unit 6) — before
      writing the combined list through the redacting `writer.write_text` path under `{sid}/app-crash/`,
      appending a directory-naming trailer to `result.failure`; a failure inside
      `lz.app_crash_tombstone()` itself logged, never raised, so a lost tombstone never costs the
      `logcat` layer already on the outcome; `bajutsu/common/report/manifest.py`'s `_scenario_dict`
      popping `app_crash_artifacts` from every outcome dict in `steps` / `before_outcomes` /
      `after_outcomes`, alongside its existing `wall_offset_s` pop, so the raw `bytes` this unit adds
      to `StepOutcome` never reaches `json.dumps` — `report/load.py` needs no matching change to
      `_kw` itself, since its existing missing-field handling already reconstructs the field at its
      default; `load.py:34-41`'s own comment updated in the same change, naming `app_crash_artifacts`
      alongside `wall_offset_s` as a second deliberate round-trip exception, since it currently reads
      "the *one* deliberate exception" and would otherwise invite a later contributor to "fix" this
      item's own pop as an oversight.
- [x] Unit 9 — `TracingDriver`: add `base.AppCrashSignal` to `_PROTOCOLS` so `--trace-driver` installs
      it as a real attribute only on a wrapped driver that implements it.
- [x] Unit 10 — `crawl`'s own integration: `_build_lane`'s per-lane `app_crash_artifacts` (never
      `app_crash_tombstone` — `crawl` simply does not thread that second callable anywhere, which is
      what keeps Android's tombstone-pull layer off for the whole walk (Unit 5), with no separate flag
      needed), threaded through `WorkerFactory` and `crawl()`'s primary-lane parameters the same way
      `driver`/`reset` already are; the capture call joining
      `record_crash`'s existing off-lock crash check, gated on
      the driver positively confirming the event (`isinstance`/`app_crash_signal()`) so a UI-tree
      false positive does not pay a full-timeout sweep, wrapped in its own
      `except (base.BackendCrashError, OSError, subprocess.CalledProcessError): pass` — unlike `run`,
      `crawl` owns no recovery path for a
      propagating `XcuitestRunnerCrashError`, and this call sits outside `_walk`'s own `try`; the wider
      catch is a second guard alongside Unit 4's own fix to `app_crash_signal()`, since nothing under
      `backend_cli/adb/` raises `BackendCrashError` and its `RunFn` is `subprocess.run(...,
      check=True)`; `Crash`'s new `artifacts` field, deliberately
      left out of `serialize.py`'s `screenmap_dict`/`screenmap_from_dict` round trip (raw `bytes` has
      no JSON encoding; a carried-forward `Crash` reload always gets `artifacts=()`);
      `repro.py`'s `write_repros` writing non-empty artifacts through the same redacting
      `writer.write_text` path `run`'s own copy uses, under `crashes/crash-NNN/app-crash/`
      before its own `continue` on a non-replayable crash, alongside that crash's own
      `crashes/crash-NNN.yaml` repro.
- [x] Unit 11 — Showcase fixtures: a "force a crash" affordance gated behind a launch-env flag (not a
      build configuration) on iOS (SwiftUI) and Android (Compose), one scenario per platform
      exercising it via `preconditions.launchEnv` — each scenario takes one step after the crash
      trigger (against the now-dead app), not ending on the trigger itself, since a scenario whose last
      step is the crash trigger never fails by this item's own reactive-check design (see *Detecting
      the event*) — wired as a non-gating per-PR signal in `ios-e2e.yml` / `android-e2e.yml`.
- [x] Unit 12 — Docs: `docs/evidence.md` (+ `docs/ja/`) gains this artifact kind; `docs/ci.md`
      (+ `docs/ja/`) notes the showcase signal lane; `docs/architecture.md` (+ `docs/ja/`)
      cross-references the no-retry app-crash path against the existing backend-crash retry section.
- [x] Unit 13 — Tests: three pins for the unconfirmed-launch flag, the third latch Unit 7 adds — the
      most stateful new logic this item introduces, and otherwise ships with no coverage distinguishing
      "the app never foregrounded, so do not confirm" from "confirm on the very first failing step" (the
      misdiagnosis the flag exists to remove) from the opposite failure (the flag latching for a whole
      scenario and disabling the item for a `count`-rung target class). A scenario whose first step
      fails, leased with `readiness.signal == "count"`, asserting no probe runs and `app_crashed` stays
      unset; the same shape leased with `readiness.signal == "readyWhen"`, asserting the probe *does*
      run; and a clear-path test asserting a non-matching `if` and an `http` step both leave the flag
      set while a successful `tap` clears it, and a settled `relaunch` re-sets it. `app_crash_signal()`
      answering `None` on an ordinary `ElementNotFound` (no
      false positive on a missing selector), on a `wait`/`assert` failure, and on `deviceType: device`
      regardless of `app.state`, for both backends; an `AdbDriver` built with `package=None` or an
      unset `launched_at` answering `None` immediately, never reaching `pidof` or `exit-info`, so it
      cannot confirm another process's crash as this app's; a scenario whose crashed step is followed
      by an `after: on: error` `relaunch` still attaching the confirmed crash's own `.ips`/`logcat`
      report — not the empty result a live re-read after the teardown's own `relauncher()` re-stamp
      would produce — pinning that `app_crash_artifacts()` is captured once, at confirmation inside
      `_finish_outcome`, and carried on the outcome rather than re-swept from `pipeline.py`;
      `XcuitestEnvironment.app_launched_at` advancing
      past a `relaunch` step's own launch, past a `crawl`-driven `crawl_reset()`'s own launch, and past
      a `_resume_warm` cross-lease reuse's own launch, and the `.ips` sweep after each finding only the
      report from that most recent launch, never an earlier scenario's or crash's; a fake
      `AndroidEnvironment.app_crash_tombstone()` never invoked from `_finish_outcome` or anywhere else
      inside the step loop — asserted behaviourally, through that fake recording every call it
      receives rather than through a source-level check on `_LoopConfig`/`StepLoopState`, since
      neither records which environment method an opaque `capture_app_crash` callable came from —
      pinning that the tombstone layer's call site stays exclusively in `pipeline.py`'s post-return
      scan; that scan's own
      `_write_app_crash_artifacts` calling `lz.app_crash_tombstone()` exactly once per crashed
      `RunResult` and writing the merged `logcat`-plus-tombstone list, never the outcome's
      `app_crash_artifacts` alone; a crawl lane's own `AndroidEnvironment` never attempting the
      root-gated tombstone pull regardless of a confirmed crash (the `logcat` layer still runs), simply
      because `crawl` never calls `app_crash_tombstone()` at all, while a `run`-leased one, driven
      through `pipeline.py`'s post-return scan, still attempts it; a
      `BackendCrashError` raised by `d.app_crash_signal()` inside `crawl`'s own gate never escaping —
      the `Crash` is still recorded, with no `artifacts`, and the walk continues; a target with
      no `appPath` configured resolving `app_crash_artifacts()` to `[]` up front, never reaching the
      `Info.plist` read at all; `write_repros` writing `crashes/crash-NNN/app-crash/` for a crash whose
      path cannot be faithfully replayed — pinning that the artifact write sits before that loop's own
      `continue`, and that it lands redacted, through `write_text`, not `write_bytes` — under the same
      `NNN` the skipped repro would have carried; a failing `relaunch` step never probing
      `app_crash_signal()`, and neither does its wrapping `if`/`forEach` outcome nor a dispatched
      `after: on: error` step that also fails against the terminated app; an interrupt recovery step
      (BE-0314's `_run_recovery`, distinct from the `after` phase) that also fails against a
      terminated app probing once per recovery step, neither latch bounding it either; an ordinary
      (non-`relaunch`, non-crash) failure three levels deep still probing once per settling outcome,
      pinning that the latch does not bound this case; the iOS `.ips` sweep and the Android `logcat`/tombstone capture
      against stubbed directories and stubbed `adb` output, including the Android exit-info
      corroboration; a stubbed `adb` sequence asserting `app_crash_tombstone()` calls `adb unroot`
      (and a second `adb wait-for-device`) after a successful pull, and does so even when the pull
      itself fails, pinning that `adb root`'s privilege level never outlives the one lease that
      requested it; a stubbed `adb` sequence whose post-`unroot` `id -u` still answers `0` — a refused
      `unroot` — asserting the loud log this now requires, since pinning only that `unroot` is *called*
      would miss a restore that was attempted but never took effect; a `logcat` dump within the launch marker's own time window but carrying only a
      different package's `FATAL EXCEPTION`/`>>> <process> <<<` block extracting nothing, pinning that
      the process bound is real and not only the time one; a stubbed exit-info sequence and a stubbed
      `logcat` dump sequence each answering empty/no-match on the first read and a matching entry only
      on a later one within the bound, confirming `app_crash_signal()` and `app_crash_artifacts()`
      both poll rather than trust a single read, and the bound itself expiring on an entry that never
      arrives; a stubbed startup-crash sequence — a `logcat`/exit-info entry timestamped between a
      pre-`e.launch` marker and where a post-`e.launch` marker would have landed — confirming the
      Android marker is stamped *before* `e.launch`, not after: this is the one case Unit 11's own
      tap-triggered fixture cannot exercise on real hardware, so it needs its own coverage here or the
      before/after decision ships untested on either lane; a `_step_runner.py` test asserting the
      in-band failure, the new `app_crashed`
      field, and the confirmed-crash latch holding across a nested `if`/`forEach` failure — and, on
      that same nested crash with a failing `after: on: error` step dispatched afterward, that
      `app_crashed` is `True` on exactly one settled outcome (the confirming action's own, not its
      wrapping `if`/`forEach` outcomes or the `after` step), so `pipeline.py`'s later scan never has
      to choose among several; a
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
      reports `ok=True`, declaring neither `expect` nor a non-failure `after` rule, staying green with
      no `app_crashed` outcome and no `app-crash/` artifact, pinning the documented blind spot rather
      than leaving it to be rediscovered as a bug; the same shape but with a scenario-level `expect`
      declared, pinning that it instead fails red on the `expect:` assertion mismatch — unclassified,
      no `app_crashed`, no `app-crash/` — a distinct blind spot from the green one, not a variant of it;
      and the same shape again but with an `on: always` `after` rule that also fails against the
      now-dead app, pinning that this one *is* classified, `app_crashed=True` and `app-crash/` written,
      since that teardown step's own outcome reaches `_finish_outcome` through the ordinary
      `_dispatch_after`/`run_phase` path; a `manifest.py` test round-tripping a crashed `RunResult`
      (`StepOutcome.app_crash_artifacts` populated on an outcome in each of `steps`, `before_outcomes`,
      and `after_outcomes`) through `manifest_dict` and a plain `json.dumps` with no `default=`,
      pinning that it raises nothing — the regression this unit's own `_scenario_dict` exclusion
      exists to prevent — and that `report/load.py` reconstructs the same `RunResult` with
      `app_crash_artifacts` back at its `()` default on every affected outcome, `app_crashed` and
      `reason` intact.

Log:

- [#2012](https://github.com/bajutsu-e2e/bajutsu/pull/2012) — Units 1–13. Shipped the whole item: the
  reactive in-band step-loop classification and its three scenario-scoped latches, the iOS `.ips`
  sweep and Android `logcat`/exit-info/tombstone signal, the pipeline write and manifest exclusion,
  the `crawl` integration, the showcase fixtures and their non-gating CI lanes, and the bilingual
  docs. A 3-round self-review pass before this PR opened found and fixed two Android parsing bugs
  (an exit-info field-order assumption that didn't match real `dumpsys` output, and a `logcat`
  crash-block match window that could absorb a neighboring process's crash) and a protocol-isinstance
  gap (`AppCrashPollResettable`, added to keep the exit-info poll's reset working through
  `TracingDriver`) — see the PR body for the full account.
- [#2012](https://github.com/bajutsu-e2e/bajutsu/pull/2012) — iOS evidence fallback. The
  `app-crash (xcuitest)` CI job classified the crash on every run yet never captured a report. A
  listing step in the job then showed why: on the GitHub-hosted macOS runner, `ReportCrash` writes no
  `.ips` for a Simulator app at all. It searched both `DiagnosticReports` stores and the device's own
  CoreSimulator log and data directories. When the `.ips` sweep finds nothing,
  `XcuitestEnvironment` now attaches `unified-log.txt` instead: the Simulator's own unified log for
  the app's launch window (`simctl spawn … log show`, filtered to the app's process and to lines
  naming its bundle id), bounded to its last 256 KB. The xcresult was not used: `xcodebuild` writes it
  on exit, well after the step loop confirms the crash.

## References

- [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact.md)
  (Implemented, [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)) — the runner's own
  crash-report capture this item complements and reuses directly: `_reports_since()` in
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
- [`bajutsu/common/report/manifest.py`](../../bajutsu/common/report/manifest.py) — `_scenario_dict`,
  whose existing `wall_offset_s` pop is the precedent this item's own `app_crash_artifacts` exclusion
  follows
- [`bajutsu/common/report/load.py`](../../bajutsu/common/report/load.py) — `_kw`'s missing-field
  handling, already documented as `wall_offset_s`'s own reconstruction path — `_kw` itself needs no
  change, but its `load.py:34-41` comment gains `app_crash_artifacts` as a second deliberate
  round-trip exception (Unit 8)
- [`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py) —
  the protocol `app_crash_artifacts()` and `app_crash_tombstone()` join
- [`bajutsu/common/platform_lifecycle/relaunchers.py`](../../bajutsu/common/platform_lifecycle/relaunchers.py) —
  `device_relauncher`, the `relaunch` step's actual iOS launch path, distinct from
  `_resume_warm`'s cross-lease one
- [`bajutsu/common/platform_lifecycle/environments/ios.py`](../../bajutsu/common/platform_lifecycle/environments/ios.py) —
  `_DeviceEnvironment.crawl_reset()`, the third iOS launch site `XcuitestEnvironment` overrides to
  keep `app_launched_at` current, alongside `relauncher()`
- [`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py) —
  `_resume_warm`, BE-0291's fourth iOS launch site, on the same long-lived environment across leases,
  and the one this item records `app_launched_at` in too
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
  `fetch_clock`/`act` keywords on the adb branch are the precedent this item's `package` keyword
  follows
- [`docs/ci.md`](../../docs/ci.md#the-ios-lane) — `actuation (xcuitest)` and `golden (adb)`, whose
  non-gating placement this item's showcase scenarios follow; `fault-injection (xcuitest)`, whose
  failure-shape-asserting behavior (not its own, now-required gating) this item's CI wrapper follows
