**English** · [日本語](BE-XXXX-device-pool-concurrent-real-verification-ja.md)

# BE-XXXX — Real concurrent-device verification of parallel device-pool isolation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-device-pool-concurrent-real-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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

- [ ] Boot two real devices concurrently — Simulators in `ios-e2e.yml`, emulators in
  `android-e2e.yml` (resources permitting) — and run `--workers 2` against both.
- [ ] Assert per-worker isolation of `udid` and the `run_dir/<scenario_id>` subdirectory.
- [ ] Land non-gating first, promote once stable.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/runner/pool.py`, `bajutsu/runner/pipeline.py`, `tests/runner/test_pool.py`, `.github/workflows/ios-e2e.yml`,
  `.github/workflows/android-e2e.yml`, `DESIGN.md` §3.3 (parallel execution and isolation)
