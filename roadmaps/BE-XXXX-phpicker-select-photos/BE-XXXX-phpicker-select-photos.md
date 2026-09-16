**English** · [日本語](BE-XXXX-phpicker-select-photos-ja.md)

# BE-XXXX — Select photos from PHPickerViewController

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-phpicker-select-photos.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Deferred** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) |
<!-- /BE-METADATA -->

## Introduction

`PHPickerViewController` is the system photo picker an iOS app presents to let a user attach an
image — a profile photo, a post's picture. Bajutsu can already drive OS-owned UI outside an app's
own screens: a SpringBoard permission prompt through `handleSystemAlert`
([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)), and the
separate-process in-app browser through a merged element tree
([BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)).
It cannot drive the photo picker at all: there is no step that selects an image, so a scenario
either stops short of that action or has to have the picked state injected from outside.

This item set out to add a `selectPhotos` step that picks one or more images from the picker's
grid by ordinal position and confirms the selection, plus a `seedPhotos` precondition that seeds
the Simulator's photo library with known fixture images so the grid's content is deterministic.
The picker turned out to need neither a second process handle nor a tree merge — it is measured to
run inside the host app's own process, unlike the two precedents above — but every actuation
technique tried against its grid cells failed to reliably select one, on the one Mac architecture
this investigation could test. **This item is deferred rather than implemented**: the blocking
finding is recorded in *Detailed design*, Unit 3, and the design for the rest (Units 1, 2, 4)
is kept below as the starting point for whoever picks this back up once Unit 3 has an answer.

## Motivation

The gap is not a missing capability so much as a missing *step*. `permissions: { photos: grant }`
already exists (BE-0276) and pre-grants the OS-level photo access an app-scoped query cannot
observe, but nothing then reaches inside the picker itself to choose an image — the one prompt in
this flow that pre-granting cannot answer, because there is no "photos" service to switch on with
`simctl privacy`; the picker's own grid is the entire prompt. A scenario for a profile-photo or
post-attachment screen therefore cannot exercise the path a user actually takes: open the picker,
pick a photo, confirm. It either asserts on a pre-seeded "already picked" state, which skips the
interaction under test, or stops before the picker opens.

Two further facts, both measured on the showcase app rather than assumed, decide the design:

- **The picker's grid content is not deterministic by default.** A freshly created Simulator's
  photo library already ships with several sample images (landscapes, flowers) before a scenario
  adds anything, so `indices: [0]` addresses whatever the library happens to contain unless a
  scenario seeds its own. `simctl` has no wrapper for adding media at all today —
  `bajutsu/common/backend_cli/simctl/_functions.py` wraps `privacy`/`push`/`erase`/`boot`, not
  `addmedia`.
- **The grid's cells cannot be tapped by the same handle-based path an ordinary element uses.**
  Measured against the showcase app on Xcode 26.6 / iOS 26.5: resolving a grid cell by
  `{ id: "PXGGridLayout-Info", index: 0 }` (every cell shares that one identifier, so `index` is
  the only way to name one — see Unit 2) and tapping it through the driver's existing `/tap`
  reproducibly fails as `element vanished (stale handle)`, even after the driver's own stale-retry
  loop (`_STALE_MAX_ATTEMPTS` re-resolutions) is exhausted. The picker's collection view
  invalidates a cell's accessibility node between resolution and actuation on every attempt — not
  a transient race, but a two-for-two reproducible failure. A `Cancel` tap resolved and actuated
  the same way, immediately after opening the same picker, succeeds every time; only the recycled
  grid cells are affected. This resembles the failure
  [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)
  hit with the in-app browser's chrome, where `XCUIElement.tap()` was silently dropped rather than
  reported as stale — but unlike that case, the matching fix (actuate at the element's live frame
  centre through a raw coordinate) does not carry over: tried here, it drops the tap just as
  silently and selects nothing. Unit 3 records the full investigation and why this item is
  deferred rather than shipped on this fix.

A later reader can confirm this item is unblocked by running the showcase's own scenario against
two seeded fixture images with `selectPhotos: { indices: [0, 1] }`, checking that the app mirrors
`Selected: 2`. Unit 3 records exactly what fails today so that check has a fixed target: on the
day some actuation technique passes it (a different `via`, a runtime fix, a different Mac
architecture), Units 1, 2, and 4 below need no rework to ship alongside it.

## Detailed design

### Unit 1 — Seed the Simulator's photo library

