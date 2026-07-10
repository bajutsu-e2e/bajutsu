**English** · [日本語](BE-XXXX-adb-tab-bar-navigation-ja.md)

# BE-XXXX — Reach every Android tab by driving the tab bar over adb

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-adb-tab-bar-navigation.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md), [BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) |
<!-- /BE-METADATA -->

## Introduction

Since [BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut.md)
retired the `SHOWCASE_TAB` launch shortcut, the showcase's shared scenario set
([`demos/showcase/scenarios/`](../../demos/showcase/scenarios)) reaches every non-launch tab by
tapping the native tab bar — a single cross-backend selector, `tap: { label: "Log", traits: [button] }`.
iOS satisfies that selector on the XCUITest backend, which enumerates each tab as a
label-addressable button. The Android adb backend
([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) does not yet: the physical tap
is available, but the shared selector does not *resolve* against the `uiautomator dump` tree, so
`bajutsu run` fails before it can tap. As a direct consequence the Android on-device e2e lane
([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)) shrank to the
Stable-tab scenarios. This item gives the adb driver the ability to reach every tab by driving the
tab bar with the same selector iOS uses, restoring the shared scenarios' portability to Android and
unblocking BE-0208's scenario growth.

## Motivation

When BE-0107 removed `SHOWCASE_TAB`, reaching a non-Stable tab became a tab-bar tap by
`{ label, traits: [button] }` on every backend. On the adb backend the tap *mechanism* works — the
driver resolves a selector to an element frame and taps its centre — but the *resolution* fails.
`uiautomator dump` surfaces the Compose `NavigationBarItem` with its visible text as the `label`
channel (so "Log" matches), but the item's widget `class` is not `android.widget.Button`, so the
driver's class-to-trait mapping ([`_norm_class`](../../bajutsu/drivers/adb.py)) never emits the
`button` trait the shared selector requires. The element is found by label and then rejected by
trait, and — correctly, under determinism — the run fails rather than tapping something that only
half-matched.

The cost is concrete. BE-0208's Android e2e lane holds out `search`, `data_driven`, `relaunch`,
`system`, and the Log/Notices-tab flows (`components`, `modals`, `gestures`, `controls`, `notices`)
— the majority of the shared set — leaving Android on-device coverage at `smoke`, `firstlook`, and
`navigation` (all Stable-tab). Every one of those held-out scenarios begins by switching tabs, so
none can run until the tab bar is drivable.

This is a driver-layer portability gap, which is exactly where prime directive 3 (app-agnostic)
places it: a scenario is authored once and must resolve on every backend, and which app-side
attribute satisfies a selector lives inside the Driver, never the scenario. A tab that iOS reaches
by `{ label, traits: [button] }` but Android cannot is a gap in the adb Driver, not in the shared
scenario. The fix must keep the determinism contract intact — an ambiguous tab match fails rather
than tapping the first hit, and no fix reaches for a raw coordinate tap.

## Detailed design

The work is deliberate about resting on device truth first, then fixing the driver, then
discharging the coverage the fix unblocks. It touches the adb Driver and the showcase e2e wiring;
it changes no shared scenario (that is the point) and adds no LLM to any path.

### Work breakdown (MECE)

1. **Diagnose the tab-bar tree on device.** Capture `uiautomator dump` over the Compose
   (`NavigationBarItem`) and Views (`BottomNavigationView`) showcase tab bars and pin exactly which
   channels each tab item exposes — `class`, `text`, `content-desc`, `selected`, `clickable` — so
   the mapping decision rests on the real tree, not an assumption about how Compose flattens.
2. **Resolve the cross-backend tab selector in the adb driver.** Make `{ label, traits: [button] }`
   resolve to the correct tab item on the adb backend, mirroring how XCUITest exposes each tab as a
   label-addressable button — the fix living in the driver's normalization (the trait/label
   mapping), app-agnostic, with no special-case for the showcase. Ambiguity stays fail-fast: two
   tabs matching one selector raise rather than tap the first.
3. **Cover both Android toolkits.** Compose and Views surface the bar with different widget classes
   in `uiautomator dump`; the resolution must hold for both showcase twins (`showcase-compose`,
   `showcase-views`), or the residual difference is documented and scoped out explicitly.
4. **Reintroduce the blocked scenarios into the e2e lane.** Once the bar is drivable, add the
   held-out shared scenarios back to [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile)'s
   `E2E_SCENARIOS` as each passes on device — `search`, `data_driven`, `relaunch`, `system` first,
   then the Log/Notices flows subject to the CI emulator's software-render timing. This is what
   discharges BE-0208's Unit 5.
5. **Add tab navigation to the driver conformance suite.** Extend the driver conformance contract
   ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) with a
   tab-bar navigation case, so the capability is checked across backends rather than only implied by
   the showcase lane.

## Alternatives considered

- **Keep a launch-time tab shortcut for Android only.** Rejected: it reintroduces the exact
  fixture-fidelity gap BE-0107 removed — a scenario for the Log tab that launches *onto* the Log tab
  and exercises none of the tab switching a user performs — and it makes Android diverge from iOS on
  the shared set, an app/backend-specific carve-out that prime directive 3 forbids.
- **Give the showcase tab items explicit ids and switch the shared scenarios to id-based tab taps.**
  Rejected: the shared scenarios reach tabs by `{ label, traits: [button] }` precisely so the one
  scenario drives both iOS and Android; changing the selector to an id would fork the scenario per
  backend and weaken the cross-backend guarantee. The right layer to change is the Driver, not the
  scenario.
- **Coordinate-tap the tab bar by geometry.** Rejected: the run path resolves selectors only
  (DESIGN, "determinism first") — a raw coordinate tap has no addressable target and breaks the
  "ambiguous selector fails" contract.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Diagnose the Compose / Views tab-bar tree via `uiautomator dump` on device.
- [ ] Resolve `{ label, traits: [button] }` to the tab item in the adb driver (fail-fast on ambiguity).
- [ ] Cover both Android toolkits (`showcase-compose`, `showcase-views`), or document the scoped gap.
- [ ] Reintroduce the held-out shared scenarios into the Android e2e lane (BE-0208 Unit 5).
- [ ] Add a tab-navigation case to the driver conformance suite (BE-0114).

## References

[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0208 — Android on-device e2e in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[BE-0107 — Reach every showcase tab by navigation](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py),
[`demos/showcase/android/compose/.../RootScreen.kt`](../../demos/showcase/android/compose/src/main/java/com/bajutsu/showcase/compose/RootScreen.kt)
