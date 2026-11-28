**English** · [日本語](ja/run-loop.md)

# The run loop (Orchestrator) and the run pipeline

> The Tier 2 deterministic runner. Each step is **act → (wait) → verify**, and pass/fail comes
> purely from machine assertions. No AI is involved. The runner stops at the first failure.
>
> Implementation:
>
> - `bajutsu/common/orchestrator/` holds the loop body. Its package modules are `loop`, `waits`,
>   `substitution`, `evidence_rules`, `actions`, and `control_channel`.
> - `bajutsu/common/runner/` holds the real-device launch and the report wiring. Its package modules
>   are `pipeline`, `pool`, and `launch`.

Related: [scenarios](scenarios.md) · [selectors](selectors.md) · [evidence](evidence.md) · [reporting](reporting.md)

---

## `run_scenario` (running one scenario)

```python
def run_scenario(driver, scenario, clock=None, sink=None, alert_guard=None, ...) -> RunResult
```

- `driver`: a `base.Driver` (a real driver or `FakeDriver`). The loop depends on this interface alone.
- `clock`: injected time / sleep (to make waits deterministic in tests). Default `RealClock`
  (`time.monotonic` / `time.sleep`).
- `sink`: the evidence output target (default `NullSink` = writes nothing) ([evidence](evidence.md)).
- `alert_guard`: a handler for a step that did not pass. It "cleans up a blocker (a system alert,
  etc.) and returns the event it dismissed."
  - If it dismissed one, **the runner retries the step once, never more**
    ([the alert guard](recording.md#dismissing-system-alerts-automatically)).
  - For a `wait` step (`for`/`settled`/`screenChanged`), the same handler is also armed
    **mid-wait** (BE-0269). The handler runs against the already-polled screen as soon as the tree
    looks collapsed.
  - The handler waits out a debounce and a cooldown, and it makes at most two attempts per wait. A
    blocked wait can recover before its own timeout elapses, independent of the end-of-step retry.
- `target_runtimes`: one bundle per target the scenario declares, keyed by name. Each bundle holds
  that target's own live driver and everything else bound to its lease. Absent for a scenario
  declaring no targets, which reads the flat arguments above unchanged.

### Routing a step to its own target (multi-target scenarios)

A [scenario declaring two or more targets](scenarios.md#targets--target-multi-target-scenarios-be-0428)
runs one step loop, not one per target. `run_scenario` builds one step runner per declared target,
all over **one shared loop state**. Each step goes to the runner its own `target` names.

What each target's own bundle carries:

| Field | Bound to |
|---|---|
| `driver` | that target's live driver |
| `sink` | that target's evidence output |
| `network` / `transitions` / `channel` | that target's own collector |
| `webview_bridge` | that target's WebView bridge, so a `web:` block opens the right app |
| `control` / `relaunch` | that target's device |
| `mailbox` / `locale` / `capture` / `interrupts` | that target's own config (`services.mailbox`, `runsOn.ios.locale`, `evidence.capture`, `hooks.interrupts`) |
| `ctx` | that target's `paths.baselines` / `paths.schemas` / `paths.goldens` directories |

Three consequences follow from the shared loop state:

- **One numbering.** Every step takes the next index whichever target ran it. Two targets'
  evidence directories never collide.
- **One `${vars.*}` dictionary.** `live_bindings` belongs to no driver. A value one target's
  `extract` captures is readable from an assertion against another.
- **One verdict.** A miss on the second target ends the scenario as one on the first does.

A step naming no target keeps whatever driver is already active. The rule matters inside a `web:`
block. Its nested steps must keep running against the block's own `WebContextDriver`. Falling back
would reach the app surface underneath the block instead.

The trailing `expect` block groups its entries by the target each names. It polls once per
referenced target, against that target's driver and network source. Results merge back in the
scenario's declared order, so a reader sees the block as authored.

### The lifecycle phases around the step loop

A scenario's `setup` / `cleanup` fields ([scenarios](scenarios.md#setup--cleanup-setup-and-teardown-phases))
wrap the step loop in two more phases. The same step runner drives both. Their steps are
as capable as any other step, and they share the run's `vars.*` bindings:

1. **`setup` runs first.** A miss there sets `failure = "setup: …"` and skips `steps` and
   `expect` entirely, because `setup` is a precondition for the scenario, not a step within it.
2. **`steps` and `expect` run unchanged**, computing the verdict as they did before.
3. **`cleanup` runs once a verdict exists**, on every path out of `steps`. Those paths are the
   passing one, the failing one, and the cancelled one.
   - Each rule whose `on` matches `always` or that verdict runs, in declaration order.
   - When the run was passing, a failing rule's message becomes the run's `failure`. Otherwise the
     message goes behind the existing `failure`.

A cancelled run cannot bound the `cleanup` phase with the cancel source. That source is a latched
`threading.Event.is_set`. Its first read inside the phase would raise `RunCancelled` again. No
cleanup step would run at all.

The phase runs under its own deadline instead, a tenth of `grace_seconds()`. It abandons the
remaining rules once the deadline passes. Cleanup thus gets a bounded chance to run. The
shutdown tail also fits the window `serve` waits before terminating unconditionally.

Each phase numbers its steps from zero and records them in its own `RunResult` list. The report
shows the setup and cleanup blocks apart from the scenario's numbered steps.

`RunResult.after_verdict` records the verdict the cleanup phase dispatched on. A run stored before
BE-0452 holds `error` there. The report loader reads that value as `failure`, so an earlier run
still renders its cleanup block.

### The flow of one step

For each step `i` (in `orchestrator/loop.py`):

1. `kind = _action_of(step)` — determine which action the step is.
2. `step_id = step.name or f"step{i}"` — the evidence output unit.
3. (If `capturePolicy` has a `screenChanged` trigger) record the pre-action `query()`.
4. **Start interval captures** (`video` / `deviceLog` among those that must begin before the
   action). `_pre_intervals` picks the triggers determinable from the step itself and no others, because
   `screenChanged` and `error` are too late.
5. Run the **act** (or wait / assert) via `_run_step_body` → `(ok, reason, assertion_results)`.
6. If the step did not pass and `alert_guard` cleared a blocker, **retry once**.
7. **Stop interval captures** (after the step has settled). Record the artifacts.
8. Take the **instant captures** (`screenshot` / `elements`) that `_collect_captures` lists for
   this step.
9. Push a `StepOutcome`. If the step did not pass, set `failure` and **break**.

### `_run_step_body` (act / wait / assert dispatch)

- `wait` → `_wait` (condition wait, below).
- `assert_` → call `assertions.evaluate(driver.query(), ...)` and require every result to pass.
- otherwise (tap/longPress/type/swipe/relaunch) → `_do_action`.
- catches `SelectorError` / `NotImplementedError` and converts the exception to
  `(False, reason, [])` instead of propagating it.

### `_do_action` (the action bodies)

| Action | Body |
|---|---|
| `tap` | `driver.tap(sel)` |
| `longPress` | `driver.long_press(sel, duration)` |
| `type` | if `into`, `driver.tap(into)` first → `driver.type_text(text)` |
| `swipe` | `{from,to}` → `driver.swipe` directly. `{on,direction}` → `resolve_unique` the target → from the frame center, a screen fraction in the direction (`_SWIPE_FRACTION`, default 0.125; `amount` overrides). A fraction, not a fixed count, keeps the scroll reach at parity across backends whose frames use different units (iOS points, Android pixels) |
| `relaunch` | terminate + relaunch the app (re-applying launch env/args + overrides) via the runner-injected relauncher, then wait until ready |

## Waits (condition waits only)

`_wait(driver, w, clock) -> (ok, reason)` uses no fixed sleep. It polls `query()` at `_POLL = 0.05s`
intervals until the condition holds or the `timeout` expires.

The one fixed pause is the separate `sleep` step. `_do_sleep` runs it in quarter-second slices, so a
cancelled run stops within one slice. The step reads no tree. The determinism audit lists every
`sleep` ([scenarios](scenarios.md#sleep-fixed-pause)).

| Form | Condition met | On timeout |
|---|---|---|
| `for: <sel>` | a matching element appears | **fail** |
| `until: { gone: <sel> }` | a matching element disappears | **fail** |
| `until: screenChanged` | `query()` changed from the initial value | **fail** |
| `until: settled` | on iOS, when the app has reported a screen-transition event (BE-0310): no further one for a short quiescence window. Otherwise: the screen is stable (two consecutive unchanged `query()`s, and there is an element with an id) | **proceed (does not fail)** |

> `settled` is a stabilization hint that "waits for a transition / animation to settle," not a
> correctness assertion. Under the tree-diff path, an empty / collapsed tree (mid-render, or covered
> by a system alert) is never treated as settled. On timeout, the wait proceeds with the current
> screen.
>
> The screen-transition signal (BE-0310) is a positive "the last transition finished and no new one
> started." It is read-only and opt-in (an app linking `BajutsuKit`'s observer). A target that
> doesn't report the signal keeps the tree-diff behavior exactly as before.

<a id="evidence-rule-firing"></a>

## Evidence rule matching

Decides whether each `capturePolicy` rule applies to this step ([evidence](evidence.md#a-capturepolicy-rule-based)).

- `_rule_fires`: whether the step matches one of `on.action` (+ optional `idMatches`),
  `on.event == screenChanged`, or `on.result == error`. The check first converts the action name to
  the Domain Specific Language (DSL) name (`long_press`→`longPress`, `assert_`→`assert`).
- `_collect_captures`: leads with `elements`, then gathers three sources and dedupes them.
  - The sources are the inline `step.capture`, the matched rules' captures, and the target's
    `evidence.capture` baseline. The baseline applies unconditionally, unlike the other two.
  - Leading with `elements` gives every step the post-action tree, whatever the three sources
    ask for.
  - The pre-step baseline writes no tree of its own (BE-0407). This read is the step's sole one,
    into `elements.json`'s single filename.
- The other half of the pair, `after.png`, is not on this list. `_handle_action` takes that
  screenshot right after the step's action.
  - The shot comes ahead of every consumer that could otherwise read the tree first: a
    `screenChanged` comparison, a `for`-wait timeout diagnostic, and `extract`.
  - `_handle_action` then drops `screenshot.after` from the capture list above.
  - For that reason, a bare `screenshot` from any source becomes that token first. The shot is thus
    never taken twice.
- One tree still predates the shot. A non-mutating step (`assert`, `wait`) reuses its
  settled tree rather than re-reading it (BE-0259). On those two kinds, `elements.json` records the
  screen from before `after.png`, not from after.
- The sink's `capture()` takes the instant kinds (screenshot/elements). The interval kinds are video
  and deviceLog. For those, the runner stops what `start_intervals()` began and collects it.

`primary_id` is "the id of the step's primary target selector". That selector is the tap target for
tap, `into` for type, and `on` for swipe. An `idMatches` trigger `fnmatch`es against this `id`.

## Run results (data structures)

```python
@dataclass
class StepOutcome:
    index: int
    action: str                  # "tap" / "wait" / ...
    target: str                  # the declared target that ran it; "" when the scenario declares none
    ok: bool
    reason: str                  # failure reason
    duration_s: float            # timing
    actuations: list[Actuation]  # what the driver actually did: the coordinate/gesture it sent
    assertion_results: list[AssertionResult]
    artifacts: list[Artifact]    # evidence captured for this step

@dataclass
class RunResult:
    scenario: str
    ok: bool
    steps: list[StepOutcome]
    expect_results: list[AssertionResult]  # evaluation of the final expect
    failure: str | None          # e.g. "step 3 (tap): no match: {...}"
    before_outcomes: list[StepOutcome]  # the `setup` phase's own steps
    after_outcomes: list[StepOutcome]   # the `cleanup` phase's own steps
    after_verdict: str           # "success" / "failure" — which `cleanup` rules ran; "" when none
    target_devices: dict[str, TargetDeviceInfo]  # one row per declared target (multi-target only)
    coordinates: str             # e.g. "iPhone SE (3rd generation) / iOS 17"; "" when no condition applies
    run_coordinates: dict[str, dict[str, str]]  # the same coordinates per target and condition field
```

Each of `RunResult`'s singular fields describes one target alone. Those fields are `backend`,
`device`, `device_name`, and `device_runtime`.

- A multi-target run leaves all four empty and fills `target_devices` instead. `target_devices`
  holds one row per declared target. Each row carries that target's backend, udid, model, and OS
  version.
- A single-target run fills the singular fields and leaves `target_devices` empty.

An existing reader of either sees what it always did.

The runner evaluates `expect` after all steps pass, and not earlier. If `alert_guard` is present,
`expect` is also re-evaluated once. These results go directly into `report/`'s outputs
([reporting](reporting.md)). The outputs are `manifest.json`, JUnit, and the HTML report.

## runner (the run pipeline)

Implementation: `bajutsu/common/runner/`. The package connects the orchestrator to a real device and
wires the result through to the report.

### `launch_driver` (launch the app and return a ready driver)

Builds the environment with `simctl` per the `preconditions`:

```
erase (if pre.subject.reinstall is erase: shutdown → erase) → boot → bootstatus -b (wait out the boot)
  → terminate(bundle) (for a clean launch state)
  → launch(bundle, [subject.launch.args, *locale_args(locale)], {**subject.launch.env, **preconditions.subject.launch.env})
  → openurl(deeplink) (if any) → make_driver(actuator, udid)
  → await_ready (poll until query() returns 2+ elements, up to 10s)
```

> `await_ready` polls for the strongest readiness signal available, in this order:
>
> 1. an explicit `subject.startWhen` condition
> 2. an app-reported screen-transition event
>    ([BE-0310](../roadmaps/BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md),
>    opt-in via `BajutsuKit`)
> 3. any element whose id belongs to a declared `subject.idNamespaces`
> 4. the fallback "the app has rendered a UI (more than the root element)", up to 10s
>
> [configuration](configuration.md) documents each rung in full.
>
> Whichever rung fires, the gate then waits for the screen to **stop moving**. It returns once two
> consecutive queries report the same element identifiers, labels, and frames.
>
> Every rung answers "the app's first content is on screen", and a screen mid-transition satisfies
> that answer. The Simulator can drop a touch synthesized into a moving screen. The actuation is then reported as delivered, while the problem surfaces much later, at an
> unrelated step's `wait` timeout.
>
> The confirmation costs one extra poll where the rung's own tree read seeds the comparison. On the
> `screenChanged` rung, which has no tree read of its own, it costs two. The confirmation is capped
> at three polls. After that, the gate returns ready anyway and records `settled: false` in the
> first-wait diagnostic, rather than holding a screen that never holds still. The confirmation
> never turns a ready app into a timeout.
>
> `locale` **is** applied at launch. The scenario's `preconditions.device.ios.locale` overrides the
> target's `runsOn.ios.locale`, and the value is passed as launch args via `env.locale_args`.
> `simctl boot` returns as soon as the boot has been *requested*, so every step that follows it waits
> the boot out with `bootstatus` first. That includes the extra boot cycle the system-locale pin runs
> ([BE-0359](../roadmaps/BE-0359-xcuitest-boot-completion-wait/BE-0359-xcuitest-boot-completion-wait.md)).
> The simctl launch sequencing is validated on a real device (iPhone 17 Pro) via
> `make -C demos/showcase run-swiftui` and the `ios-e2e.yml` CI workflow.

### Bringing several targets up together

`run_all` takes one `TargetPool` per declared target. Each `TargetPool` carries that target's
resolved config and the pool serving its backend. It also carries the actuator and device spec that
the target's own capability preflight reads.

The command-line layer fills the map. The pipeline needs neither the config file nor the
device registry to bring a second target up.

The pipeline builds one pool per distinct **actuator**, not per target. A pool resolves one
environment's device catalog and pre-starts that backend's collectors.

- An iOS target and a web target need two differently-built pools.
- Two targets sharing an actuator share a pool.

The config each target hands `lease()` tells the targets apart. That config also decides the app
the lease launches.

The pipeline leases every declared target before the scenario's first step. Every worker takes the
pools in one fixed order: the pool's actuator, then the target name. That ordering rules out the
circular wait a deadlock needs. All workers approach all pools the same way round, so no two can
hold each other's next pool.

A launch that fails partway through releases everything already up before propagating the error.
The scenario's own end releases the whole set.

### `device_pool` / `run_all` / `run_and_report`

- `device_pool(udids, backends, ...)`: selects the actuator and returns a `(lease, shutdown)` pair.
  `lease(eff, scenario)` leases a free device and `launch_driver`s it per scenario.
- `run_all(eff, scenarios, lease, ...)`: runs each scenario **with a freshly leased, freshly built
  driver**. The result is clean isolation.
- `run_and_report(...)`: writes the `run_all` results via `write_report(runs_dir/run_id, ...)`. It
  returns `(results, manifest_path)`.

The Command Line Interface (CLI) command `run` calls `run_and_report` ([cli](cli.md#run)).

**Runs formed by device conditions (BE-0452)**

Each target a scenario runs against has a `runsOn`, which may declare a condition.
`bajutsu/run/device_matrix.py` then plans the invocation before any scenario runs:

1. It reads each pool once, through `common/devices/facts.py`.
2. It forms each scenario's runs and picks each run's eligible devices, through
   `common/devices/runs.py`.
3. It groups the runs that take the same devices into a cell.

The cells run one after another. Each cell goes through `run_and_report`, on a pool narrowed to the
cell's eligible devices. As a result, `--workers` spreads a cell's runs over identical devices.

A run that no device fits fails. Under an open range, the run becomes a not-applicable record
instead ([configuration](configuration.md#device-conditions-runson)). That record never reaches a
driver. An invocation whose targets declare no condition keeps the path above unchanged.

> **Warm XCUITest runner (BE-0291).** Each scenario still gets a freshly launched app and a fresh
> driver (clean isolation). The XCUITest backend's resident `xcodebuild` runner has the largest fixed
> cost, a cold startup. The pool keeps that runner **resident per device across leases**
> and relaunches only the app between scenarios, so a suite pays the cold start once per device
> rather than once per scenario.
>
> The pool holds the warm runner keyed by `(udid, actuator)`. Three cases tear it down and respawn
> it: a lease that resolves to a different actuator (BE-0240), a scenario that `erase`s the device,
> and a runner that fails its bounded `/health` probe. The failed probe counts as a cache miss,
> which costs one extra cold start and never a lost run.
>
> The other backends (adb, Playwright, fake) spawn no such resident and are unchanged.
>
> The resident runner crashes after a handful of `app.launch()` cycles because of an XCTest-session limit
> that `docs/architecture.md` describes. Warm reuse is thus **bounded** (BE-0287). After
> `BAJUTSU_XCUITEST_MAX_WARM_REUSES` reuses (default 3), the runner is respawned cold *before* the
> next launch can crash it, rather than letting the crash land mid-scenario and fail it. The `/health`
> probe above is merely reactive, because it catches an already-crashed runner. This proactive
> refresh is what keeps a long suite off the crash. Set the knob to `0` to disable warm reuse
> entirely (every lease cold) on a device that proves to crash sooner.

> **Backend-crash recovery.** The proactive refresh above narrows the crash window but does not
> close it. When a backend still crashes mid-scenario, `_ScenarioRunner.run_one` catches the
> backend-agnostic `base.BackendCrashError` (raised by any driver, not only XCUITest's). It then
> discards the dead lease, leases a fresh one, and re-runs the *whole* scenario from the start. The
> fresh lease is a cold respawn, because the pool drops the dead warm runner.
>
> Two settings bound the retries:
>
> - `crash_retries` caps the number of retries. The default is 1, so one retry follows the first
>   crash. `BAJUTSU_CRASH_RETRIES` overrides the value.
> - `crash_recovery_budget` is an optional wall-clock ceiling on the total time spent respawning.
>   The default is unset, meaning unbounded. `BAJUTSU_CRASH_RECOVERY_BUDGET` overrides the value, in
>   seconds.
>
> The budget exists because the count alone caps retries, not time. A runner that never comes back
> would otherwise pay a full cold-startup ceiling on every one of its `crash_retries` attempts, and
> the run would silently turn into a job hang rather than a loud failure. A scenario that crashes on
> every attempt exhausts one budget or the other and fails loudly, so flakiness is never absorbed
> into a silent pass.
>
> A second, run-scoped budget bounds the *accumulated* recovery time across every scenario in the
> run. The budget is `run_crash_recovery_budget`, also unset by default, and
> `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` overrides it. `crash_recovery_budget` alone resets for each new
> scenario. A device that keeps degrading would then pay the budget again and again until an
> external CI timeout cancels the job with no diagnosable cause, rather than the run itself failing
> loudly.
>
> Spending the run-level budget on a recovery that ultimately succeeds latches nothing, because that
> only shows the device still works. Once a scenario's own crash-retry loop has actually failed
> because that budget was the binding constraint, `run_one` latches on it at the very top of every
> later scenario. The latch applies before that scenario's own first lease is even attempted, not
> only inside the crash-retry loop's own `except` clause. A device that has already proven it cannot
> recover thus avoids costing every remaining scenario one full cold-spawn attempt on the way to
> the same cancellation.
>
> The latch has a second cause, which needs no such qualification: a device preparation that *timed
> out* (BE-0374, below). A budget is a measurement that a slow-but-successful recovery can also trip.
> A device command that never returned is a direct observation that the service stopped answering,
> so it latches on its own. The latch carries whichever cause set it, and a later scenario's failure
> reports that cause rather than a budget the run may never have had.
>
> On the XCUITest backend specifically, a cold respawn's own bring-up can also pay a device
> recovery: a reboot or replacement of a Simulator that stopped honouring automation. Neither
> budget can preempt that recovery. `CrashRecoveryBudget.on_crash` is consulted only between
> crashes, not during a bring-up already under way. Instead, the recovery's own bound limits it:
> `BAJUTSU_XCUITEST_RECOVERY_TIMEOUT`, plus its unbounded device re-prep.
>
> The retry also forces `preconditions.subject.reinstall: erase`, the same device reset a scenario
> already gets by declaring that value. That reset is a Simulator restart on XCUITest and an
> app-level clean state on adb. It replaces a bare in-place respawn onto the very device that just
> crashed. Three cases skip the forced erase:
>
> - A merged reinstall of `overwrite`, from the scenario or else its target, which keeps the app's
>   data across the lease.
> - A route that rejects any `erase` precondition outright: a real device
>   (`runsOn.ios.kind: device`) or the live route to an `appium` grid.
> - An explicit `bajutsu run --no-erase`. The CLI carries that flag into the run as
>   `force_erase_on_retry`, so an operator's deliberate opt-out still skips the forced retry.
>
> A merged `clean`, the built-in default, does *not* skip the forced erase. `clean` wipes the app's
> data anyway, so a guard on it would disable the forced retry on the very production path it exists
> for. The CLI no longer stamps a concrete erase value onto every scenario; `_filter_scenarios`
> moves a scenario's reinstall only when a flag changes it
> ([BE-0473](../roadmaps/BE-0473-config-run-group-and-interrupts/BE-0473-config-run-group-and-interrupts.md)).
>
> Suppose the forced-erase lease itself fails with a device-level fault. The fault is
> `simctl.DeviceError` or `adb.DeviceError`, sibling types to `BackendCrashError` rather than
> subclasses of it. The retry then degrades to the bare in-place respawn. Otherwise the fault would
> escape this loop and abort the whole run past `run_all`. One fault is the exception: a **timeout**
> (`device_errors.DeviceTimeout`, of which `simctl.DeviceTimeout` is the iOS case) fails the
> scenario instead (BE-0374).
>
> The degradation answers a device that refused, and a refusal is evidence about one operation. A
> device that never answered has produced no evidence about the operation at all. It shows only that
> the service behind every operation stopped serving. Retrying onto such a device assumes exactly
> what the timeout disproved.
>
> The scenario alone fails, with a message naming the command and the deadline it exceeded. Every
> verdict the run has already earned survives, and protecting those verdicts is what the
> degradation was written for in the first place. The same branch then latches the run (see the
> run-level budget above). A wedged host belongs to the host rather than to the scenario that met
> it, so no later scenario spends its own deadline rediscovering it.
>
> Because the replayed app's data is wiped by default, the retry is safe only for a scenario that is
> idempotent up to its crash point. Two kinds of scenario can fail on replay, or pass against the
> wrong state. The first has a persistent side effect before the crash, such as a server-side write.
> The second depends on state that a prior step in the same scenario already set up. The decision logic
> lives in `bajutsu/common/runner/recovery.py`. The on-device driver conformance suite shares that
> logic, so a Simulator infrastructure fault there recovers the same way instead of reddening the
> required check on an unrelated PR (BE-0334).
>
> Above the forced erase sits one more rung, on the Simulator XCUITest route only (BE-0354): a
> **replacement device**. An erase resets the device's data, which recovers app-data corruption but
> not a Simulator whose capture services have wedged. CI showed the erased device coming back wedged
> and the retry reproducing the first attempt exactly. A retry that already ran with a forced erase
> and crashed again asks its lease's environment for a device that has never run
> anything. The request goes through the same path a vanished device's replacement uses. The
> degraded device is shut down and never handed out again.
>
> That path gives the new device the old device's own device type and runtime, or fails the run. It
> never falls back to a configured model or to the newest iPhone
> ([BE-0452](../roadmaps/BE-0452-target-config-restructure/BE-0452-target-config-restructure.md)).
>
> The attempt's own video evidence can select that rung directly. When the recording never confirms
> it started writing (the wedge's earliest symptom), the *first* crash escalates, skipping the erase
> whose remedy that signal has already ruled out. A replacement attempt drops the forced erase,
> since a device about to be created has nothing to erase.
>
> The rung is scoped to an unpinned run with a `subject.path`:
>
> - `--udid` names a device the operator meant, and a replacement would silently move the run off
>   it. An unpinned run is also a pool of one device served by one worker, so the escalated retry
>   necessarily leases back the device that asked for the swap.
> - A blank device has nothing to install, which is why the rung needs a `subject.path`.
>
> The rung honors both opt-outs the erase rung honors, `reinstall: overwrite` and
> `bajutsu run --no-erase`, because a replacement resets strictly more than an erase does. Every
> other route ignores the request and keeps the strongest retry it has. Both signals are advisory to
> the *rung*, never to the verdict: a scenario that keeps crashing still exhausts its budget and
> fails loudly.
>
> The stall signal also reaches the result
> ([BE-0520](../roadmaps/BE-0520-stalled-video-note-on-red-result/BE-0520-stalled-video-note-on-red-result.md)).
> A scenario can end red without a crash: a device that stopped painting shows up as a wait timeout.
> So a result that did not pass, on a lease whose video never confirmed it started, carries one note
> in `device_notes`. The report shows the note under the first failed step. For a scenario that used
> up its respawns, the signal is read from the last lease that stopped answering. The note is
> evidence, not a cause: the same warning also appears on scenarios that pass. A passing result
> therefore gets no note. The note never changes `ok` or `failure`, and nothing runs again.

> **Cooperative cancellation (BE-0370).** A cancelled run finishes on its own terms and lands in the
> run history as a failed run, instead of dying wherever it happens to be. `bajutsu run` answers
> `SIGTERM` by setting an event rather than taking Python's default disposition, which is immediate
> termination. The pipeline reads that event at three safe boundaries:
>
> - the top of each scenario in `run_all`'s dispatch loop
> - between the steps of one scenario
> - inside the poll loops that already back every condition wait
>
> A scenario the request reaches becomes `RunResult(ok=False, failure="cancelled")`, the same shape
> a backend crash or a failed preflight already produces. `run_all` thus still returns exactly one
> result per scenario in declaration order. `run_and_report` writes `manifest.json`, `report.html`,
> and `junit.xml` the way it does for any other failing run.
>
> An operator who cancels early in a long suite sees every scenario that never got to run counted
> as failed too. That is the accepted consequence of treating a cancelled run as failed at all.
>
> The event is read at one boundary beyond those three: where a backend crash would otherwise
> trigger a fresh cold respawn (the retry above). That bring-up can outlive the grace window below
> and so leave the run killed before it wrote anything.
>
> The shutdown stays bounded, with or without a canceller watching. A scenario blocked inside a
> single driver call notices the request only once that call returns. An XCUITest HTTP request, an
> `adb` subprocess, or a Playwright call can each hold it that long. The graceful path gets a grace
> window. `BAJUTSU_CANCEL_GRACE` sets that window, 90 seconds by default. The default
> combines the 60 seconds a read riding the transient retry can hold, plus 30 for the shutdown tail behind it:
> failing the remaining scenarios, writing the report, and printing the verdict line. A window equal
> to the call alone would leave that tail nothing.
>
> Past its own deadline beyond that window, the handler restores `SIGTERM`'s default disposition
> and re-raises it, so a genuinely wedged runner dies exactly as it would have without the handler
> installed. The handler answers every sender, not only the `serve` Web UI's Cancel button. A
> `docker stop`, a systemd unit stop, and a CI job cancellation all reach it. `serve` waits out the
> same window on a timer of its own before escalating to an unconditional kill. It also passes the
> window down to the run it spawns, so the run's internal deadline is bound to the window `serve` is
> already waiting rather than to a constant chosen independently of it.
>
> A cross-browser matrix run reads the event once more, between engine passes. Each pass first
> brings up a whole `device_pool`: resolving the environment, reading the device catalog, and
> starting the per-device collectors. A cancel during the first engine would otherwise still pay
> every remaining engine's bring-up and teardown before the run could finish. Every scenario of an
> engine that never ran is failed as cancelled rather than dropped from the report, so the manifest's
> `matrix` block still names every requested engine. A cell that says `cancelled` states plainly
> that the axis was requested and never executed. Dropping those engines instead would leave `ok`
> aggregating only the passes that ran, so a cancel landing after a green first engine would record
> a `PASS` for a run that never finished.
