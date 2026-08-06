**English** · [日本語](BE-XXXX-xcuitest-adb-crash-retry-device-recovery-ja.md)

# BE-XXXX — Force device recovery on a backend-crash retry and cap total crash-recovery time per run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-adb-crash-retry-device-recovery.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1526](https://github.com/bajutsu-e2e/bajutsu/pull/1526) |
| Topic | Platform support |
| Related | [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md), [BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's run pipeline already recovers from a mid-scenario backend crash: it discards the dead
lease, leases a fresh device, and re-runs the whole scenario, bounded by a retry count
(`crash_retries`) and a wall-clock budget (`crash_recovery_budget`) shared with the on-device driver
conformance suite ([BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md),
[BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown.md)). That recovery only
replaces the crashed runner process — it never touches the device the process ran on. When the
crash's real cause is the device itself, such as an iOS Simulator or an Android emulator whose
rendering has stopped responding, the fresh process comes up on the very device that caused the
crash, and the scenario fails the same way again.

This item closes that gap in two parts. First, a crash-triggered retry forces the same
device-erase-and-reinstall sequence a scenario already gets by declaring
`preconditions.erase: true`, instead of a bare in-place respawn. On the XCUITest backend this
restarts the Simulator process itself (`simctl shutdown → erase → boot` plus a reinstall); on the
adb backend it is an app-level clean state (`uninstall`/`install` plus `pm clear`) that never
restarts the emulator process, so it recovers a narrower class of Android failure than the iOS
remedy — see *Alternatives considered*. Second, a new run-scoped wall-clock budget
(`run_crash_recovery_budget`) bounds how long crash recovery may run across a whole run, not only
within one scenario, so a device that keeps degrading fails the run loudly and quickly instead of
silently spending every scenario's own budget until the job's own continuous integration (CI)
timeout cancels it with no diagnosable cause.

## Motivation

On 2026-08-06, the `actuation (xcuitest)` job of a routine pull request ran for 27 minutes and was
then **cancelled** by its own `timeout-minutes` — not failed, a state that names no cause a reader
can act on. The runner log shows what happened. Early in the job, before any scenario ran, the
existing cold-spawn recovery ladder ([BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md))
already rebooted the Simulator once, on its own account:

```
rebooted Simulator 2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA after a failed cold runner spawn
```

That reboot is evidence the existing ladder works when it fires. But once a scenario is under way and
the runner answers `/health` normally, no equivalent trigger exists for a device that degrades while
the run continues. Two scenarios later, the Simulator's screen capture stopped producing frames:

```
recordVideo produced no new bytes in runs/.../02-foreach-opens-every-real-catalog-row/scenario.mp4 within 5.0s
```

and every later screenshot request began timing out, recovering, and timing out again:

```
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
runner channel GET /screenshot failed (attempt 2/3), retrying: timed out
runner channel GET /screenshot: the runner became unreachable past the retry budget — a mid-run crash: ...
runner channel GET /screenshot: the runner recovered from a mid-run crash; re-issuing the idempotent call (recovery 1/3)
```

That pattern repeated across three more scenarios until `xcodebuild` itself exited (code 65) and the
pipeline's own `BackendCrashError` recovery (`bajutsu/runner/pipeline.py`) respawned the runner and
re-ran the scenario from the start. The respawn kept the same environment instance and, in the words
of `bajutsu/runner/pool.py`'s own comment, "respawns cold in place": it launched a fresh runner
process on the same Simulator. The new process answered `/health` normally, so the device-recovery
ladder never ran a second time — that ladder only fires when a spawn attempt itself fails to become
ready, not when a healthy-looking process is handed a Simulator whose rendering has already wedged.
The same pattern reproduced on the next scenario, and the one after that, until the job's
`timeout-minutes` ended it with no scenario ever reporting why.

`.github/workflows/ios-e2e.yml` already documents an identical incident from 2026-08-04, in the
comment above `BAJUTSU_CRASH_RECOVERY_BUDGET`: a job whose scenarios also burn that per-scenario
budget can add it to the cold-spawn recovery ceiling, and "a run on 2026-08-04 hit `timeout-minutes`
that way." The comment records the caveat; nothing in the code closes it, because
`crash_recovery_budget` resets for every new scenario, and each new scenario's first crash-triggered
retry never earns more than an in-place respawn.

## Detailed design

Two independent units.

1. **Force `preconditions.erase = True` on a crash-triggered retry, unless forcing it would be unsafe.**
   In `bajutsu/runner/pipeline.py`, `_ScenarioRunner.run_one`'s retry loop leases a fresh device on
   every attempt after the first. From the second attempt onward, build a copy of the scenario whose
   `preconditions.erase` is `True`, and lease with that copy instead of the original — unless the
   scenario declared `reinstall: overwrite`, or forcing `erase` would itself raise on this route:

   ```python
   retry_scenario = s
   if (
       attempt > 1
       and s.preconditions.reinstall != "overwrite"
       and erase_precondition_supported(actuator, self.eff, self.udid_spec)
   ):
       retry_scenario = s.model_copy(
           update={"preconditions": s.preconditions.model_copy(update={"erase": True})}
       )
   lz = self.lease(self.eff, retry_scenario)
   ```

   `Scenario` and `Preconditions` (`bajutsu/scenario/models/scenario.py`) are both pydantic models, so
   `model_copy(update=...)` overrides the field without mutating the original scenario or its
   `RunResult.scenario` name. Both backends already run the full erase sequence for `erase=True`, so
   this needs no new `simctl`/adb mechanics: `bajutsu/platform_lifecycle/environments/xcuitest.py`
   shuts the Simulator down, erases it, boots it, and reinstalls the app, and
   `bajutsu/platform_lifecycle/environments/android.py` uninstalls and reinstalls the app and clears
   its data. On XCUITest, the erase path already keeps the full cold-startup readiness ceiling rather
   than the tighter one a bare in-place respawn gets, because a device coming back from `erase` is in
   a genuine first-boot state — no code change is needed there.

   The `reinstall != "overwrite"` guard exists because `reinstall: overwrite` is a scenario's explicit
   declaration that it needs its app's data container preserved across a lease (`reinstall`'s default
   is `clean`, and both backends already gate their own wipe on `pre.erase or pre.reinstall ==
   "clean"`). Forcing `erase` unconditionally would silently wipe exactly the state such a scenario
   was written to keep, so a retry for that scenario keeps today's bare in-place respawn instead.

   `preconditions.erase` deliberately does **not** get the same guard: an early version checked
   `s.preconditions.erase is not False` on the theory that an explicit `erase: false` is the same kind
   of deliberate override `reinstall: overwrite` is. Traced against the real CLI path, that guard
   silently disabled this whole unit in production. `bajutsu/cli/commands/run.py`'s `_filter_scenarios`
   resolves every scenario's `erase` from `None` to a concrete `bool` — the target config's own default
   (`False` unless a target opts in) when the scenario itself never set one — *before* `run_all` ever
   sees it, by its own docstring's design ("Leaves every scenario with a concrete bool, so downstream
   never sees the unset `None`"). So a scenario reaching `run_one` with `erase is False` is the *common*
   case (nobody asked for erase), not the rare explicit-opt-out case the guard was written for, and the
   two are indistinguishable by the time the pipeline sees them. See *Alternatives considered* for why
   dropping this guard, rather than threading the pre-resolution signal through, is the right fix.

   `erase_precondition_supported` exists because two XCUITest routes reject any `erase` precondition
   outright instead of honoring it: a real device (`xcuitest.deviceType: device`) and the live
   WebDriver endpoint both raise (`simctl.DeviceError` / `base.UnsupportedAction`) rather than
   silently no-op'ing, by the same "determinism first, fail loudly" design their permission and
   install preconditions already follow. Neither exception is a `base.BackendCrashError`, so forcing
   `erase` there would raise past this loop's own `except BackendCrashError` and abort the whole run
   instead of retrying the one scenario — worse than the bare in-place respawn this item replaces. It
   lives in `bajutsu/backends.py`, next to `capabilities_for_run` (BE-0238) rather than in
   `pipeline.py`, and reuses that same function's exact two routing predicates
   (`xcuitest_targets_real_device(eff)`, `is_webdriver_endpoint(udid_spec)`): the one file that already
   classifies a route's capabilities is also the one place this question is answered, so a future
   XCUITest-adjacent route (a device farm, a new transport) is reviewed here alongside its capability
   narrowing, instead of risking a second, easily-forgotten file elsewhere that still assumes `erase`
   is always safe.

   Android's `pre.erase` is an app-level clean state, not a restart of the emulator process itself
   (`adb emu kill` plus relaunch); see *Alternatives considered*.

