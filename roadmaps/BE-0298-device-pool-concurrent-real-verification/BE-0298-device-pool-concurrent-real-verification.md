**English** · [日本語](BE-0298-device-pool-concurrent-real-verification-ja.md)

# BE-0298 — Real concurrent-device verification of parallel device-pool isolation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0298](BE-0298-device-pool-concurrent-real-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0298") |
| Implementing PR | [#1666](https://github.com/bajutsu-e2e/bajutsu/pull/1666) |
| Topic | Verification & coverage |
| Related | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

`runner/pool.py`'s `device_pool` claims a specific isolation guarantee for `--workers N` runs: each
worker leases its own `udid` and writes evidence under its own `run_dir/<scenario_id>` subdirectory
of the one shared run directory (`run_dir = runs_dir / run_id` in `runner/pipeline.py`), sharing no
mock port or index with any other worker's scenario — the no-shared-state invariant `DESIGN.md` §3.3
states, even though that section's own `runs/<runId>`-per-worker wording predates today's shared
`run_dir` layout. Every test of this guarantee — `tests/runner/test_pool.py` —
monkeypatches `bajutsu.backends.make_driver` to return `FakeDriver` instances against fabricated
udids like `"UDID-A"`/`"UDID-B"`. No CI lane ever boots two real Simulators or two real emulators
concurrently; every job in `ios-e2e.yml`/`android-e2e.yml` boots exactly one device. This item adds
a real concurrent-device lane.

## Motivation

Fabricated udids and `FakeDriver` prove the pool's bookkeeping logic is internally consistent —
worker A's resources really are kept separate from worker B's *in the data structures the pool
manages*. They cannot prove the guarantee holds against real OS-level device and process contention:
whether two real `simctl`/`adb` invocations targeting different devices ever race on a shared
resource idb/adb touches outside the pool's own bookkeeping (a shared boot lock, a port collision, an
artifact path computed before a worker's `run_dir/<scenario_id>` subdirectory is fully established),
or whether two real
devices' [evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage) capture ever
cross-writes under real timing pressure that a synthetic, sequential fake test cannot produce.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Boot two real devices concurrently in an existing E2E lane.** Extend `ios-e2e.yml` (two booted
  Simulators) and, separately, `android-e2e.yml` (two booted emulators, resource permitting), running
  `--workers 2` against a scenario set large enough to keep both workers busy simultaneously.
- **Assert real isolation, not just completion.** Confirm each worker's `udid` and its
  `run_dir/<scenario_id>` subdirectory are cleanly separated and that no artifact from one worker's
  scenario appears under another's — the concrete, checkable form of the isolation claim.
- **Land as non-gating signal first.** A concurrent-device lane is more resource-intensive and
  potentially more environment-sensitive than the existing single-device jobs; follow the precedent
  in [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
  and promote once stable.

## Alternatives considered

- **Trust the fake-driver pool tests, since the bookkeeping logic is unit-tested.** Correct
  bookkeeping in the pool's own data structures says nothing about contention at the OS/subprocess
  level outside those structures, which is precisely what concurrent real devices can surface and a
  sequential fake cannot.
- **Simulate contention with a synthetic stress harness instead of real devices.** A bespoke harness
  would not exercise the actual `simctl`/`adb` subprocess layer where a real race would occur; two
  real concurrently-booted devices are the more faithful (if more expensive) test.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Boot two real devices concurrently — Simulators in `ios-e2e.yml`, emulators in
  `android-e2e.yml` (resources permitting) — and run `--workers 2` against both.
- [x] Assert per-worker isolation of `udid` and the `run_dir/<scenario_id>` subdirectory.
- [x] Land non-gating first.
- [ ] Promote each lane to required once stable.

Log:

- [#1666](https://github.com/bajutsu-e2e/bajutsu/pull/1666) — landed both lanes as non-gating
  signals, plus the assertion they gate on. **The assertion:**
  `scripts/assert_pool_isolation.py` reads the finished run's `manifest.json` and the run directory's
  own subdirectory listing and fails on five distinct violations — an artifact recorded under another
  worker's slug, two results sharing one slug, a subdirectory no result claims, one device having
  taken every scenario, and no two scenarios on different devices having overlapped in wall-clock.
  The last one is what stops a lane passing without ever having produced contention: two devices and
  four scenarios yield two distinct udids even if the pool alternated them serially, so the check
  derives each scenario's window from its steps' own absolute `started_at` (manifest v6) and requires
  a genuine cross-device overlap. Every violation has a unit test
  (`tests/test_assert_pool_isolation.py`), including the case a same-device overlap must *not*
  satisfy. The device count is a lower bound rather than an equality: a run that loses a Simulator
  mid-flight continues on the replacement the pool adopts (BE-0344, reachable on a `--udid`-pinned run
  too), which names a device the pool was never handed, and exceeding the pool size is strictly more
  separation between workers — so only a run that used *fewer* devices than it was given fails.
  **iOS:** `pool (xcuitest)` boots two Simulators (a new `exclude-udid` input on the
  `boot-simulator` action is what makes the second one a different device — without it the action's
  reuse branch returns the first, and `--udid "$A,$A"` would pass every check for the wrong reason)
  and runs smoke / search / notices / navigation through one `bajutsu run --workers 2`. **Android:**
  `pool (adb)` boots a second instance of its cached AVD with `-read-only`, from inside the
  emulator-runner's own step, since that action has no two-emulator mode and its emulator lives only
  for that step's length (`scripts/android_pool_e2e.sh`, `make -C demos/showcase/android e2e-pool`);
  both instances run at `-memory 3072 -cores 1`, which is why the job keeps its own AVD cache key — a
  resumed snapshot's machine config must match the one it was saved with. The Android side buys that
  same distinctness with a check rather than an input: the second instance's console port is fixed at
  5556 while the action's own emulator takes only the first *free* even port, so the script fails before
  booting anything if any attached emulator already holds that serial — otherwise the second `emulator`
  would fail to bind, both boot waits would answer from an emulator it never booted, and the run would
  get either the same serial twice or a device nothing verified. **The change filter:** both
  jobs are keyed on a new `pool` output from `scripts/e2e_changes.py` (`touches_pool`), narrower than
  the lane-wide signal every other job reads, so an ordinary `bajutsu/` change does not pay
  for two booted devices; a `workflow_dispatch` fires them too, which is how they are exercised on
  demand. **DESIGN.md §3.3** was realigned in the same change (BE-0113): its "a `runs/<runId>` per
  worker" wording predated the shared run directory this item's own Introduction describes, and its
  "`--udid` pins a single device" line contradicted the comma list this lane depends on.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/runner/pool.py`, `bajutsu/runner/pipeline.py`, `tests/runner/test_pool.py`, `.github/workflows/ios-e2e.yml`,
  `.github/workflows/android-e2e.yml`, `DESIGN.md` §3.3 (parallel execution and isolation)
