**English** · [日本語](BE-0285-scenario-feature-real-backend-coverage-ja.md)

# BE-0285 — Verify scenario-authoring features on real backends

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0285](BE-0285-scenario-feature-real-backend-coverage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0285") |
| Implementing PR | [#1184](https://github.com/bajutsu-e2e/bajutsu/pull/1184), [#1214](https://github.com/bajutsu-e2e/bajutsu/pull/1214) |
| Topic | Verification & coverage |
| Related | [BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md), [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md), [BE-0281](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

Several scenario-authoring features run against a real backend only on Android, or nowhere.
`extract` and `forEach` ([BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md))
appear in no demo scenario and are actuated by no lane; data-driven rows
([BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md)) and `relaunch` run
only on adb. This item authors real-backend scenarios that exercise these features on adb and web
today, extending to iOS once BE-0281 lands, and deliberately drives them over a dynamically-changing
screen so two timing premises that `FakeDriver` cannot represent get real exercise.

## Motivation

`extract` (capture a value into `${vars.*}`) and `forEach` (iterate the matched elements) are pure
orchestrator logic over `query()` snapshots, and are thoroughly unit-tested. Their correctness
under a real, mutating element tree is exactly what the fake cannot show: `FakeDriver`'s screen is
frozen unless a test scripts a change, so a `forEach` over a list that re-orders or recycles its
rows mid-loop, or an `extract` whose field value is reported differently by a real accessibility
tree, is never observed.

Two shipped optimizations rest on premises no fake can exercise. The read-count reduction
([BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md)) assumes
that two adjacent snapshots taken without an actuation between them are the same device state — an
assumption a live clock, an animation, or a background timer can violate on a real device with no
actuation at all. The wait floor ([BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md))
assumes a poll budget calibrated against real Compose recomposition timing. Both are structurally
unrepresentable in a frozen fake; a scenario over a live, drifting UI is the only place they are
observed.

The point is not to re-test the pure logic on a device — that adds nothing. It is to exercise the
two properties that live only on real hardware: a mutating tree during iteration, and UI drift
between snapshots.

Because a platform is just a backend behind one interface, these properties must be checked on
every backend that actuates, not adb and web alone. idb can actuate `tap` / `type` / `swipe`, but
no iOS CI lane exercises them on a real scenario yet;
[BE-0281](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage.md)
proposes wiring that in, and once it lands this item targets iOS alongside adb and web. The iOS
lane runs on metered macOS runners, so it lands as a non-gating signal first.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **`extract` reuse scenario.** On adb and web, capture a real field's value with `extract`
  and feed it into a later step, asserting the captured value is what the real tree reported.
- **`forEach` over a real list.** On adb and web, iterate a multi-element list, act per
  element, and assert the outcome — the case a frozen fake cannot make real.
- **Data-driven and `relaunch` symmetry.** Run data-driven rows and `relaunch` on web too, so
  neither feature is proven on adb alone.
- **Extend to iOS once BE-0281 lands.** After BE-0281 wires real iOS actuation into CI, run the
  same `extract` / `forEach` / data-driven / `relaunch` scenarios on iOS, as a non-gating macOS
  lane.
- **Dynamic-UI scenario.** Drive a showcase screen with a live element (an elapsed-time or counter
  display) so the read-count snapshot-identity assumption and the wait floor get real exercise;
  wire it into an existing lane as signal.

## Alternatives considered

- **Leave these features at unit-test level.** The pure logic is covered, but the mutating-tree
  behavior of `forEach` and the snapshot-drift and recomposition-timing premises are
  unrepresentable in the fake. A frozen screen can never surface them.
- **Build a synthetic stress harness for the timing premises.** A bespoke harness would be less
  faithful than a real showcase screen and would not reuse the existing on-device infrastructure;
  a live showcase element is both closer to reality and cheaper.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `extract` reuse scenario on adb + web (real field value fed into a later step).
- [x] `forEach` over a real multi-element list on adb + web.
- [x] Data-driven and `relaunch` on web (beyond adb).
- [ ] Extend the extract / forEach / data-driven / relaunch scenarios to iOS once BE-0281 lands (non-gating macOS lane).
- [x] Dynamic-UI scenario exercising the read-count snapshot-identity and wait-floor premises (web; the
  wait-floor premise, BE-0245, is adb/Android-specific and has no web analog).

**Log**

- 2026-07-17 ([#1184](https://github.com/bajutsu-e2e/bajutsu/pull/1184)): web slice landed —
  `demos/web/app/index.html` gained a real multi-element list and a live ticker (no prior demo had
  either), and `demos/web/scenarios/` gained `extract.yaml`, `foreach.yaml`, `data_driven.yaml`,
  `relaunch.yaml`, and `dynamic_ui.yaml`, all run against the real Playwright backend. adb
  equivalents for `extract`/`forEach` and the iOS extension remain.
- 2026-07-21 ([#1214](https://github.com/bajutsu-e2e/bajutsu/pull/1214)): adb slice landed —
  `demos/showcase/scenarios/` gained `extract.yaml` (capture the Log tab's live counter value and
  reuse it) and `foreach.yaml` (iterate the five Stable rows, open each detail so the tree mutates
  between iterations). Both reuse existing showcase instruments (no app change), carry both id forms
  (BE-0221) so they run on Compose and Views alike, and join the adb lane through the showcase
  Android Makefile's `E2E_SCENARIOS` / `E2E_VIEWS_SCENARIOS`. The iOS extension still waits on
  BE-0281.

## References

- [BE-0031 — Data-driven scenarios](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md)
- [BE-0033 — Scenario variables and control flow](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)
- [BE-0030 — Parameterized shared steps](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md)
- [BE-0259 — Assert / query snapshot reuse](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md)
- [BE-0245 — adb resident UI Automator server](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)
- [BE-0281 — Add real on-device actuation coverage to the iOS CI](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage.md)
