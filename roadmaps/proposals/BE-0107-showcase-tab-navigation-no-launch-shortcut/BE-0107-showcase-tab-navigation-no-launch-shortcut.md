**English** · [日本語](BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md)

# BE-0107 — Reach every showcase tab by navigation, not a launch-env shortcut

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0107](BE-0107-showcase-tab-navigation-no-launch-shortcut.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Dogfood fixtures (demo apps) |
| Related | [BE-0079](../../in-progress/BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase.md), [BE-0019](../../in-progress/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) |
| Origin | Dogfooding |
<!-- /BE-METADATA -->

## Introduction

[BE-0079](../../in-progress/BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase.md)
began removing the showcase's launch-time shortcuts to a screen or state: it dropped the
`SHOWCASE_SEED` data-injection knob (the catalog is fixed) and the deeplink *detail push* (a horse
or notice is reached only by tapping its row). It **kept** one shortcut — `SHOWCASE_TAB`, which
selects the initial tab at launch — because the alternative (a scenario tapping the tab bar to move
between tabs) is not yet reliable under the idb backend. This item finishes the job: make every tab
reachable by **driving the tab bar**, and retire `SHOWCASE_TAB`.

## Motivation

**A real test drives the UI it ships; it does not teleport past navigation.** With `SHOWCASE_TAB`,
a scenario for the Log tab launches *onto* the Log tab, exercising none of the navigation a user
performs to get there. That is a gap in the fixture's fidelity: tab switching — a first-class
interaction — is never itself tested, and a regression in it would pass unnoticed.

**idb cannot tap a native tab bar, which is exactly why `SHOWCASE_TAB` exists.** A SwiftUI
`TabView` / UIKit `UITabBarController` collapses in idb's `describe-all` into one opaque `Tab Bar`
group with no per-tab children (verified on device), so its tabs are unaddressable by selector and
a coordinate tap is off the table (the run path resolves selectors only — DESIGN, "determinism
first"). `SHOWCASE_TAB` is the deterministic workaround. Removing it therefore requires a backend
that *can* reach the tabs.

**[BE-0019](../../in-progress/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) is that
backend.** Its XCUITest runner enumerates each native tab as a label-addressable button, so
`tap: {label: "Log", traits: [button]}` switches tabs on the native bar (its `demos/showcase/scenarios-xcuitest/tabs.yaml`
already demonstrates this on the `-noax` products). This item depends on BE-0019 being reliable
enough to run the showcase's functional scenarios: at the time BE-0079 landed, the runner's
`/elements` query was slow and, on some environments, timed out or dropped the connection — the
perf/stability work BE-0019 itself flags as its remaining blocker. Building on it before it is ready
would make the showcase's on-device path flaky, which the determinism directive forbids.

## Detailed design

- **Retire `SHOWCASE_TAB`.** The app always launches on the Stable tab (both toolkits); drop the
  env parsing in `AppModel`. Keep `SHOWCASE_UITEST` (animations-off is a determinism aid, not a
  screen shortcut). Decide the fate of the tab-selecting deeplinks (`…://search` etc.): either drop
  them too (any launch-time tab jump is a shortcut) or keep them solely as a deeplink-feature demo.
- **Move tab-crossing scenarios to the XCUITest backend.** Any scenario that leaves the Stable tab
  gains a leading `tap: {label: "<Tab>", traits: [button]}` step and runs under `--backend ios`.
  The a11y products keep their identifier contract, so within a tab the scenarios still address
  elements by `id`; only the tab switch is label-addressed.
- **Settle the idb vs XCUITest split.** Decide which showcase scenarios stay on idb (the
  Stable-centred smoke/golden the idb-compatibility monitor needs) and which move to XCUITest
  (everything that crosses tabs), and wire `e2e.yml` / `idb-monitor.yml` accordingly. This is the
  crux and must not regress the idb-compatibility signal (BE-0005 / BE-0006).
- **Re-record any goldens** reached via navigation, and update `SPEC.md` §3–§5 (both languages):
  `SHOWCASE_TAB` gone, tabs reached by tapping.

## Alternatives considered

- **A custom, button-backed tab bar** (each tab a `Button` with a `tab.<name>` id, like the
  showcase's segmented control). Rejected for this item: it makes idb able to tap tabs, but it
  replaces the *native* `TabView` / `UITabBarController` — the very controls the fixture exists to
  exercise — with a hand-rolled stand-in, weakening its fidelity. Driving the real native bar via
  XCUITest keeps the fixture honest.
- **Keep `SHOWCASE_TAB` forever.** Rejected: it permanently leaves tab navigation untested and
  contradicts the "no launch-time shortcut to a screen" goal BE-0079 set.
- **Coordinate-tap the tab bar region.** Rejected: raw coordinates violate the selector-only,
  determinism-first run path.

## Progress

- [ ] Retire `SHOWCASE_TAB` (both toolkits); settle the deeplink tab-select question.
- [ ] Move tab-crossing scenarios onto `--backend ios`; settle the idb/XCUITest split and CI wiring.
- [ ] Re-record navigation-reached goldens; update `SPEC.md` §3–§5 (EN + JA).

## References

- [BE-0079 — Consolidate the demo & dogfood apps onto the showcase suite](../../in-progress/BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase.md) — removed the seed / deeplink-push shortcuts; deferred the tab shortcut to this item
- [BE-0019 — XCUITest backend](../../in-progress/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the backend that reaches a native tab bar; its perf/stability is this item's prerequisite
- [BE-0006 — idb element-tree normalization](../../implemented/BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) · [BE-0005 — idb version monitoring](../../implemented/BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md) — the idb-compatibility signal the split must preserve
