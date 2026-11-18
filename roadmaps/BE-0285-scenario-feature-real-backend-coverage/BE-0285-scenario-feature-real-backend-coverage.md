**English** · [日本語](BE-0285-scenario-feature-real-backend-coverage-ja.md)

# BE-0285 — Verify scenario-authoring features on real backends

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0285](BE-0285-scenario-feature-real-backend-coverage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0285") |
| Implementing PR | [#1184](https://github.com/bajutsu-e2e/bajutsu/pull/1184), [#1214](https://github.com/bajutsu-e2e/bajutsu/pull/1214), [#1377](https://github.com/bajutsu-e2e/bajutsu/pull/1377) |
| Topic | Verification & coverage |
| Related | [BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md), [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md), [BE-0281](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

Several scenario-authoring features run against a real backend only on Android, or nowhere. `extract` and `forEach` ([BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)) appear in no demo scenario, and no lane actuates them. Data-driven rows ([BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md)) and `relaunch` run only on adb.

This item authors real-backend scenarios that exercise these features on adb and web today. The scenarios extend to iOS once BE-0281 lands. They deliberately drive a dynamically changing screen. That screen gives real exercise to two timing premises that `FakeDriver` cannot represent.

## Motivation

`extract` captures a value into `${vars.*}`, and `forEach` iterates the matched elements. Both are pure orchestrator logic over `query()` snapshots, and unit tests cover them thoroughly. A real, mutating element tree is exactly what the fake cannot show. The screen of `FakeDriver` stays frozen unless a test scripts a change. Two cases therefore never get observed:

- A `forEach` over a list that re-orders or recycles its rows mid-loop.
- An `extract` whose field value a real accessibility tree reports differently.

Two shipped optimizations also rest on premises that no fake can exercise:

- The read-count reduction ([BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md)) assumes that two adjacent snapshots, taken with no actuation between them, show the same device state. On a real device, a live clock, an animation, or a background timer can violate that assumption with no actuation at all.
- The wait floor ([BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)) assumes a poll budget calibrated against real Compose recomposition timing.

A frozen fake cannot represent either premise. A scenario over a live, drifting UI is the only place where they are observed.

Re-testing the pure logic on a device would add nothing. The scenarios target the two properties that exist only on real hardware: a mutating tree during iteration, and UI drift between snapshots.

A platform is a backend behind one interface, so every backend that actuates must have these properties checked, not adb and web alone. idb can actuate `tap`, `type`, and `swipe`, yet no iOS CI lane exercises them on a real scenario. [BE-0281](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage.md) proposes wiring that in. Once BE-0281 lands, this item targets iOS alongside adb and web. The iOS lane runs on metered macOS runners, so it lands first as a non-gating signal.

## Detailed design

This section stays at proposal altitude. The work breaks down MECE (mutually exclusive, collectively exhaustive) along the units below.

- **`extract` reuse scenario.** On adb and web, capture a real field's value with `extract` and feed the value into a later step. Assert that the captured value is what the real tree reported.
- **`forEach` over a real list.** On adb and web, iterate a multi-element list, act on each element, and assert the outcome. A frozen fake cannot make this case real.
- **Data-driven and `relaunch` symmetry.** Run data-driven rows and `relaunch` on web too, so that adb alone does not prove either feature.
- **Extend to iOS once BE-0281 lands.** After BE-0281 wires real iOS actuation into CI, run the same `extract`, `forEach`, data-driven, and `relaunch` scenarios on iOS. Run them as a non-gating macOS lane.
- **Dynamic-UI scenario.** Drive a showcase screen that has a live element (an elapsed-time or counter display). The scenario gives real exercise to the read-count snapshot-identity assumption and the wait floor. Wire it into an existing lane as a signal.

## Alternatives considered

- **Leave these features at unit-test level.** The pure logic is covered. The mutating-tree behavior of `forEach` and the snapshot-drift and recomposition-timing premises remain unrepresentable in the fake, and a frozen screen can never surface them.
- **Build a synthetic stress harness for the timing premises.** A bespoke harness would be less faithful than a real showcase screen. It would also not reuse the existing on-device infrastructure. A live showcase element is both closer to reality and cheaper.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `extract` reuse scenario on adb + web (real field value fed into a later step).
- [x] `forEach` over a real multi-element list on adb + web.
- [x] Data-driven and `relaunch` on web (beyond adb).
- [x] Extend the extract / forEach / data-driven / relaunch scenarios to iOS once BE-0281 lands (non-gating macOS lane).
- [x] Dynamic-UI scenario exercising the read-count snapshot-identity and wait-floor premises (web; the
  wait-floor premise, BE-0245, is adb/Android-specific and has no web analog).

**Log**

- 2026-07-17 ([#1184](https://github.com/bajutsu-e2e/bajutsu/pull/1184)): the web slice landed.
  - `demos/web/app/index.html` gained a real multi-element list and a live ticker. No earlier demo had either.
  - `demos/web/scenarios/` gained `extract.yaml`, `foreach.yaml`, `data_driven.yaml`, `relaunch.yaml`, and `dynamic_ui.yaml`. All run against the real Playwright backend.
  - The adb equivalents for `extract` and `forEach`, and the iOS extension, remain.
- 2026-07-21 ([#1214](https://github.com/bajutsu-e2e/bajutsu/pull/1214)): the adb slice landed.
  - `demos/showcase/scenarios/` gained `extract.yaml`, which captures the live counter value of the Log tab and reuses it.
  - It also gained `foreach.yaml`, which iterates the five Stable rows and opens each detail so that the tree mutates between iterations.
  - Both scenarios reuse existing showcase instruments, so the app did not change. They carry both id forms (BE-0221), so they run on Compose and Views alike.
  - Both join the adb lane through `E2E_SCENARIOS` and `E2E_VIEWS_SCENARIOS` in the showcase Android Makefile.
  - The iOS extension still waits on BE-0281.
- 2026-07-26 ([#1377](https://github.com/bajutsu-e2e/bajutsu/pull/1377)): the iOS slice landed and completed the item.
  - `extract.yaml`, `foreach.yaml`, `data_driven.yaml`, and `relaunch.yaml` joined the non-gating `actuation (xcuitest)` job of `ios-e2e.yml`. They are the same shared showcase files that adb runs, unchanged. They run ahead of the device-control scenarios, so no OS-level side effect precedes them.
  - They fold into that existing job instead of a new one. A second non-gating job would differ only in which scenarios it ran. It would also re-pay the boot, install, and cold XCTest-host startup of the lane. The lane already consolidated twice by this reasoning.
  - All four scenarios ran on a real Simulator against both iOS toolkits before the wiring. The run surfaced one asymmetry that no fake could show. The `UIStepper` of the UIKit twin started at 0, so three taps left the counter at 3, while `extract.yaml` expected "4".
  - `LogController` now starts the count at 1, like Compose, Views, and iOS SwiftUI. The one file therefore runs unchanged on every target (BE-0221). `SPEC.md` records that initial value as part of the contract, not as a coincidence across four implementations.

## References

- [BE-0031 — Data-driven scenarios](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md)
- [BE-0033 — Scenario variables and control flow](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)
- [BE-0030 — Parameterized shared steps](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md)
- [BE-0259 — Assert / query snapshot reuse](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md)
- [BE-0245 — adb resident UI Automator server](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)
- [BE-0281 — Add real on-device actuation coverage to the iOS CI](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage.md)
