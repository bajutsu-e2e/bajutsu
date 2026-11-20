**English** · [日本語](BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)

# BE-0353 — Force device recovery on a backend-crash retry and cap total crash-recovery time per run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0353](BE-0353-xcuitest-adb-crash-retry-device-recovery.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0353") |
| Implementing PR | [#1526](https://github.com/bajutsu-e2e/bajutsu/pull/1526) |
| Topic | Platform support |
| Related | [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md), [BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's run pipeline already recovers from a mid-scenario backend crash. It discards the dead lease,
leases a fresh device, and re-runs the whole scenario. Two limits bound the recovery: a retry count
(`crash_retries`) and a wall-clock budget (`crash_recovery_budget`). The on-device driver conformance
suite shares both limits
([BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md),
[BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown.md)).

That recovery replaces only the crashed runner process. It never touches the device the process ran on.
Sometimes the real cause of the crash is the device itself, such as an iOS Simulator or an Android
emulator whose rendering has stopped responding. The fresh process then comes up on the very device that
caused the crash, and the scenario fails the same way again.

This item closes that gap in two parts.

1. A crash-triggered retry forces the same device-erase-and-reinstall sequence that a scenario already
   gets by declaring `preconditions.erase: true`. Before, the retry was a bare in-place respawn.
   - On the XCUITest backend, the sequence restarts the Simulator process itself (`simctl shutdown →
     erase → boot` plus a reinstall).
   - On the adb backend, the sequence is an app-level clean state (`uninstall`/`install` plus
     `pm clear`). It never restarts the emulator process, so it recovers a narrower class of Android
     failure than the iOS remedy. See *Alternatives considered*.
2. A new run-scoped wall-clock budget (`run_crash_recovery_budget`) bounds how long crash recovery may
   run across a whole run, not only within one scenario. A device that keeps degrading then fails the
   run loudly and quickly. Without the budget, the run silently spends every scenario's own budget until
   the job's own continuous integration (CI) timeout cancels it with no diagnosable cause.

The run-scoped budget bounds *retry* time, not the run itself. The budget is read only after a scenario
has already leased and crashed once. Once the budget is spent, every remaining scenario still pays one
cold-spawn attempt against the degraded device before failing on it. This is a deliberate trade-off
against abandoning a device that recovered on its own after the retry that spent the budget (see the
comment above `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` in `.github/workflows/ios-e2e.yml`).

## Motivation

On 2026-08-06, the `actuation (xcuitest)` job of a routine pull request ran for 27 minutes. Its own
`timeout-minutes` then **cancelled** it. The job did not fail. A cancellation names no cause that a
reader can act on. The runner log shows what happened.

Early in the job, before any scenario ran, the existing cold-spawn recovery ladder
([BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)) rebooted the
Simulator once, on its own account:

```
rebooted Simulator 2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA after a failed cold runner spawn
```

That reboot is evidence that the existing ladder works when it fires. But once a scenario is under way
and the runner answers `/health` normally, no equivalent trigger exists for a device that degrades while
the run continues. Two scenarios later, the Simulator's screen capture stopped producing frames:

```
recordVideo produced no new bytes in runs/.../02-foreach-opens-every-real-catalog-row/scenario.mp4 within 5.0s
```

Every later screenshot request then began timing out, recovering, and timing out again:

```
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
runner channel GET /screenshot failed (attempt 2/3), retrying: timed out
runner channel GET /screenshot: the runner became unreachable past the retry budget — a mid-run crash: ...
runner channel GET /screenshot: the runner recovered from a mid-run crash; re-issuing the idempotent call (recovery 1/3)
```

That pattern repeated across three more scenarios, until `xcodebuild` itself exited (code 65). The
pipeline's own `BackendCrashError` recovery (`bajutsu/runner/pipeline.py`) then respawned the runner and
re-ran the scenario from the start. The respawn kept the same environment instance. In the words of the
comment in `bajutsu/runner/pool.py`, it "respawns cold in place": it launched a fresh runner process on
the same Simulator.

The new process answered `/health` normally, so the device-recovery ladder never ran a second time. That
ladder fires only when a spawn attempt itself fails to become ready. It does not fire when a
healthy-looking process is handed a Simulator whose rendering has already wedged. The same pattern
reproduced on the next scenario, and the one after that. The job's `timeout-minutes` ended it, and no
scenario ever reported why.

`.github/workflows/ios-e2e.yml` already documents an identical incident from 2026-08-04, in the comment
above `BAJUTSU_CRASH_RECOVERY_BUDGET`. A job whose scenarios also burn that per-scenario budget can add
the spending to the cold-spawn recovery ceiling, and "a run on 2026-08-04 hit `timeout-minutes` that
way." The comment records the caveat, and nothing in the code closes it. `crash_recovery_budget` resets
for every new scenario, and each new scenario's first crash-triggered retry never earns more than an
in-place respawn.

## Detailed design

Two independent units.

1. **Force `preconditions.erase = True` on a crash-triggered retry, unless forcing it would be unsafe.**
   In `bajutsu/runner/pipeline.py`, the retry loop of `_ScenarioRunner.run_one` leases a fresh device on
   every attempt after the first. From the second attempt onward, build a copy of the scenario whose
   `preconditions.erase` is `True`, and lease with that copy instead of the original. Skip the copy in
   two cases: the scenario declared `reinstall: overwrite`, or forcing `erase` would itself raise on
   this route.

   ```python
   retry_scenario = s
   if (
       attempt > 1
       and self.force_erase_on_retry
       and s.preconditions.reinstall != "overwrite"
       and erase_precondition_supported(actuator, self.eff, self.udid_spec)
   ):
       retry_scenario = s.model_copy(
           update={"preconditions": s.preconditions.model_copy(update={"erase": True})}
       )
   lz = self.lease(self.eff, retry_scenario)
   ```

   `force_erase_on_retry` is a `_ScenarioRunner` field. It is threaded through `run_all` /
   `run_and_report` and defaults to `True`, so every existing caller is unchanged. It carries the value
   of `bajutsu run --no-erase` from before `_filter_scenarios` (`erase is not False`, computed in `run()`
   of `bajutsu/cli/commands/run.py`). The CLI flag later collapses into the same per-scenario bool, and
   the paragraph below explains that this bool cannot distinguish an opt-out from silence.

   Dropping the `preconditions.erase is not False` guard (below) does not lose every opt-out signal. It
   loses only the signal that the *post-resolution scenario field* could carry. The *pre-resolution CLI
   flag* can still carry the signal, and does.

   The lease itself can still fail. `erase=True` sends `XcuitestEnvironment._prepare_simulator` down
   `e.shutdown()` → `e.erase()` → `e.boot()`. A nonzero exit from any of those, from a device that
   rejects the sequence outright, surfaces as `simctl.DeviceError`. That exception is a sibling of
   `BackendCrashError`, not a subclass. It would otherwise escape this loop's own
   `except BackendCrashError` and abort `run_all` entirely, discarding every already-passed scenario's
   verdict.

   `run_one` therefore catches `device_errors.DeviceError` around the forced-erase lease specifically. A
   bare lease keeps its pre-existing behavior, where its own `DeviceError` propagates. On a caught
   error, `run_one` degrades to the ordinary bare respawn and leases `s` unmodified. The fault this
   attempt hits stays this attempt's problem, not the whole run's.

   The subprocess calls of `simctl` carry no `timeout=` (`simctl._real_run`). A `shutdown`, `erase`, or
   `boot` against a fully wedged CoreSimulator daemon can therefore still hang rather than raise. This is
   left as a follow-up. The property is pre-existing for every `erase: true` precondition and is not
   unique to this forced retry. Bounding it needs a deliberately chosen timeout value and failure mode,
   shared with the other calls in `simctl.py`. A local fix to this one retry loop cannot supply that.

   `Scenario` and `Preconditions` (`bajutsu/scenario/models/scenario.py`) are both pydantic models.
   `model_copy(update=...)` overrides the field without mutating the original scenario or its
   `RunResult.scenario` name. Both backends already run the full erase sequence for `erase=True`, so this
   needs no new `simctl` or adb mechanics:

   - `bajutsu/platform_lifecycle/environments/xcuitest.py` shuts the Simulator down, erases it, boots it,
     and reinstalls the app.
   - `bajutsu/platform_lifecycle/environments/android.py` uninstalls and reinstalls the app and clears
     its data.

   On XCUITest, the erase path already keeps the full cold-startup readiness ceiling. A bare in-place
   respawn gets a tighter ceiling. A device coming back from `erase` is in a genuine first-boot state, so
   no code change is needed there.

   The `reinstall != "overwrite"` guard exists because `reinstall: overwrite` is a scenario's explicit
   declaration that it needs its app's data container preserved across a lease. The default of
   `reinstall` is `clean`, and both backends already gate their own wipe on
   `pre.erase or pre.reinstall == "clean"`. Forcing `erase` unconditionally would silently wipe exactly
   the state such a scenario was written to keep. A retry for that scenario keeps today's bare in-place
   respawn instead.

   `preconditions.erase` deliberately does **not** get the same guard. An early version checked
   `s.preconditions.erase is not False`, on the theory that an explicit `erase: false` is the same kind
   of deliberate override that `reinstall: overwrite` is. Traced against the real CLI path, that guard
   silently disabled this whole unit in production.

   `_filter_scenarios` in `bajutsu/cli/commands/run.py` resolves every scenario's `erase` from `None` to a
   concrete `bool` *before* `run_all` ever sees it. When the scenario itself never set a value, the
   resolution uses the target config's own default (`False` unless a target opts in). The docstring
   states this design: "Leaves every scenario with a concrete bool, so downstream never sees the unset
   `None`". A scenario that reaches `run_one` with `erase is False` is therefore the *common* case,
   where nobody asked for erase. It is not the rare explicit-opt-out case the guard was written for. By
   the time the pipeline sees the two cases, they are indistinguishable. See *Alternatives considered*
   for why dropping this guard is the right fix, instead of threading the pre-resolution signal through.

   `erase_precondition_supported` exists because two XCUITest routes reject any `erase` precondition
   outright instead of honoring it:

   - a real device (`xcuitest.deviceType: device`)
   - the live WebDriver endpoint

   Both raise (`simctl.DeviceError` and `base.UnsupportedAction`) instead of silently doing nothing. This
   follows the same "determinism first, fail loudly" design that their permission and install
   preconditions already follow. Neither exception is a `base.BackendCrashError`. Forcing `erase` there
   would raise past this loop's own `except BackendCrashError` and abort the whole run instead of
   retrying the one scenario. That outcome is worse than the bare in-place respawn this item replaces.

   The function lives in `bajutsu/backends.py`, next to `capabilities_for_run` (BE-0238), not in
   `pipeline.py`. It reuses the exact two routing predicates of that function
   (`xcuitest_targets_real_device(eff)` and `is_webdriver_endpoint(udid_spec)`). The one file that
   already classifies a route's capabilities is also the one place that answers this question. A future
   XCUITest-adjacent route (a device farm, a new transport) is then reviewed there, alongside its
   capability narrowing. A second, easily forgotten file could otherwise keep assuming that `erase` is
   always safe.

   Android's `pre.erase` is an app-level clean state, not a restart of the emulator process itself
   (`adb emu kill` plus relaunch). See *Alternatives considered*.

2. **Cap the *actual recovery time accumulated* across a whole run.** Add a small primitive next to the
   existing `CrashRecoveryBudget` in `bajutsu/runner/recovery.py`:

   ```python
   class RunCrashRecoveryBudget:
       def __init__(self, budget: float | None) -> None:
           self.budget = budget if budget is None or budget > 0 else None
           self._spent = 0.0
           self._lock = threading.Lock()

       def exhausted(self) -> bool:
           """Whether the accumulated recovery time already meets the budget."""
           with self._lock:
               return self.budget is not None and self._spent >= self.budget

       def add_recovery_time(self, seconds: float) -> None:
           """Bill `seconds` of actual recovery time against the shared run-level total."""
           with self._lock:
               self._spent += seconds
   ```

   `run_one` in `bajutsu/runner/pipeline.py` times its own crash-retry loop with a *local* variable
   (`recovery_started`, set once at the scenario's first crash). It calls `add_recovery_time` once, in a
   `finally` around the whole loop. The call bills only the seconds that this one scenario's own retry
   attempts actually spent recovering.

   The design deliberately bills accumulated recovery time, not wall-clock time elapsed since some
   earlier crash. An earlier version armed a single shared deadline at the *first* crash anywhere in the
   run and never re-armed it. A long, perfectly healthy stretch between two unrelated one-off crashes
   then silently ate into the same budget. A scenario whose backend crashed only once, late in the run,
   could be denied even its first retry. That is exactly the "residual one-off crash" that
   `crash_retries` exists to ride out. Billing the accumulated total means that 600s means 600s actually
   spent recovering.

   The timing state (`recovery_started`) stays local to each `run_one` call. It is not a field on
   `RunCrashRecoveryBudget`. The `workers > 1` path of `run_all` can run the crash-retry loops of several
   scenarios concurrently (the same reason `lease_defect_lock` in `bajutsu/runner/pool.py` exists). A
   single shared start-of-episode timestamp would let two concurrent recoveries corrupt each other's
   timing, because whichever finished first would end "the" episode out from under the other.

   Each `run_one` call touches the shared object in only two ways: a threadsafe read (`exhausted`), and,
   once its own loop is done, a single atomic add (`add_recovery_time`). Accumulation therefore stays
   correct under any amount of concurrency.

   `budget` is a public field, not `_budget`. The one object that enforces the budget is then also the
   one place where a caller reads the configured seconds for a failure message, with no second field to
   keep in sync by hand. Add a matching env-driven default next to `_default_crash_recovery_budget`,
   reading a new `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET`.

   Wire the budget through `bajutsu/runner/pipeline.py`:

   - `run_all` gains a `run_crash_recovery_budget: float | None = None` parameter. The parameter is
     resolved the same `None`-reads-the-environment way as `crash_recovery_budget`.
   - `run_all` passes the budget into `_ScenarioRunner` as one `RunCrashRecoveryBudget`, shared across
     every scenario in the run.
   - In the `except BackendCrashError` branch of `run_one`, the reading of `exhausted()` decides whether
     to lease again. The decision also uses the `on_crash(attempt).will_retry` of the per-scenario
     budget.
   - The failure message names the run-level budget as the cause only when that budget is genuinely the
     binding one (`run_exhausted and decision.will_retry`). The budget may merely be exhausted by
     coincidence, while the count or the per-scenario budget was the actual reason recovery stopped. A
     scenario whose own retry count ran out would then misleadingly blame a budget it never hit.

   Add `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` to the workflow-level `env` of `.github/workflows/ios-e2e.yml`,
   sized well under the `timeout-minutes` of the `run` and `actuation` jobs. Rewrite the comment that
   documents the 2026-08-04 caveat to say the caveat is now bounded.

   Add the same knob to `android-e2e.yml`, alongside `BAJUTSU_CRASH_RETRIES` and
   `BAJUTSU_CRASH_RECOVERY_BUDGET`. That workflow sets none of the three today, so it falls back to one
   retry and an unbounded budget. Size the new values against that workflow's own `timeout-minutes`, not
   by copying the iOS values.

   Update these documents, per the repository's rule that a documented behavior change updates both
   language mirrors in the same change:

   - the "Backend-crash recovery in the run pipeline" bullet in `docs/architecture.md` and
     `docs/ja/architecture.md`
   - the longer prose passage in `docs/run-loop.md` and `docs/ja/run-loop.md`

   The updates describe both the forced-erase retry and the run-scoped budget. The `run-loop.md` page
   spells out today's "not erased" safety claim, which this item overturns. It needs the same fix, not
   only the shorter summary in `architecture.md`.

## Alternatives considered

- **Force `erase=True` unconditionally, even for a scenario that declares `reinstall: overwrite`.**
  Rejected. `overwrite` exists precisely so that a scenario can keep its app's data container across a
  lease, as an upgrade or resume-with-existing-data scenario does. Both backends already honor that by
  skipping their own wipe when `reinstall` is `overwrite` and `erase` is unset. A crash retry that
  overrode it anyway would silently swap in a different precondition than the one the scenario's own
  assertions were written against. That would turn an infrastructure crash into an unrelated assertion
  failure (or a false pass) instead of a clean retry. No scenario in the repository sets
  `reinstall: overwrite` today. The guard costs one comparison and keeps the retry honest for the day
  one does.
- **Skip the forced-erase retry on an explicit `erase: false`, and thread the scenario's
  pre-CLI-resolution `erase` value through to the runner instead of dropping the guard.** The first
  implementation did the opposite. It shipped a guard on the *post-resolution* value
  (`s.preconditions.erase is not False`), and that guard turned out to disable the whole unit on the
  real `bajutsu run` CLI path. `_filter_scenarios` (`bajutsu/cli/commands/run.py`) always resolves an
  unset scenario's `erase` to the target's own default (`False` unless a target config opts in) before
  `run_all` ever sees it. "The scenario explicitly wrote `erase: false`" and "nobody said anything"
  become the same value by the time the pipeline can look.

  Threading the pre-resolution signal through would restore the distinction. Two ways exist: a second
  field on `Scenario`, or deferring the CLI's resolution until later. Either would touch the CLI and
  `Preconditions`. Per the stated invariant of the resolution step ("downstream never sees the unset
  `None`"), it would likely touch other code that already assumes every scenario reaches the pipeline
  pre-resolved. Rejected as disproportionate to what a bare `erase: false` actually protects, which is
  nothing on its own. The default of `reinstall` (`clean`) wipes the app's data regardless of `erase`.
  `reinstall: overwrite` is therefore the only precondition that genuinely needs protecting from an
  override, and it already has its own guard. Dropping the `erase: false` guard costs nothing that a
  scenario relies on today.
- **Escalate to a full device recovery only after several bare respawns, not from the first retry.**
  Rejected. The incident above shows that a bare in-place respawn already fails to clear a
  rendering-degraded device. Waiting through several of them before escalating would spend exactly the
  wall-clock this item exists to save, on a remedy already shown not to work. Forcing erase from the
  first retry costs one erase-and-reinstall cycle. Every scenario that already declares `erase: true`
  pays that cost on its very first attempt.
- **Keep the "which XCUITest routes reject `erase`" check inside `bajutsu/runner/pipeline.py`, as its own
  private helper, instead of moving it next to `capabilities_for_run` in `bajutsu/backends.py`.**
  Rejected after an early draft did exactly this. `capabilities_for_run` already classifies a route's
  capabilities on these same two predicates (`xcuitest_targets_real_device`, `is_webdriver_endpoint`). A
  private copy in `pipeline.py` would be the same rule living in two files. A future route added only to
  the narrowing in `backends.py` would leave the `pipeline.py` copy silently answering "safe". That
  would reintroduce the exact whole-run-abort failure mode that this item's own retry-safety guard
  exists to prevent, this time for a *new* route instead of the two known today.
  `erase_precondition_supported` lives in `backends.py` instead, so both questions about a route are
  reviewed in the one file that already owns route classification.
- **Arm a single shared deadline at the run's first crash, instead of accumulating actual recovery
  time.** The first implementation of Unit 2 did this. `note_crash()` set `_deadline = now() + budget`
  on the first crash anywhere in the run and compared every later crash's timestamp against it.
  Rejected once traced through a multi-scenario run. The deadline measures wall-clock *elapsed*, not
  time *spent recovering*. A long, perfectly healthy stretch between two unrelated one-off crashes
  silently ate into the same budget. A scenario whose backend crashed only once, late in the run, could
  be denied even its first retry. That is exactly the "residual one-off crash" that `crash_retries`
  already exists to ride out (the comment on `BAJUTSU_CRASH_RETRIES` in `ios-e2e.yml`). Billing
  accumulated recovery time (`add_recovery_time`, timed locally per scenario) means that the budget
  shrinks only in response to genuine recovery activity.
- **Give Android a literal emulator-process restart (`adb emu kill` plus relaunch) instead of reusing the
  app-level `erase` path.** Left out of this item's scope. `pre.erase` (uninstall/install plus
  `pm clear`) is already the escalation path that a scenario can request on the adb backend, and reusing
  it needs no new adb mechanics. Whether an emulator-process-level restart is worth the added machinery
  is a separate question. `bajutsu/platform_lifecycle/environments/android.py` and `bajutsu/adb.py` can
  take it up later, if the app-level path proves insufficient.
- **Extend the run-scoped budget to the on-device driver conformance suite in the same change.** Left
  for a follow-up. The conformance suite (`tests/test_driver_conformance_ondevice.py`, BE-0334) leases a
  module-scoped device directly, not through `Preconditions`, so forced erase does not carry over the
  same way. That suite's own job also already carries its own `timeout-minutes`.
- **Read this as "automatic retry of failed tests."** The *Not adopting* list of the roadmap already
  rejects that idea as in tension with determinism-first. This item is not that idea. It extends the
  crash-triggered recovery that `BackendCrashError` already gates
  ([BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md),
  [BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown.md),
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)). The pipeline
  already classifies this infrastructure apart from a contract violation. The item never retries a
  scenario that fails its own assertions.
  [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)'s "flakiness
  is never tolerated by absorption" stance holds unchanged. A scenario whose backend keeps crashing on
  every attempt, or whose run-level budget is spent, still fails loudly.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — force `preconditions.erase=True` on attempt 2 and later of a crash-triggered retry,
      unless the scenario declared `reinstall: overwrite`, or the route rejects `erase` outright
      (`erase_precondition_supported` in `bajutsu/backends.py`), on both the XCUITest and adb
      backends. Deliberately not skipped on `erase is False` alone — see *Alternatives considered*.
      An explicit `bajutsu run --no-erase` is still honored, via `force_erase_on_retry` carrying that
      flag's pre-resolution value past `_filter_scenarios` (see *Detailed design*). A forced-erase
      lease that itself raises `device_errors.DeviceError` degrades to a bare respawn instead of
      aborting the whole run (see *Detailed design*); the underlying lack of a `timeout=` on
      `simctl`'s subprocess calls is left as a follow-up, not unique to this retry. Honoring
      `--no-erase` (above) had a real CI-side consequence caught by a live `actuation (xcuitest)`
      run: `.github/actions/bajutsu-e2e/action.yml` — the composite action `golden`/`actuation`/
      `run`/`bundled-runner` (xcuitest) all share — hardcoded `--no-erase` on every `bajutsu run`
      invocation, which is a no-op for an ordinary scenario lease (neither showcase target sets a
      target-level `erase` default) but, once this unit honors the flag, silently disabled forced-erase
      recovery on precisely the jobs the 2026-08-06 incident named. Removed the redundant flag from
      the composite action rather than relaxing the CLI-level guarantee.
- [x] Unit 2 — add `RunCrashRecoveryBudget` (accumulated-recovery-time, not deadline-based), wire
      `run_crash_recovery_budget` / `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` through `run_all`, add the
      workflow env knobs, and update `docs/architecture.md` / `docs/run-loop.md` and their `docs/ja/`
      mirrors.

## References

- [BE-0344 — Repair the Simulator between XCUITest cold-spawn retry attempts](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — the device-recovery ladder this item extends to the crash-triggered retry path.
- [BE-0334 — Give the on-device conformance suite the infrastructure-fault recovery the run pipeline already has](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md) — the `CrashRecoveryBudget` this item adds a run-scoped counterpart to.
- [BE-0342 — Give the on-device suites' lease a teardown that reaches the runner](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown.md) — the shared teardown bookkeeping the crash-retry path uses.
- [BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — the flakiness-is-never-absorbed stance this item's bounded recovery preserves.
- `bajutsu/runner/pipeline.py`, `bajutsu/runner/recovery.py`, `bajutsu/runner/pool.py` — the crash-retry loop and the new budget primitive.
- `bajutsu/backends.py` — `capabilities_for_run` and the `erase_precondition_supported` sibling this item adds next to it.
- `bajutsu/platform_lifecycle/environments/xcuitest.py`, `bajutsu/platform_lifecycle/environments/android.py` — the erase path both backends already run.
- `.github/workflows/ios-e2e.yml`, `.github/workflows/android-e2e.yml` — the workflow env knobs this item adds and the incident already documented in the former.
- `docs/run-loop.md`, `docs/architecture.md` (and their `docs/ja/` mirrors) — the documented behavior this item updates.
