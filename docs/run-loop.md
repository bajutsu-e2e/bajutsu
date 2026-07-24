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
def run_scenario(driver, scenario, clock=None, sink=None, on_blocked=None) -> RunResult
```

- `driver`: a `base.Driver` (a real driver or `FakeDriver`). The loop depends only on this interface.
- `clock`: injected time / sleep (to make waits deterministic in tests). Default `RealClock`
  (`time.monotonic` / `time.sleep`).
- `sink`: the evidence output target (default `NullSink` = writes nothing) ([evidence](evidence.md)).
- `on_blocked`: a handler that, on step failure, "cleans up a blocker (a system alert, etc.) and
  returns True." If it does, **the step is retried exactly once**
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
6. On failure, if `on_blocked` cleared a blocker, **retry once**.
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
- `_collect_captures`: gathers the inline `step.capture` + the fired rules' captures and dedupes.
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
    duration_s: float            # timing (the actionLog equivalent)
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

`expect` is evaluated only after all steps pass. If `on_blocked` is present, expect is also
re-evaluated once. These become `report/`'s `manifest.json` / JUnit / HTML directly
([reporting](reporting.md)).

## runner (the run pipeline)

Implementation: `bajutsu/runner/`. Connects the orchestrator to a real device and wires through
to the report.

### `launch_driver` (launch the app and return a ready driver)

Builds the environment with `simctl` per the `preconditions`:

```
erase (if pre.erase: shutdown → erase) → boot → terminate(bundle) (for a clean launch state)
  → launch(bundle, [launchArgs, *locale_args(locale)], {**config.launchEnv, **pre.launchEnv})
  → openurl(deeplink) (if any) → make_driver(actuator, udid)
  → _await_ready (poll until query() returns 2+ elements, up to 10s)
```

> `_await_ready` polls until "the app has rendered a UI (more than the root element)." `locale` **is**
> applied at launch (the scenario's `preconditions.locale` overrides the config default, passed as
> launch args via `env.locale_args`). The simctl launch sequencing is validated on a real device
> (iPhone 17 Pro) via `make -C demos/showcase run-swiftui` + the `ios-e2e.yml` CI workflow.

### `device_factory` / `run_all` / `run_and_report`

- `device_factory(udid, backends, ...)`: selects the actuator and returns a factory that
  `launch_driver`s per scenario.
- `run_all(eff, scenarios, factory, ...)`: runs each scenario **with a freshly built driver**
  (clean isolation).
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
> start, never a lost run). idb and the other backends spawn no such resident and are unchanged.
>
> The resident runner crashes after a handful of `app.launch()` cycles (an XCTest-session limit; see
> `docs/architecture.md`), so warm reuse is **bounded** (BE-0287): after `BAJUTSU_XCUITEST_MAX_WARM_REUSES`
> reuses (default 3), the runner is respawned cold *before* the next launch can crash it, rather than
> letting the crash land mid-scenario and fail it. The `/health` probe above is only reactive — it
> catches an already-crashed runner — so this proactive refresh is what keeps a long suite off the
> crash. Set the knob to `0` to disable warm reuse entirely (every lease cold) on a device that
> proves to crash sooner.
