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
       and s.preconditions.erase is not False
       and _erase_is_safe_to_force(actuator, self.eff, self.udid_spec)
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
   was written to keep, so a retry for that scenario keeps today's bare in-place respawn instead. The
   `erase is not False` guard covers the same case a level up: `Preconditions.erase` is `bool | None`
   (`None` inherits the target's own `erase` default; an explicit `true`/`false` pins it for this
   scenario), so a scenario that explicitly pins `erase: false` has made the identical kind of
   deliberate override, and a forced retry must not overrule it either.

   `_erase_is_safe_to_force` exists because two XCUITest routes reject any `erase` precondition
   outright instead of honoring it: a real device (`xcuitest.deviceType: device`) and the live
   WebDriver endpoint both raise (`simctl.DeviceError` / `base.UnsupportedAction`) rather than
   silently no-op'ing, by the same "determinism first, fail loudly" design their permission and
   install preconditions already follow. Neither exception is a `base.BackendCrashError`, so forcing
   `erase` there would raise past this loop's own `except BackendCrashError` and abort the whole run
   instead of retrying the one scenario — worse than the bare in-place respawn this item replaces. The
   guard reuses the exact signal `capabilities_for_run` (BE-0238) already routes preflight on
   (`xcuitest_targets_real_device(eff)`, `is_webdriver_endpoint(udid_spec)`), so the two checks can
   never disagree about which route a scenario is on.

   Android's `pre.erase` is an app-level clean state, not a restart of the emulator process itself
   (`adb emu kill` plus relaunch); see *Alternatives considered*.

2. **Cap total crash-recovery time across a whole run.** Add a small primitive next to the
   existing `CrashRecoveryBudget` in `bajutsu/runner/recovery.py`:

   ```python
   class RunCrashRecoveryBudget:
       def __init__(self, budget: float | None, now: Callable[[], float]) -> None:
           self.budget = budget
           self._now = now
           self._deadline: float | None = None
           self._lock = threading.Lock()

       def note_crash(self) -> bool:
           """Record a crash and report whether the run-level budget is exhausted."""
           with self._lock:
               t = self._now()
               if self.budget is not None and self._deadline is None:
                   self._deadline = t + self.budget
                   return False
               return self._deadline is not None and t >= self._deadline
   ```

   The deadline is set at the first crash anywhere in the run and shared by every scenario after it.
   `note_crash` reads the clock exactly once, so the very crash that arms the deadline is judged
   against the same instant that set it and can never itself be reported exhausted — the same
   never-block-the-first-respawn rule `CrashRecoveryBudget.on_crash` already gives its own single
   clock read (this is why the two operations are one method, not two: an earlier version of this
   design split them into `note_crash()` + a separate `exhausted()`, each with its own clock call —
   still correct on any clock available today, but a real gap between the two calls could in
   principle make the very crash that arms the deadline read as already exhausted). `budget` is a
   public field, not `_budget`, so the one object that enforces the budget is also the one place a
   caller reads the configured seconds for a failure message — no second field to keep in sync by
   hand. A `threading.Lock` guards `note_crash` because `run_all`'s `workers > 1` path shares one
   `_ScenarioRunner` across a thread pool, the same reason `bajutsu/runner/pool.py`'s
   `lease_defect_lock` exists. Add a matching env-driven default next to
   `_default_crash_recovery_budget`, reading a new `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET`.

   Wire it through `bajutsu/runner/pipeline.py`: `run_all` gains a
   `run_crash_recovery_budget: float | None = None` parameter, resolved the same
   `None`-reads-the-environment way as `crash_recovery_budget`, and passed into `_ScenarioRunner` as
   one `RunCrashRecoveryBudget` shared across every scenario in the run. In `run_one`'s
   `except BackendCrashError` branch, `note_crash()`'s return value decides — alongside the
   per-scenario budget's own `on_crash(attempt).will_retry` — whether to lease again; when the run-level
   budget is exhausted, stop retrying and report a failure that names it explicitly, distinct from a
   per-scenario budget or retry-count exhaustion.

   Add `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` to `.github/workflows/ios-e2e.yml`'s workflow-level `env`,
   sized well under the `run`/`actuation` jobs' `timeout-minutes`, and rewrite the comment documenting
   the 2026-08-04 caveat to say it is now bounded. Add the same knob, alongside `BAJUTSU_CRASH_RETRIES`
   and `BAJUTSU_CRASH_RECOVERY_BUDGET`, to `android-e2e.yml`, which sets none of the three today and so
   falls back to one retry and an unbounded budget — sized against that workflow's own
   `timeout-minutes`, not copied from the iOS values.

   Update the "Backend-crash recovery in the run pipeline" bullet in `docs/architecture.md` and
   `docs/ja/architecture.md` to describe both the forced-erase retry and the run-scoped budget,
   per the repository's rule that a documented behavior change updates both language mirrors in the
   same change.

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
- **Escalate to a full device recovery only after several bare respawns, not from the first retry.**
  Rejected: the incident above shows a bare in-place respawn already fails to clear a
  rendering-degraded device, so waiting through several of them before escalating would spend exactly
  the wall-clock this item exists to save, on a remedy already shown not to work. Forcing erase from
  the first retry costs one erase-and-reinstall cycle — a cost every scenario that already declares
  `erase: true` pays on its very first attempt.
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
      unless the scenario declared `reinstall: overwrite`, on both the XCUITest and adb backends.
- [x] Unit 2 — add `RunCrashRecoveryBudget`, wire `run_crash_recovery_budget` /
      `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` through `run_all`, add the workflow env knobs, and update
      `docs/architecture.md` / `docs/ja/architecture.md`.

## References

- [BE-0344 — Repair the Simulator between XCUITest cold-spawn retry attempts](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — the device-recovery ladder this item extends to the crash-triggered retry path.
- [BE-0334 — Give the on-device conformance suite the infrastructure-fault recovery the run pipeline already has](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md) — the `CrashRecoveryBudget` this item adds a run-scoped counterpart to.
- [BE-0342 — Give the on-device suites' lease a teardown that reaches the runner](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown.md) — the shared teardown bookkeeping the crash-retry path uses.
- [BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — the flakiness-is-never-absorbed stance this item's bounded recovery preserves.
- `bajutsu/runner/pipeline.py`, `bajutsu/runner/recovery.py`, `bajutsu/runner/pool.py` — the crash-retry loop and the new budget primitive.
- `bajutsu/platform_lifecycle/environments/xcuitest.py`, `bajutsu/platform_lifecycle/environments/android.py` — the erase path both backends already run.
- `.github/workflows/ios-e2e.yml`, `.github/workflows/android-e2e.yml` — the workflow env knobs this item adds and the incident already documented in the former.
