**English** · [日本語](ja/run-loop.md)

# The run loop (Orchestrator) and the run pipeline

> The Tier 2 deterministic runner. Each step is **act → (wait) → verify**, and pass/fail comes
> only from machine assertions. No AI is involved. It stops at the first failure.
>
> Implementation: `bajutsu/orchestrator/` (the loop body, package: `loop` / `waits` / `substitution` /
> `evidence_rules` / `actions`) · `bajutsu/runner/` (real-device launch + report wiring, package:
> `pipeline` / `pool` / `launch`).

Related: [scenarios](scenarios.md) · [selectors](selectors.md) · [evidence](evidence.md) · [reporting](reporting.md)

---

## `run_scenario` (running one scenario)

```python
def run_scenario(driver, scenario, clock=None, sink=None, alert_guard=None, ...) -> RunResult
```

- `driver`: a `base.Driver` (a real driver or `FakeDriver`). The loop depends only on this interface.
- `clock`: injected time / sleep (to make waits deterministic in tests). Default `RealClock`
  (`time.monotonic` / `time.sleep`).
- `sink`: the evidence output target (default `NullSink` = writes nothing) ([evidence](evidence.md)).
- `alert_guard`: a handler that, on step failure, "cleans up a blocker (a system alert, etc.) and
  returns the event it dismissed." If it does, **the step is retried exactly once**
  ([the alert guard](recording.md#dismissing-system-alerts-automatically)). For a `wait` step
  (`for`/`settled`/`screenChanged`), the same handler is also armed **mid-wait** (BE-0269): it fires
  against the already-polled screen as soon as the tree looks collapsed — debounced, cooldown-limited,
  capped at two attempts per wait — so a blocked wait can recover before its own timeout elapses,
  independent of the end-of-step retry.

### The flow of one step

For each step `i` (in `orchestrator/loop.py`):

1. `kind = _action_of(step)` — determine which action it is.
2. `step_id = step.name or f"step{i}"` — the evidence output unit.
3. (If `capturePolicy` has a `screenChanged` trigger) record the pre-action `query()`.
4. **Start interval captures** (`video` / `deviceLog` among those that must begin before the
   action). `_pre_intervals` picks only triggers determinable from the step itself
   (`screenChanged`/`error` are too late).
5. Run the **act** (or wait / assert) via `_run_step_body` → `(ok, reason, assertion_results)`.
6. On failure, if `alert_guard` cleared a blocker, **retry once**.
7. **Stop interval captures** (after the step has settled). Record the artifacts.
8. Acquire the **instant captures** (`screenshot` / `elements`) (from `_collect_captures`'s
   firing result).
9. Push a `StepOutcome`. On failure, set `failure` and **break**.

### `_run_step_body` (act / wait / assert dispatch)

- `wait` → `_wait` (condition wait, below).
- `assert_` → evaluate `assertions.evaluate(driver.query(), ...)` and AND.
- otherwise (tap/longPress/type/swipe/relaunch) → `_do_action`.
- catches `SelectorError` / `NotImplementedError` and converts to `(False, reason, [])` (does not
  propagate the exception).

### `_do_action` (the action bodies)

| Action | Body |
|---|---|
| `tap` | `driver.tap(sel)` |
| `longPress` | `driver.long_press(sel, duration)` |
| `type` | if `into`, `driver.tap(into)` first → `driver.type_text(text)` |
| `swipe` | `{from,to}` → `driver.swipe` directly. `{on,direction}` → `resolve_unique` the target → from the frame center, a screen fraction in the direction (`_SWIPE_FRACTION`, default 0.125; `amount` overrides). A fraction, not a fixed count, keeps the scroll reach at parity across backends whose frames use different units (iOS points, Android pixels) |
| `relaunch` | terminate + relaunch the app (re-applying launch env/args + overrides) via the runner-injected relauncher, then wait until ready |

## Waits (condition waits only)

`_wait(driver, w, clock) -> (ok, reason)`. No fixed sleep. It polls `query()` at `_POLL = 0.05s`
intervals until the condition holds or `timeout` is reached.

| Form | Condition met | On timeout |
|---|---|---|
| `for: <sel>` | a matching element appears | **fail** |
| `until: { gone: <sel> }` | a matching element disappears | **fail** |
| `until: screenChanged` | `query()` changed from the initial value | **fail** |
| `until: settled` | on iOS, when the app has reported a screen-transition event (BE-0310): no further one for a short quiescence window. Otherwise: the screen is stable (two consecutive unchanged `query()`s, and there is an element with an id) | **proceed (does not fail)** |

> `settled` is a stabilization hint that "waits for a transition / animation to settle," not a
> correctness assertion. An empty / collapsed tree (mid-render, or covered by a system alert) is
> never treated as settled under the tree-diff path. On timeout it proceeds with the current screen.
> The screen-transition signal (BE-0310) is a positive "the last transition finished and no new one
> started," read-only and opt-in (an app linking `BajutsuKit`'s observer); a target that doesn't
> report it keeps the tree-diff behavior exactly as before.

## Evidence rule firing

Decides whether each `capturePolicy` rule fires for this step ([evidence](evidence.md#a-capturepolicy-rule-based)).

- `_rule_fires`: whether it matches one of `on.action` (+ optional `idMatches`) / `on.event ==
  screenChanged` / `on.result == error`. The action name is mapped to the DSL name
  (`long_press`→`longPress`, `assert_`→`assert`).
- `_collect_captures`: leads with `elements`, then gathers the inline `step.capture` + the fired
  rules' captures + the config's `defaults.capture` baseline (applied unconditionally, unlike the
  other two) and dedupes. Leading with `elements` is what gives every step the post-action tree
  whatever the three sources asked for; `elements.json` has a single filename, so that read replaces
  the pre-action tree the pre-step baseline wrote.
- The other half of the pair — `after.png` — is not on this list. `_handle_action` shoots it itself,
  immediately after the step's action, ahead of every consumer that could otherwise read the tree
  first (a `screenChanged` comparison, a `for`-wait timeout diagnostic, `extract`). It then drops
  `screenshot.after` from the capture list above, which is why a bare `screenshot` from any source is
  normalized to that token first: the shot is never taken twice. One tree still predates the shot —
  a non-mutating step (`assert`, `wait`) reuses the tree it already settled on rather than re-reading
  it (BE-0259), so on those two kinds `elements.json` comes from just before `after.png` instead of
  just after.
- Instant kinds (screenshot/elements) are acquired by the sink's `capture()`; interval kinds
  (video/deviceLog) are collected by stopping the ones started earlier via `start_intervals()`.

`primary_id` is "the id of the step's primary target selector" (tap → the tap target, type → `into`,
swipe → `on`). An `idMatches` trigger `fnmatch`es against this `id`.

## Run results (data structures)

```python
@dataclass
class StepOutcome:
    index: int
    action: str                  # "tap" / "wait" / ...
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
```

`expect` is evaluated only after all steps pass. If `alert_guard` is present, expect is also
re-evaluated once. These become `report/`'s `manifest.json` / JUnit / HTML directly
([reporting](reporting.md)).

## runner (the run pipeline)

Implementation: `bajutsu/runner/`. Connects the orchestrator to a real device and wires through
to the report.

### `launch_driver` (launch the app and return a ready driver)

Builds the environment with `simctl` per the `preconditions`:

```
erase (if pre.erase: shutdown → erase) → boot → bootstatus -b (wait out the boot)
  → terminate(bundle) (for a clean launch state)
  → launch(bundle, [launchArgs, *locale_args(locale)], {**config.launchEnv, **pre.launchEnv})
  → openurl(deeplink) (if any) → make_driver(actuator, udid)
  → _await_ready (poll until query() returns 2+ elements, up to 10s)
```

> `_await_ready` polls for the strongest readiness signal available, in order: an explicit `readyWhen`
> selector, then an app-reported screen-transition event ([BE-0310](../roadmaps/BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md), opt-in via `BajutsuKit`), then any
> element whose id belongs to a declared `idNamespaces`, falling back to "the app has rendered a UI
> (more than the root element)" — up to 10s ([configuration](configuration.md) documents each rung in
> full). `locale` **is**
> applied at launch (the scenario's `preconditions.locale` overrides the config default, passed as
> launch args via `env.locale_args`). `simctl boot` returns as soon as the boot has been *requested*,
> so every step that follows it waits the boot out with `bootstatus` first — including the extra boot
> cycle the system-locale pin runs ([BE-0359](../roadmaps/BE-0359-xcuitest-boot-completion-wait/BE-0359-xcuitest-boot-completion-wait.md)).
> The simctl launch sequencing is validated on a real device
> (iPhone 17 Pro) via `make -C demos/showcase run-swiftui` + the `ios-e2e.yml` CI workflow.

### `device_pool` / `run_all` / `run_and_report`

- `device_pool(udids, backends, ...)`: selects the actuator and returns a `(lease, shutdown)` pair —
  `lease(eff, scenario)` leases a free device and `launch_driver`s it per scenario.
- `run_all(eff, scenarios, lease, ...)`: runs each scenario **with a freshly leased, freshly built
  driver** (clean isolation).
- `run_and_report(...)`: writes the `run_all` results via `write_report(runs_dir/run_id, ...)` and
  returns `(results, manifest_path)`.

The CLI's `run` calls this `run_and_report` ([cli](cli.md#run)).

> **Warm XCUITest runner (BE-0291).** Each scenario still gets a freshly launched app and a fresh
> driver (clean isolation), but the XCUITest backend's resident `xcodebuild` runner — whose cold
> startup is its largest fixed cost — is kept **resident per device across leases** and only the app
> is relaunched between scenarios, so a suite pays that cold start once per device rather than once
> per scenario. The pool holds the warm runner keyed by `(udid, actuator)`; a lease that resolves to
> a different actuator (BE-0240), or a scenario that `erase`s the device, tears it down and respawns,
> and a runner that fails its bounded `/health` probe is treated as a cache miss (one extra cold
> start, never a lost run). The other backends (adb, Playwright, fake) spawn no such resident and
> are unchanged.
>
> The resident runner crashes after a handful of `app.launch()` cycles (an XCTest-session limit; see
> `docs/architecture.md`), so warm reuse is **bounded** (BE-0287): after `BAJUTSU_XCUITEST_MAX_WARM_REUSES`
> reuses (default 3), the runner is respawned cold *before* the next launch can crash it, rather than
> letting the crash land mid-scenario and fail it. The `/health` probe above is only reactive — it
> catches an already-crashed runner — so this proactive refresh is what keeps a long suite off the
> crash. Set the knob to `0` to disable warm reuse entirely (every lease cold) on a device that
> proves to crash sooner.

> **Backend-crash recovery.** The proactive refresh above narrows the crash window but
> does not close it; when a backend still crashes mid-scenario, `_ScenarioRunner.run_one` catches the
> backend-agnostic `base.BackendCrashError` (raised by any driver, not only XCUITest's), discards the
> dead lease, leases a fresh one — a cold respawn, since the pool drops the dead warm runner — and
> re-runs the *whole* scenario from the start, bounded by a retry count (`crash_retries`, default 1,
> so one retry after the first crash — overridable via `BAJUTSU_CRASH_RETRIES`) and an optional
> wall-clock ceiling on the total time spent respawning (`crash_recovery_budget`, unset by default,
> i.e. unbounded — overridable in seconds via `BAJUTSU_CRASH_RECOVERY_BUDGET`). The budget exists because
> the count alone caps retries, not time: a runner that never comes back would otherwise pay a full
> cold-startup ceiling on every one of its `crash_retries` attempts, silently turning into a job hang
> rather than a loud failure. A scenario that crashes on every attempt exhausts one budget or the
> other and fails loudly, so flakiness is never absorbed into a silent pass. A second, run-scoped
> budget (`run_crash_recovery_budget`, also unset by default — overridable via
> `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET`) bounds the *accumulated* recovery time across every scenario in
> the run, not just this one, because `crash_recovery_budget` alone resets for each new scenario: a
> device that keeps degrading would otherwise pay it again and again until an external CI timeout
> cancels the job with no diagnosable cause, rather than the run itself failing loudly. Spending that
> run-level budget on a recovery that ultimately succeeds latches nothing — that only shows the device
> still works — but once a scenario's own crash-retry loop has actually failed because that budget was
> the binding constraint, `run_one` latches on it at the very top of every later scenario — before
> that scenario's own first lease is even attempted, not only inside the crash-retry loop's own
> `except` clause — so a device that has already proven it cannot recover does not still cost every
> remaining scenario one full cold-spawn attempt apiece on the way to the same cancellation. On the XCUITest
> backend specifically, a cold respawn's own bring-up can now also pay a device recovery — a reboot or
> replacement of a Simulator that stopped honouring automation — that neither budget can
> preempt. `CrashRecoveryBudget.on_crash` is consulted only between crashes, not during a bring-up
> already under way, so that recovery's own bound (`BAJUTSU_XCUITEST_RECOVERY_TIMEOUT`, plus its
> unbounded device re-prep) is what limits it instead.
>
> The retry also forces the same `erase` precondition a scenario already gets by declaring
> `erase: true` — a Simulator restart on XCUITest, an app-level clean state on adb — instead of a bare
> in-place respawn onto the very device that just crashed it, unless the scenario declares
> `reinstall: overwrite` to keep its app's data across the lease, or the route itself rejects any
> `erase` precondition outright (a real device, `xcuitest.deviceType: device`, or the live WebDriver
> endpoint). A plain `erase: false` does *not* skip the forced retry: the CLI (`_filter_scenarios` in
> `bajutsu/cli/commands/run.py`) resolves every scenario's `erase` to a concrete bool, most commonly
> `false`, before the pipeline ever sees it, so a guard on that value alone would disable the forced
> retry on the very production path it exists for — only `reinstall: overwrite` genuinely protects a
> scenario's data, since `reinstall`'s own default (`clean`) wipes it regardless of `erase` anyway. An
> explicit `bajutsu run --no-erase`, by contrast, *is* honored: the CLI carries that flag's
> pre-resolution value (`erase is not False`) into the run as `force_erase_on_retry`, so an operator's
> deliberate opt-out still skips the forced retry even though a scenario's own `erase: false` cannot.
> If the forced-erase lease itself fails with a device-level fault (`simctl.DeviceError`/
> `adb.DeviceError`, a sibling type to `BackendCrashError` rather than a subclass of it), the retry
> degrades to the bare in-place respawn instead of letting that fault escape this loop and abort the
> whole run past `run_all`. Because the replayed app's data is wiped by default, the retry is safe
> only for a scenario idempotent up to its crash point; one with a persistent side effect before the
> crash (e.g. a server-side write), or one that depends on state a prior step in the same scenario
> already set up, can fail, or pass against the wrong state, on replay. The decision logic lives in
> `bajutsu/runner/recovery.py`, shared with the on-device driver conformance suite so a Simulator
> infrastructure fault there recovers the same way instead of reddening the required check on an
> unrelated PR (BE-0334).
>
> Above the forced erase sits one more rung, on the Simulator XCUITest route only (BE-0354): a
> **replacement device**. An erase resets the device's data, which recovers app-data corruption but
> not a Simulator whose capture services have wedged — CI showed the erased device coming back wedged
> and the retry reproducing the first attempt exactly. So a retry that already ran with a forced erase
> and crashed again asks its lease's environment for a device that has never run anything, created
> through the same path a vanished device's replacement uses; the degraded device is shut down and
> never handed out again. The attempt's own video evidence can select that rung directly: when the
> recording never confirms it started writing — the wedge's earliest symptom — the *first* crash
> escalates, skipping the erase whose remedy that signal has already ruled out. A replacement attempt
> drops the forced erase, since a device about to be created has nothing to erase. The rung is scoped
> to an unpinned run (`--udid` names a device the operator meant, and a replacement would silently
> move the run off it — and an unpinned run is a pool of one device served by one worker, so the
> escalated retry necessarily leases back the device that asked for the swap) with an `appPath` (a
> blank device has nothing to install), and it honors both
> opt-outs the erase rung honors — `reinstall: overwrite` and `bajutsu run --no-erase` — because a
> replacement resets strictly more than an erase does. Every other route ignores the request and
> keeps the strongest retry it has.
> Both signals are advisory to the *rung*, never to the verdict: a scenario that keeps crashing still
> exhausts its budget and fails loudly.

> **Cooperative cancellation (BE-0370).** A cancelled run finishes on its own terms and lands in the
> run history as a failed run, instead of dying wherever it happens to be. `bajutsu run` answers
> `SIGTERM` by setting an event rather than taking Python's default disposition, which is immediate
> termination, and the pipeline reads that event at three safe boundaries: the top of each scenario in
> `run_all`'s dispatch loop, between the steps of one scenario, and inside the poll loops that already
> back every condition wait. A scenario the request reaches becomes `RunResult(ok=False,
> failure="cancelled")` — the same shape a backend crash or a preflight failure already produces — so
> `run_all` still returns exactly one result per scenario in declaration order, and `run_and_report`
> writes `manifest.json`, `report.html`, and the JUnit XML the way it does for any other failing run.
> An operator who cancels early in a long suite therefore sees every scenario that never got to run
> counted as a failure too, the accepted consequence of treating a cancelled run as failed at all.
>
> The shutdown stays bounded, with or without a canceller watching. A scenario blocked inside a
> single driver call notices the request only once that call returns — an XCUITest HTTP request, an
> `adb` subprocess or a Playwright call all hold it that long — so the graceful path gets a grace
> window. `BAJUTSU_CANCEL_GRACE` sets that window, 60 seconds by default, wide enough to clear the
> longest such call. Past its own deadline beyond that window, the handler restores `SIGTERM`'s
> default disposition and re-raises it, so a genuinely wedged runner dies exactly as it would have
> without the handler installed. The handler answers every sender, not only the `serve` Web UI's Cancel button: a
> `docker stop`, a systemd unit stop, and a CI job cancellation all reach it. `serve` waits out the
> same window on a timer of its own before escalating to an unconditional kill, and passes the window
> down to the run it spawns so the run's internal deadline is bound to the window `serve` is already
> waiting rather than to a constant chosen independently of it.
>
> A cross-browser matrix run reads the event once more, between engine passes. Each pass first brings
> up a whole `device_pool` — resolving the environment, reading the device catalog, starting the
> per-device collectors — so a cancel during the first engine would otherwise still pay every
> remaining engine's bring-up and teardown before the run could finish. Every scenario of an engine
> that never ran is failed as cancelled rather than dropped from the report, so the manifest's
> `matrix` block still names every requested engine and a cell that says `cancelled` states plainly
> that the axis was requested and never executed. Dropping those engines instead would leave `ok`
> aggregating only the passes that ran, so a cancel landing after a green first engine would record a
> `PASS` for a run that never finished.