`bajutsu/common/backend_cli/simctl/_functions.py` gains `addmedia_cmd(udid: str, media_paths:
Sequence[str]) -> list[str]`, in the same argv-builder shape as `privacy_cmd` / `push_cmd`:

```python
def addmedia_cmd(udid: str, media_paths: Sequence[str]) -> list[str]:
    return ["xcrun", "simctl", "addmedia", validated_udid(udid), *media_paths]
```

Unlike `privacy` / `push`, `addmedia` is not bundle-scoped — it seeds the whole device's photo
library — and re-running it against the same paths adds duplicate library entries rather than being
a no-op. The `Scenario` model (`bajutsu/common/scenario/models/scenario/scenario.py`) gains
`seed_photos: list[str]` (YAML `seedPhotos`), a list of paths resolved relative to the suite root
through the same `contained_ref` choke point `dataFile` already uses
(`bajutsu/common/scenario/load_expanded.py`), so a scenario cannot seed a path outside its suite.
`_prepare_simulator` (`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py:866-939`)
seeds only on the cold-and-erase path (`cold and pre.erase`), the same path that already wipes the
Simulator's prior state — reusing it here is what keeps re-seeding from duplicating entries on a
warm-resumed or non-erased lease.

The picker sorts the library newest-first (measured: three fixtures added a few seconds apart, via
three separate `addmedia` invocations, appeared in reverse of their addition order — the
most-recently-added fixture at index 0), *ahead of* the Simulator's own pre-installed sample
images. A scenario's `indices` therefore address the seeded fixtures in the *reverse* of the order
`seedPhotos` lists them, which the DSL reference states explicitly rather than leaving to be
discovered from behaviour.

### Unit 2 — The `selectPhotos` DSL action

```yaml
- selectPhotos:
    indices: [0, 1]
    timeout: 10
```

`bajutsu/common/scenario/models/actions/select_photos.py` defines `SelectPhotos` (`indices:
list[int]`, `timeout: float`), validated the same way `HandleSystemAlert` validates its own shape
(`@model_validator(mode="after")`): `indices` must be non-empty, non-negative, and duplicate-free.
No `sel` field is needed — a cell carries no author-assignable identifier for a scenario to name,
only its ordinal grid position, addressed the same way `handleSystemAlert` addresses a SpringBoard
button it cannot label either.

Every cell in the grid shares one identifier, `PXGGridLayout-Info` (measured; it is not a
per-cell value), so the *existing*, already-general `index` field on `Selector`
(`bajutsu/common/scenario/models/selector.py`, consumed by `resolve_unique` in
`bajutsu/common/drivers/base/_functions.py:321`) is what disambiguates one cell from the others —
the same "nth of multiple matches" mechanism `handleSystemAlert` already relies on for SpringBoard
buttons. No new selector field is needed; `select_photos`'s driver method builds an ordinary
`{ id: "PXGGridLayout-Info", index: i }` selector per requested index and resolves it through the
existing `/elements` query — the grid did not need a dedicated query endpoint, only a dedicated way
to *tap* what that query already finds (Unit 3).

### Unit 3 — Actuate a cell reliably (blocked)

The planned shape of `XcuitestDriver.select_photos(indices: Sequence[int], *, timeout: float) ->
None` was: query `/elements`, resolve each requested index against `{ id: "PXGGridLayout-Info",
index: i }`, tap each resolved cell, then decide whether a confirm tap is needed by re-querying
`/elements` and checking whether the picker is still up. Every actuation technique tried for the
middle step — tapping the resolved cell — failed to select anything, against the showcase app, on
Xcode 26.6, on the one Mac this investigation had access to (Apple silicon, an M-series chip):

