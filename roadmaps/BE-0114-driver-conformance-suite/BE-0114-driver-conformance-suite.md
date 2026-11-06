**English** · [日本語](BE-0114-driver-conformance-suite-ja.md)

# BE-0114 — Driver conformance suite for backend-agnostic behavior

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0114](BE-0114-driver-conformance-suite.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0114") |
| Implementing PR | [#632](https://github.com/bajutsu-e2e/bajutsu/pull/632), [#644](https://github.com/bajutsu-e2e/bajutsu/pull/644), [#669](https://github.com/bajutsu-e2e/bajutsu/pull/669) |
| Topic | Driver & backend architecture |
| Related | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) |
<!-- /BE-METADATA -->

## Introduction

We add a **driver conformance suite**: one parametrized test body that runs the same
backend-agnostic spec against every backend. Driver tests today are written per backend. Each
backend must uphold the determinism-core invariants: an ambiguous selector fails, a zero-match
query fails, and `capabilities()` matches actual behavior. Those invariants are asserted separately
for each backend, or not at all. A shared conformance suite (a technology compatibility kit, or
TCK) turns "every backend behaves the same way" from a hope into a check.

## Motivation

Prime directive #3 says the tool, drivers, and runner stay backend-agnostic: a platform is a
backend behind one interface. Two things hold the determinism-core invariants that make this real
today: the shared implementation in `drivers/base`, and each driver's own tests. No common contract
test imposes the same spec on all backends at once.

The backend count is about to rise. Today there are fake, idb, and Playwright backends. XCUITest
(BE-0019) is in progress, and Android (BE-0007) is proposed. As backends multiply, subtle
per-backend behavioral differences can quietly erode the backend-agnostic promise. They are most
likely where a driver bypasses the shared implementation, in its own query resolution or its own
settle handling.

Nothing in the current setup detects such drift. A backend could tap the first match on an
ambiguous selector, or return success on a zero-match query. That backend would pass its own tests,
and no shared test would fail it. The cost/benefit of a conformance suite peaks now, just before
more backends land, because every backend added afterward is then born against the same spec.

## Detailed design

The work divides MECE into the five pieces below.

### 1. Enumerate the backend-agnostic contract

Write down the invariants every `Driver` must satisfy, grounded in the `Driver` Protocol and
DESIGN. At minimum:

- An ambiguous selector (more than one match) fails rather than acting on the first.
- A zero-match query fails rather than returning success.
- `capabilities()` declarations match observed behavior.
- Condition waits, not fixed sleeps, back the wait semantics.
- Evidence and error shapes are uniform across backends.

This enumeration is the definition a backend must meet.

### 2. Build the parametrized conformance suite

Implement a single pytest suite that takes a driver instance (via fixture or parametrization) and
runs the contract's assertions against the instance. The same test body then executes for every
backend. The suite depends on the `Driver` interface, never on a backend's internals.

### 3. Run the Linux-capable backends without a Simulator

Wire FakeDriver into `make check`, so the conformance suite runs on every PR on Linux with no
Simulator. Run the Playwright backend in a separate web CI job (installing `bajutsu[web]` and the
browser binaries) rather than in the fast gate. This matches how `web-e2e.yml` already runs
Playwright today.

### 4. Run the on-device backends in the E2E path

Wire idb and XCUITest so the same suite runs under the on-device E2E path (macOS + Simulator). This
proves that the on-device backends meet the identical contract. The piece reuses the suite from
piece 2 and adds no second spec.

### 5. `capabilities()` conformance and docs

Assert that each backend's declared `capabilities()` matches its observed behavior, tying into
BE-0082's capability preflight. Document the conformance contract as the target a new backend
implementer builds against. BE-0007 (Android) and BE-0019 (XCUITest) then have a concrete
definition of "done" for the driver interface.

### Machine-checkable outcome

The same parametrized suite passes for every backend. A backend that violates a contract invariant
fails the suite. Examples are tapping the first match on an ambiguous selector, succeeding on a
zero-match query, and over-declaring `capabilities()`. The suite is deterministic and model-free.
It *is* directives #2 and #3 made executable, uniformly, across backends.

### Prime-directive compliance

The suite runs on the deterministic path only: machine-checkable assertions, no LLM. It
strengthens directive #3 (backend-agnostic) by making the directive a shared, enforced spec instead
of per-backend manners. It strengthens directive #2 (determinism) by checking the same wait,
selector, and zero-match rules on every backend.

## Alternatives considered

- **Keep per-backend tests (status quo).** Rejected. There is no shared spec, so drift on any path
  that bypasses `drivers/base` is undetectable. Each new backend re-invents the invariant checks or
  omits them.
- **Test only through the `drivers/base` shared implementation.** Rejected. Drivers carry
  backend-specific query and settle code that bypasses the common layer, which is exactly where
  drift hides. The suite must run against the real driver instance, not the shared base alone.
- **Defer the suite until Android / XCUITest land.** Rejected. The value is highest *before* they
  land. Building the suite now means each new backend is developed against the contract from the
  start. Otherwise each backend is retrofitted to the contract after divergence has already crept
  in.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Enumerate the backend-agnostic contract (ambiguous / zero-match / `capabilities()` / wait / evidence invariants)
- [x] Build the parametrized conformance suite against the `Driver` interface
- [x] Run FakeDriver in the fast Linux gate (`make check`); run Playwright in the separate web CI job
- [x] Run idb + XCUITest under the on-device E2E path (same suite)
- [x] `capabilities()` conformance check + document the contract as the "done" definition for a new backend

Log:

- 2026-07-04: First slice — the executable contract (`tests/driver_conformance.py`) and the
  FakeDriver conformance suite (`tests/test_driver_conformance.py`) on the fast Linux gate, plus
  the contract documented in `docs/architecture.md`. Playwright (web CI) and idb / XCUITest
  (on-device E2E) remain, tracked here.
- 2026-07-04: Playwright slice — the same contract now runs against a real headless Chromium
  (`tests/test_driver_conformance_web.py`), realizing each conformance screen as `data-testid`
  HTML on the real `PlaywrightDriver`. It runs in a new `web-conformance` job in `web-e2e.yml`
  (never the fast gate: a `web` pytest marker + `-m 'not web'` deselects it, so the gate stays
  browser-free even when the `web` extra is installed). idb / XCUITest (on-device E2E) remain.
- 2026-07-04: On-device slice — the same contract now runs against the real iOS backends, idb and
  XCUITest (`tests/test_driver_conformance_ondevice.py`, `ConformanceView.swift`). The app enters
  conformance mode once via `SHOWCASE_CONFORMANCE`, then each screen is reseeded by writing a spec
  file the app polls (`conformance-spec.txt` in its Documents dir) — not a per-screen relaunch or
  deeplink: `simctl openurl` raises iOS's "Open in app?" dialog, and relaunching per screen crashes
  the resident XCUITest runner after a handful of `app.launch()` cycles. A dedicated `conformance`
  job in `e2e.yml` builds the app + resident runner and runs both backends serially (`-n0`) through
  `launch_driver`; an `ondevice` pytest marker (`-m 'not web and not ondevice'`) keeps it out of the
  fast gate. Verified on a pristine Simulator: all 18 cases (9 per backend) pass. This completes the
  five-piece breakdown.

## References

The `Driver` Protocol and `drivers/base` shared implementation (the contract the suite enumerates
and the layer backends bypass), the existing per-backend driver tests (fake / idb / Playwright —
what this consolidates into one spec), [DESIGN.md](../../DESIGN.md) and
[docs/architecture.md](../../docs/architecture.md) (the backend-agnostic philosophy and the
implementation-status source of truth),
[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
(the cross-platform abstractions this checks),
[BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)
(the backend registry the suite parametrizes over),
[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)
(the capability preflight this `capabilities()` conformance ties into),
[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) and
[BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) (the incoming backends that gain a
concrete "done" definition from the contract).