2. **Cap the *actual recovery time accumulated* across a whole run.** Add a small primitive next to
   the existing `CrashRecoveryBudget` in `bajutsu/runner/recovery.py`:

   ```python
   class RunCrashRecoveryBudget:
       def __init__(self, budget: float | None, now: Callable[[], float]) -> None:
           self.budget = budget
           self._now = now
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

   `bajutsu/runner/pipeline.py`'s `run_one` times its own crash-retry loop with a *local* variable
   (`recovery_started`, set once at the scenario's first crash) and calls `add_recovery_time` once in
   a `finally` around the whole loop, billing only the seconds this one scenario's own retry attempts
   actually spent recovering. This deliberately bills accumulated recovery time, not wall-clock
   elapsed since some earlier crash: an earlier version of this design armed a single shared deadline
   at the *first* crash anywhere in the run and never re-armed it, so a long, perfectly healthy
   stretch between two unrelated one-off crashes silently ate into the same budget — a scenario whose
   backend crashed only once, late in the run, could be denied even its first retry, exactly the
   "residual one-off crash" `crash_retries` exists to ride out. Billing the accumulated total instead
   means 600s means 600s actually spent recovering.

   The timing state (`recovery_started`) stays local to each `run_one` call rather than a field on
   `RunCrashRecoveryBudget`, because `run_all`'s `workers > 1` path can run several scenarios'
   crash-retry loops concurrently (the same reason `bajutsu/runner/pool.py`'s `lease_defect_lock`
   exists), and a single shared start-of-episode timestamp would let two concurrent recoveries corrupt
   each other's timing — whichever finished first would end "the" episode out from under the other.
   Each `run_one` call only ever calls into the shared object for a threadsafe read (`exhausted`) or a
   single atomic add (`add_recovery_time`) once its own loop is done, so accumulation stays correct
   under any amount of concurrency. `budget` is a public field, not `_budget`, so the one object that
   enforces the budget is also the one place a caller reads the configured seconds for a failure
   message — no second field to keep in sync by hand. Add a matching env-driven default next to
   `_default_crash_recovery_budget`, reading a new `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET`.

   Wire it through `bajutsu/runner/pipeline.py`: `run_all` gains a
   `run_crash_recovery_budget: float | None = None` parameter, resolved the same
   `None`-reads-the-environment way as `crash_recovery_budget`, and passed into `_ScenarioRunner` as
   one `RunCrashRecoveryBudget` shared across every scenario in the run. In `run_one`'s
   `except BackendCrashError` branch, `exhausted()`'s reading decides — alongside the per-scenario
   budget's own `on_crash(attempt).will_retry` — whether to lease again; the failure message names the
   run-level budget as the cause only when it is genuinely the binding one (`run_exhausted and
   decision.will_retry`), not merely coincidentally exhausted while the count or the per-scenario
   budget was the actual reason recovery stopped — otherwise a scenario whose own retry count ran out
   would misleadingly blame a budget it never actually hit.

   Add `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` to `.github/workflows/ios-e2e.yml`'s workflow-level `env`,
   sized well under the `run`/`actuation` jobs' `timeout-minutes`, and rewrite the comment documenting
   the 2026-08-04 caveat to say it is now bounded. Add the same knob, alongside `BAJUTSU_CRASH_RETRIES`
   and `BAJUTSU_CRASH_RECOVERY_BUDGET`, to `android-e2e.yml`, which sets none of the three today and so
   falls back to one retry and an unbounded budget — sized against that workflow's own
   `timeout-minutes`, not copied from the iOS values.

   Update the "Backend-crash recovery in the run pipeline" bullet in `docs/architecture.md` and
   `docs/ja/architecture.md`, and the longer prose passage in `docs/run-loop.md` /
   `docs/ja/run-loop.md`, to describe both the forced-erase retry and the run-scoped budget — the
   latter is the page that spells out today's "not erased" safety claim that this item overturns, so
   it needs the same fix, not only the shorter `architecture.md` summary — per the repository's rule
   that a documented behavior change updates both language mirrors in the same change.

## Alternatives considered

- **Force `erase=True` unconditionally, even for a scenario that declares `reinstall: overwrite`.**
  Rejected: `overwrite` exists precisely so a scenario can keep its app's data container across a
  lease — an upgrade or resume-with-existing-data scenario, for example — and both backends already
  honor that by skipping their own wipe when `reinstall` is `overwrite` and `erase` is unset. A crash
  retry that overrode it anyway would silently swap in a different precondition than the one the
  scenario's own assertions were written against, turning an infrastructure crash into an unrelated
  assertion failure (or a false pass) instead of a clean retry. No scenario in the repository sets
  `reinstall: overwrite` today, but the guard costs one comparison and keeps the retry honest for the
  day one does.
- **Skip the forced-erase retry on an explicit `erase: false`, and thread the scenario's
  pre-CLI-resolution `erase` value through to the runner instead of dropping the guard.** The first
  implementation did the opposite of this and shipped a guard on the *post-resolution* value
  (`s.preconditions.erase is not False`), which turned out to disable the whole unit on the real
  `bajutsu run` CLI path: `_filter_scenarios` (`bajutsu/cli/commands/run.py`) always resolves an unset
  scenario's `erase` to the target's own default (`False` unless a target config opts in) before
  `run_all` ever sees it, so "the scenario explicitly wrote `erase: false`" and "nobody said anything"
  become the same value by the time the pipeline can look. Threading the pre-resolution signal through
  instead — a second field on `Scenario`, or deferring the CLI's resolution until later — would restore
  the distinction, but at the cost of touching the CLI, `Preconditions`, and (per the resolution
  step's own stated invariant, "downstream never sees the unset `None`") likely other code that already
  assumes every scenario reaches the pipeline pre-resolved. Rejected as disproportionate to what a bare
  `erase: false` actually protects: nothing does, on its own — `reinstall`'s own default (`clean`) wipes
  the app's data regardless of `erase`, so `reinstall: overwrite` is the only precondition that
  genuinely needs protecting from an override, and it already has its own guard. Dropping the
  `erase: false` guard costs nothing a scenario relies on today.
- **Escalate to a full device recovery only after several bare respawns, not from the first retry.**
  Rejected: the incident above shows a bare in-place respawn already fails to clear a
  rendering-degraded device, so waiting through several of them before escalating would spend exactly
  the wall-clock this item exists to save, on a remedy already shown not to work. Forcing erase from
  the first retry costs one erase-and-reinstall cycle — a cost every scenario that already declares
  `erase: true` pays on its very first attempt.
- **Keep the "which XCUITest routes reject `erase`" check inside `bajutsu/runner/pipeline.py`, as its
  own private helper, rather than moving it next to `capabilities_for_run` in
  `bajutsu/backends.py`.** Rejected after an early draft did exactly this: `capabilities_for_run`
  already classifies a route's capabilities on these same two predicates
  (`xcuitest_targets_real_device`, `is_webdriver_endpoint`), so a private copy in `pipeline.py` is the
  same rule living in two files. A future route added only to `backends.py`'s narrowing would leave
  the `pipeline.py` copy silently answering "safe," reintroducing the exact whole-run-abort failure
  mode this item's own retry-safety guard exists to prevent — for a *new* route rather than the two
  known today. `erase_precondition_supported` lives in `backends.py` instead, so both questions about
  a route are reviewed in the one file that already owns route classification.
- **Arm a single shared deadline at the run's first crash, rather than accumulating actual recovery
  time.** The first implementation of Unit 2 did this — `note_crash()` set `_deadline = now() +
  budget` on the first crash anywhere in the run and compared every later crash's timestamp against
  it. Rejected once traced through a multi-scenario run: the deadline measures wall-clock *elapsed*,
  not time *spent recovering*, so a long, perfectly healthy stretch between two unrelated one-off
  crashes silently ate into the same budget — a scenario whose backend crashed only once, late in the
  run, could be denied even its first retry, which is exactly the "residual one-off crash"
  `crash_retries` already exists to ride out (`ios-e2e.yml`'s own comment on `BAJUTSU_CRASH_RETRIES`).
  Billing accumulated recovery time (`add_recovery_time`, timed locally per scenario) instead means
  the budget only ever shrinks in response to genuine recovery activity.
- **Give Android a literal emulator-process restart (`adb emu kill` plus relaunch) instead of reusing
  the app-level `erase` path.** Left out of this item's scope: `pre.erase` (uninstall/install plus
  `pm clear`) is already the escalation path a scenario can request on the adb backend, and reusing it
  needs no new adb mechanics. Whether an emulator-process-level restart is worth the added machinery
  is a separate question, for `bajutsu/platform_lifecycle/environments/android.py` /
  `bajutsu/adb.py` to take up later if the app-level path proves insufficient.
- **Extend the run-scoped budget to the on-device driver conformance suite in the same change.** Left
  for a follow-up: the conformance suite (`tests/test_driver_conformance_ondevice.py`, BE-0334) leases
  a module-scoped device directly, not through `Preconditions`, so forced erase does not carry over the
  same way, and that suite's own job already carries its own `timeout-minutes`.
- **Read this as "automatic retry of failed tests,"** which the roadmap's *Not adopting* list already
  rejects as in tension with determinism-first. It is not that: this item extends the
  crash-triggered recovery `BackendCrashError` already gates
  ([BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md),
  [BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown.md),
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)) — infrastructure
  the pipeline already classifies apart from a contract violation — never a scenario that fails its
  own assertions. [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)'s
  "flakiness is never tolerated by absorption" stance holds unchanged: a scenario whose backend keeps
  crashing on every attempt, or whose run-level budget is spent, still fails loudly.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — force `preconditions.erase=True` on attempt 2 and later of a crash-triggered retry,
      unless the scenario declared `reinstall: overwrite`, or the route rejects `erase` outright
      (`erase_precondition_supported` in `bajutsu/backends.py`), on both the XCUITest and adb
      backends. Deliberately not skipped on `erase is False` alone — see *Alternatives considered*.
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