| Technique | `via` | Result |
|---|---|---|
| `XCUIElement.tap()` on the handle-resolved cell | handle | `element vanished (stale handle)`, reproducible after the driver's own stale-retry loop is exhausted |
| `XCUIElement.press(forDuration:)` on the same handle, 0.05 s and 0.4 s | handle | Same `stale handle` failure at both durations |
| A raw coordinate tap at the cell's live frame centre (the existing `/tap` endpoint's `point` field — already used by the DSL's `tapPoint` action, so no new endpoint was even needed for this attempt) | coordinate | No error, but no cell is marked selected either — the tap is accepted and does nothing observable |
| A coordinate *press* at the same point, 0.15 s (`XCUICoordinate.press(forDuration:)` — the same primitive [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md) uses for the browser's frame-centre tap, extended to `tapPoint` and prototyped for this check) | coordinate | Same as the plain coordinate tap: accepted, no cell marked selected |

The first three attempts were repeated on both an iOS 26.5 Simulator and an iOS 18.6 Simulator with
identical results, which rules out an iOS-version regression as the cause. A structurally identical
tap against a *non-recycled* control — `Cancel`, in the same picker, at the same moment — succeeds
every time by the plain handle-based path (Motivation), so whatever is failing is specific to the
grid's cells, not to the picker or to XCUITest taps in general.

This matches a limitation independently reported on Apple's own developer forums: image selection
inside `UIImagePickerController` / `PHPickerViewController` failing to register under XCUITest
specifically on Apple silicon Simulators, while working on Intel Simulators and real devices
([Apple Developer Forums, thread 714024](https://developer.apple.com/forums/thread/714024);
[Bitrise Discussions, "Cannot pick image during
XCUITest"](https://discuss.bitrise.io/t/cannot-pick-image-during-xcuitest/14427)). Bajutsu retired
its one alternative iOS actuator, `idb`, in
[BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md); XCUITest is the sole
route left to drive an iOS Simulator, so there is no existing fallback path within the tool to
route around this.

**This is why the item is deferred rather than implemented.** Determinism (prime directive 2) rules
out shipping a step whose one essential action does not work on the architecture most contributors
now run — an Apple silicon Mac. The fourth attempt closes off the most obvious remaining avenue:
[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)'s
own fix, generalized from a tap to a press, still does not reach this collection view. Resuming
this item needs one of: a fix or documented workaround from Apple, a still-untried actuation
technique that does register a selection, or a deliberate decision to scope `selectPhotos` to real
devices / Intel Simulators only, none of which this investigation found.

The confirm-button half of the plan is unaffected by this and is kept for whoever resumes the
item: resolve it **structurally**, not by its label — the one button inside the `Photos` navigation
bar (`traits: ["navigationBar"]`) whose identifier is not `Cancel`. Measured: the picker's dismiss
control carries the stable identifier `Cancel`, but the confirm control carries no identifier and
only the label `Done` (a checkmark glyph in this iOS version, not the word "Add"). Resolving by
elimination rather than by that label needs no per-locale lookup table — unlike SpringBoard's alert
buttons, this control's identifier absence, not its label, is the stable fact. It is also
unaffected by the Unit 3 blocker above: a navigation-bar button is static chrome, not a recycled
cell, and the `Cancel` measurement already confirms static chrome actuates fine through the
ordinary handle-based path.

### Unit 4 — Capability, other backends, and the showcase fixture

`Capability.SELECT_PHOTOS = "selectPhotos"` (`bajutsu/common/drivers/base/capability.py`) is
declared only by `XcuitestDriver.CAPABILITIES` (and by `FakeDriver.CAPABILITIES` for unit tests),
gated through `bajutsu/common/capability/capability_preflight.py` the same way
`HANDLE_SYSTEM_ALERT` is — an Android or web target using `selectPhotos` fails preflight with a
named-capability error rather than an opaque runtime one. `playwright_driver.py`, `adb_driver.py`,
`xcuitest_live_driver.py`, and `web_context_driver.py` each raise `UnsupportedAction` from their own
`select_photos`, matching every other iOS-only action.

The showcase's `PermissionsView.swift` (SwiftUI only; UIKit parity is out of scope — see
*Alternatives*) gains a "Photos" section: an `Open Photo Picker` button
(`perm.openPhotoPicker`) presents a `PHPickerViewController` wrapped in
`UIViewControllerRepresentable` with `selectionLimit = 0` (unlimited, so the confirm-tap path is
always exercised), and a mirrored `Text` (`perm.photos.value`) reports the picked count. Both ids
join the existing `perm` namespace — no `idNamespaces` change needed. `demos/showcase/fixtures/photos/`
carries a handful of distinguishable fixture images (solid colours), seeded by
`demos/showcase/scenarios/select_photos.yaml` via `seedPhotos`, which taps `perm.openPhotoPicker`,
runs `selectPhotos: { indices: [0, 1] }`, and asserts `perm.photos.value` equals `2`. `demos/showcase/SPEC.md`
§5.4 documents the two new ids next to the section's existing ones.

## Alternatives considered

- **Reach the grid with a generic `tap` step instead of a dedicated action.** The selector half of
  this actually works today — `{ id: "PXGGridLayout-Info", index: 0 }` is an ordinary selector, no
  new field needed. Rejected anyway, on the actuation half: the same generic `/tap` a scenario
  author would have to name explicitly is exactly the path Unit 3 measured failing against this
  collection view. A generic step cannot route to a dedicated actuation path without the DSL
  knowing it is addressing a picker cell specifically, which is what makes this a dedicated action
  rather than scenario-authored composition of existing steps.
- **Treat the picker like SpringBoard or SafariViewService: a second `XCUIApplication` handle, a
  separate query endpoint, a separate handle store.** Measured unnecessary — no separate process
  ever appears in `launchctl list` while the picker is presented, and the app's own `/elements`
  already reports every cell. Building the separate-process machinery anyway would add a second
  `SnapshotStore`, handle-collision avoidance, and a bundle identifier to discover, for a process
  boundary that does not exist here.
- **Resolve the confirm control by its label, `Done`, the way `handleSystemAlert` resolves a
  SpringBoard button by locale-keyed label.** The control carries no identifier, so a label lookup
  was the first candidate. Rejected in favor of structural elimination (Unit 3) because the control
  sits beside `Cancel`, which *does* carry a stable identifier — excluding it needs no label, no
  locale table, and survives whatever the label reads in another language, at the cost of only
  reconfirming (not redesigning) the rule if a future iOS version identifies a third button in that
  bar.
- **Skip `seedPhotos` and let a scenario rely on whatever the Simulator's library already
  contains.** The library ships non-empty by default (sample images) but that content is not this
  project's fixture, is not guaranteed stable across Xcode/Simulator runtime versions, and is not
  under version control — an `indices` reference to it would not be reproducible across machines or
  CI runs, violating determinism (prime directive 2) outright rather than merely being inconvenient.
- **UIKit showcase parity in the same item.** Deferred, not rejected: the SwiftUI screen is enough
  to exercise and document every unit above, and `PHPickerViewController` is a UIKit type either
  way, so a UIKit-hosted equivalent adds no new driver-side coverage — only a second fixture screen
  duplicating the same wiring.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

**Blocked on Unit 3** (see *Detailed design*): no unit below has landed. Units 1, 2, and 4 are
buildable independently of the blocker, but shipping them with no working `selectPhotos` actuation
would land a DSL surface that parses and then fails every run — worse than not shipping it, so
none has been merged either. All four wait on Unit 3.

- [ ] Unit 1 — `addmedia_cmd`, the `seedPhotos` precondition, and its `cold`-and-`erase`-gated
  seeding in `_prepare_simulator`.
- [ ] Unit 2 — the `SelectPhotos` DSL action and its validation.
- [ ] Unit 3 — a working, reliable actuation for a picker grid cell (blocked — see *Detailed
  design*) and the structural confirm-button resolution.
- [ ] Unit 4 — the `SELECT_PHOTOS` capability, the other backends' `UnsupportedAction`, and the
  showcase fixture (`PermissionsView.swift`, fixture images, `select_photos.yaml`, `SPEC.md`).

Log:

- 2026-09-16 — investigated Unit 3 against the showcase app (Xcode 26.6, iOS 26.5 and iOS 18.6
  Simulators, Apple silicon Mac); every actuation technique tried, including a fourth prototyped
  after the first pass (a coordinate press, `tapPoint`'s `duration` extended and tested, then
  reverted since it did not unblock the item), failed to select a cell. Deferred the item rather
  than shipping the other three units alone. No PR: nothing merges from an item with no working
  core mechanism.

## References

- `bajutsu/common/scenario/models/actions/handle_system_alert.py` — the action-shape and
  locale-table precedent this item's Unit 2 and Unit 3 each partly follow and partly depart from.
- `bajutsu/common/drivers/base/_functions.py` — `resolve_unique`'s existing `index` handling
  (line 321), which this item reuses rather than extends.
- [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)
  — the frame-centre actuation and `Tappable` mechanism Unit 3 tried, and why it does not carry
  over to the picker's cells.
- [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) — the
  retirement of `idb`, the one alternative iOS actuator that might have routed around Unit 3's
  blocker; XCUITest is the only one left.
- [Apple Developer Forums, thread 714024](https://developer.apple.com/forums/thread/714024) and
  [Bitrise Discussions, "Cannot pick image during
  XCUITest"](https://discuss.bitrise.io/t/cannot-pick-image-during-xcuitest/14427) — independent
  reports of the same class of failure (image-picker selection not registering under XCUITest on
  Apple silicon Simulators), consistent with what Unit 3 measured here.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) — the
  ordinal, label-free button addressing this item's cell indexing follows.
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) — the
  `permissions:` pre-grant that answers every OS prompt this flow raises *except* the picker's own
  grid, which is this item's subject.
