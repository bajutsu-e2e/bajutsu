**English** · [日本語](BE-0433-phpicker-select-photos-ja.md)

# BE-0433 — Select photos from PHPickerViewController

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0433](BE-0433-phpicker-select-photos.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0433") |
| Implementing PR | [#2008](https://github.com/bajutsu-e2e/bajutsu/pull/2008) |
| Topic | Platform support |
| Related | [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md) |
<!-- /BE-METADATA -->

## Introduction

`PHPickerViewController` is the system photo picker. An iOS app presents it to let a user attach an
image, such as a profile photo or the picture of a post. Bajutsu can already drive OS-owned UI outside
an app's own screens:

- A SpringBoard permission prompt, through `handleSystemAlert`
  ([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)).
- The separate-process in-app browser, through a merged element tree
  ([BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)).

Bajutsu cannot drive the photo picker at all. No step selects an image. A
[scenario](../../docs/glossary.md#scenario-authoring) either stops short of that action or
needs the picked state injected from outside.

This item adds two features:

- A `selectPhotos` step picks one or more images from the picker's grid by ordinal position and
  confirms the selection.
- A `seedPhotos` precondition seeds the Simulator's photo library with known fixture images. The
  grid's content becomes deterministic.

The picker needs neither a second process handle nor a tree merge. Measurement shows that it runs
inside the host app's own process, unlike the two precedents above.

Measurement also shows two results about tapping a grid cell:

- A handle-based tap fails. A handle-based tap is the same mechanism every other element uses.
- A raw coordinate tap at the cell's exact resolved frame center reliably selects the cell.

`selectPhotos` ships using the coordinate tap. It does so on every Simulator and device for which this
item's `Capability.SELECT_PHOTOS` is advertised.

## Motivation

The gap is less a missing capability than a missing *step*. `permissions: { photos: grant }` already
exists (BE-0276). `photos` is an ordinary `simctl privacy` Transparency, Consent, and Control (TCC)
service, like `camera` or `location`. The setting pre-grants the OS-level photo access that an
app-scoped query cannot observe.

Granting that access answers a different question from picking an image. Nothing then reaches inside
the picker itself to choose one. The picker's grid also raises no separate permission prompt that
`permissions` could pre-answer, even in principle. No OS-level consent gate sits between "the app may
see the library" and "which photo did the user pick". Only the grid itself sits there.

A scenario for a profile-photo or post-attachment screen cannot exercise the path a user
takes: open the picker, pick a photo, confirm. The scenario either asserts on a pre-seeded "already
picked" state, which skips the interaction under test, or stops before the picker opens.

Two further facts decide the design. Both were measured on the showcase app, not assumed.

- **The picker's grid content is not deterministic by default.** A freshly created Simulator's photo
  library already ships with several sample images (landscapes, flowers) before a scenario adds
  anything. Unless a scenario seeds its own images, `indices: [0]` addresses whatever the library
  happens to contain. `simctl` also has no wrapper for adding media today.
  `bajutsu/common/backend_cli/simctl/_functions.py` wraps `privacy`, `push`, `erase`, and `boot`, not
  `addmedia`.
- **The grid's cells cannot be tapped by the handle-based path that an ordinary element uses.**
  The measurements ran against the showcase app on Xcode 26.6 and iOS 26.5, on an Apple silicon
  Simulator:
  - Every cell shares one identifier, so `index` is the only way to name a cell (see Unit 2).
    Resolving a grid cell by `{ id: "PXGGridLayout-Info", index: 0 }` and tapping it through the
    driver's existing handle-based `/tap` reproducibly fails as `element vanished (stale handle)`.
    The failure persists after the driver's own stale-retry loop (`_STALE_MAX_ATTEMPTS`
    re-resolutions) is exhausted.
  - Resolving a different index the same way reports the handle live but refuses the tap as
    `ElementNotTappable` (`element resolved but not hittable`).
  - A `Cancel` tap, resolved and actuated the same way right after the same picker opens, succeeds
    every time. Only the recycled grid cells are affected.
  - A raw coordinate tap at a cell's exact resolved frame center reliably selects the cell. The
    measurement covered all six cells of a seeded two-row grid. Each tap selected its intended
    photo, and the on-screen selection count matched every time.
  - A coordinate on the boundary shared with an adjacent cell, instead of the frame's own center, can
    register that neighboring cell.

  Unit 3 records the full investigation and the coordinate actuation that `select_photos` uses.

The showcase's own scenario (`select_photos.yaml`, Unit 4) exercises Units 1 through 4 end to end
against a seeded, two-image library. It was verified directly on an Apple silicon Simulator. It has
not run on a real device or an Intel Simulator, because this investigation had no access to either. A
later reader with such hardware can confirm the result by running the same scenario there and checking
that the app mirrors `Selected: 2`.

## Detailed design

### Unit 1 — Seed the Simulator's photo library

`bajutsu/common/backend_cli/simctl/_functions.py` gains `addmedia_cmd(udid: str, media_path: str)
-> list[str]`. It has the same argv-builder shape as `privacy_cmd` and `push_cmd`:

```python
def addmedia_cmd(udid: str, media_path: str) -> list[str]:
    return ["xcrun", "simctl", "addmedia", validated_udid(udid), media_path]
```

The command takes one path per call, not a batch. The newest-first ordering below was measured only
across *separate* invocations, each landing before the next started. Nothing establishes the relative
order that `simctl` assigns to several assets handed to one invocation. A batch call could leave two
fixtures sharing one timestamp. The grid order would then be whatever the invocation happened to
produce, not the order that `seedPhotos` lists.

`Env.add_media` (`bajutsu/common/backend_cli/simctl/env.py`) loops over the given paths and calls
`addmedia_cmd` once per path, in order. Every caller gets the measured ordering.

Unlike `privacy` and `push`, `addmedia` is not bundle-scoped: it seeds the whole device's photo
library. Re-running it against the same paths adds duplicate library entries rather than being a
no-op.

`Preconditions` (`bajutsu/common/scenario/models/scenario/preconditions.py`), not `Scenario`, gains
`seed_photos: list[str]` (YAML `seedPhotos`). The field sits beside the `erase` and `reinstall`
fields that already govern this same reset.

Placing the field there is what lets it reach `_prepare_simulator` (below) with no new plumbing.
`pre: Preconditions` is already one of that method's own parameters. A `Scenario`-level field,
alongside `permissions`, would need threading through `launch_driver` and `RunEnvironment.start`
(`bajutsu/common/runner/pool.py:419`). `_prepare_simulator` never otherwise needs the full `Scenario`
to read such a value.

A non-empty `seed_photos` requires `erase: true` on the same `Preconditions`. A `model_validator`
checks this at scenario-load time and raises, so the failure is loud and never a skipped seed.

Without the check, a scenario that sets `seedPhotos` but not `erase` would seed nothing, because the
gate below never opens. No error would surface. `selectPhotos: { indices: [0, 1] }` would then
silently address whatever the Simulator's ambient library happens to contain. This is the exact
non-reproducible state that the *Alternatives* section rejects, reached by omission instead of by
design.

Paths resolve the same way `dataFile` already does:

- A path resolves from the scenario file's own directory.
- The shared `contained_ref` choke point confines the path within the suite root
  (`bajutsu/common/scenario/load_expanded.py:21-45`).
- The *root* is the containment boundary, not the base that a path is joined against. This matches
  `dataFile`'s own resolution exactly.

`bajutsu run`'s own loader (`bajutsu/run/cli.py`) resolves `dataFile` and `use` refs through this
identical function, not a separate implementation. `seedPhotos` inherits the same
containment on both entry points without extra work.

`_prepare_simulator`
(`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py:866-939`) seeds only
on the cold-and-erase path (`cold and pre.erase`). The validator above guarantees that `seed_photos`
is non-empty only together with `erase`. The same path already wipes the Simulator's prior state.
Reusing it keeps re-seeding from duplicating entries on a warm-resumed lease, where `cold` is `False`
and the block does not run at all.

The picker sorts the library newest-first. The measurement added three fixtures a few seconds apart,
each through its own `addmedia` invocation. They appeared in the reverse of their addition order, with
the most recently added fixture at index 0. The seeded fixtures sit *ahead of* the Simulator's own
pre-installed sample images.

A scenario's `indices` address the seeded fixtures in the *reverse* of the order in which
`seedPhotos` lists them. The DSL reference states this explicitly, so no author has to discover it
from behavior.

### Unit 2 — The `selectPhotos` DSL action

```yaml
- selectPhotos:
    indices: [0, 1]
    timeout: 10
```

`bajutsu/common/scenario/models/actions/select_photos.py` defines `SelectPhotos` (`indices:
list[int]`, `timeout: float`). It validates its shape the same way `HandleSystemAlert` does
(`@model_validator(mode="after")`): `indices` must be non-empty, non-negative, and duplicate-free.

The action needs no `sel` field. A cell carries no author-assignable identifier for a scenario to
name. It has only its ordinal grid position. `handleSystemAlert` addresses a SpringBoard button it
cannot label either in the same way.

Every cell in the grid shares one identifier, `PXGGridLayout-Info`. The measurement showed that the
identifier is not a per-cell value. The *existing*, already-general `index` field on `Selector`
therefore disambiguates one cell from the others. The field lives in
`bajutsu/common/scenario/models/selector.py`, and `resolve_unique` consumes it in
`bajutsu/common/drivers/base/_functions.py:321`. It is the same "nth of multiple matches" mechanism
that `handleSystemAlert` already relies on for SpringBoard buttons.

No new selector field is needed. The driver method of `select_photos` builds an ordinary
`{ id: "PXGGridLayout-Info", index: i }` selector per requested index. It resolves each selector
through the existing `/elements` query. The grid did not need a dedicated query endpoint. It needed
a dedicated way to *tap* what that query already finds (Unit 3).

### Unit 3 — Actuate a cell by coordinate tap

`XcuitestDriver.select_photos(indices: list[int], *, timeout: float) -> None` runs these steps:

1. It queries `/elements`.
2. It resolves each requested index against `{ id: "PXGGridLayout-Info", index: i }`.
3. It taps each resolved cell's exact frame center (`base.frame_center`) by a raw coordinate `/tap`.
4. It taps the confirm control (below) if the picker is still up.

The method needs no dedicated Swift-side endpoint. It uses the `point` field of the same `/tap`
endpoint that the DSL's `tapPoint` action already uses.

The measurements presented the picker exactly as Unit 4 specifies: `selectionLimit = 0` (unlimited),
through `UIViewControllerRepresentable` from SwiftUI. They ran against the showcase app on Xcode 26.6,
on an Apple silicon Simulator (iOS 26.5):

| Technique | `via` | Result |
|---|---|---|
| `XCUIElement.tap()` on a handle-resolved cell | handle | `element vanished (stale handle)`, reproducible after the driver's own stale-retry loop is exhausted |
| A handle-resolved tap on a different cell (same identifier, a different `index`) | handle | The handle resolves to a live element, but the tap is refused: `ElementNotTappable` (`element resolved but not hittable`) |
| A raw coordinate tap at a cell's exact resolved frame center (`base.frame_center`) | coordinate | Selects the intended cell. Repeated across all six cells of a seeded two-row grid: every cell selected correctly, and the on-screen selection count matched the number tapped every time |
| A coordinate tap at a point on the boundary shared with an adjacent cell, rather than the frame's own center | coordinate | Selected the neighboring cell instead of the intended one |

A structurally identical handle-based tap against a *non-recycled* control succeeds every time. The
control is `Cancel`, in the same picker, at the same moment (see Motivation). The handle-based failure
is specific to the grid's cells. It does not extend to the picker or to XCUITest taps in
general.

`select_photos` taps each resolved cell's exact frame center by raw coordinate, not by handle. It
re-resolves the frame fresh before every tap in the same call. Nothing about this recycled collection
view guarantees that a cell's frame stays put while an earlier index in the same call is still being
tapped.

`Capability.SELECT_PHOTOS` carries no restriction by Simulator host architecture. Every target for
which this driver's static `CAPABILITIES` advertises the token gets the same actuation.

**What this investigation could and could not verify.** The actuation is verified directly, on an
Apple silicon Simulator. A coordinate tap at each resolved cell's exact frame center selected the
intended photo. The test covered all six cells of a seeded two-row grid, and the on-screen selection
count matched every time. Real-device and Intel-Simulator behavior is unverified, because neither was
available to this investigation. A later reader with either can confirm the behavior directly: run
`select_photos.yaml` (Unit 4) there and check that the app mirrors `Selected: 2`.

The confirm button is resolved **structurally**, not by its label. It is the one button inside the
picker's navigation bar (`traits: ["navigationBar"]`) whose identifier is not `Cancel`. The picker
presents no other bar. The rule does not name the bar by its title, `Photos`, because
`PHPickerViewController` localizes that title the same way it would localize any label.

The measurement found this about the two controls:

- The picker's dismiss control carries the stable identifier `Cancel`.
- The confirm control carries no identifier. It carries only the label `Done`, which is a checkmark
  glyph in this iOS version, not the word "Add".

Resolving by elimination inside the bar, rather than by either control's label, needs no per-locale
lookup table anywhere in the rule. The rule finds the bar by trait, not by its localized title.
Nothing in the resolution path reads a string that changes with the scenario's locale. Unlike
SpringBoard's alert buttons, the stable fact about the confirm control is the absence of its
identifier, not its label.

This resolution keeps the ordinary handle-based path unchanged. A navigation-bar button is static
chrome, not a recycled cell. The `Cancel` measurement above confirms that static chrome actuates fine
through that path.

### Unit 4 — Capability, other backends, and the showcase fixture

`Capability.SELECT_PHOTOS = "selectPhotos"` (`bajutsu/common/drivers/base/capability.py`) is declared
by `XcuitestDriver.CAPABILITIES` as a static set, and by `FakeDriver.CAPABILITIES` for unit tests. It
carries no run-time narrowing.

`bajutsu/common/capability/capability_preflight.py` gates the capability the same way as
`HANDLE_SYSTEM_ALERT`. An Android or web target that uses `selectPhotos` fails preflight with a
named-capability error rather than an opaque runtime one. `playwright_driver.py`, `adb_driver.py`,
`xcuitest_live_driver.py`, and `web_context_driver.py` each raise `UnsupportedAction` from their own
`select_photos`. This matches every other iOS-only action.

The showcase's `PermissionsView.swift` gains a "Photos" section. Only SwiftUI is covered, and UIKit
parity is out of scope (see *Alternatives*). The section holds two elements:

- An `Open Photo Picker` button (`perm.openPhotoPicker`) presents a `PHPickerViewController` wrapped
  in `UIViewControllerRepresentable` with `selectionLimit = 0`. The limit is unlimited, so the
  confirm-tap path is always exercised.
- A mirrored `Text` (`perm.photos.value`) reports the picked count.

Both ids join the existing `perm` namespace, so no `idNamespaces` change is needed.

`demos/showcase/scenarios/fixtures/photos/` carries a handful of distinguishable fixture images (solid
colors). The directory sits inside `scenarios/`, not beside it, because a `seedPhotos` ref is confined
to the suite root like every other scenario ref
([BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md)).

`demos/showcase/scenarios/select_photos.yaml` seeds them via `preconditions: { erase: true,
seedPhotos: [...] }`. Unit 1's validator requires `erase: true` alongside `seedPhotos`. The scenario
taps `perm.openPhotoPicker`, runs `selectPhotos: { indices: [0, 1] }`, and asserts that
`perm.photos.value` equals `2`. `demos/showcase/SPEC.md` §5.4 documents the two new ids next to the
section's existing ones.

## Alternatives considered

- **Reach the grid with a generic `tap` step instead of a dedicated action.** The selector half works
  today: `{ id: "PXGGridLayout-Info", index: 0 }` is an ordinary selector, and no new field is needed.
  The alternative is rejected on the actuation half. The DSL's generic `tap` step actuates by handle,
  which is exactly the path that Unit 3 measured failing against this collection view. A scenario
  author has no way to ask that step for a coordinate tap at a resolved element's frame center. A
  generic step cannot route to a dedicated actuation path unless the DSL knows that it addresses a
  picker cell. That requirement is what makes this a dedicated action, not a scenario-authored
  composition of existing steps.
- **Treat the picker like SpringBoard or SafariViewService: a second `XCUIApplication` handle, a
  separate query endpoint, a separate handle store.** Measurement shows that the separate-process machinery is unnecessary. No
  separate process ever appears in `launchctl list` while the picker is presented, and the app's own
  `/elements` already reports every cell. Building the separate-process machinery anyway would add a
  second `SnapshotStore`, handle-collision avoidance, and a bundle identifier to discover. All of it
  would serve a process boundary that does not exist here.
- **Resolve the confirm control by its label, `Done`, the way `handleSystemAlert` resolves a
  SpringBoard button by locale-keyed label.** The control carries no identifier, so a label lookup was
  the first candidate. Structural elimination (Unit 3) replaces it. The control sits beside `Cancel`,
  which *does* carry a stable identifier. Excluding `Cancel` needs no label and no locale table, and
  it survives whatever the label reads in another language. The cost is small: if a future iOS version
  identifies a third button in that bar, the rule needs reconfirming, not redesigning.
- **Skip `seedPhotos` and let a scenario rely on whatever the Simulator's library already
  contains.** The library ships non-empty by default (sample images). That content is not this
  project's fixture, is not guaranteed stable across Xcode and Simulator runtime versions, and is not
  under version control. An `indices` reference to it would not be reproducible across machines or CI
  runs. That irreproducibility violates determinism (prime directive 2) outright, beyond merely
  being inconvenient.
- **UIKit showcase parity in the same item.** Deferred, not rejected. The SwiftUI screen suffices to
  exercise and document every unit above. `PHPickerViewController` is a UIKit type either way, so a
  UIKit-hosted equivalent adds no new driver-side coverage. It would add only a second fixture screen
  that duplicates the same wiring.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `addmedia_cmd`, the `seedPhotos` precondition, and its `cold`-and-`erase`-gated
  seeding in `_prepare_simulator`.
- [x] Unit 2 — the `SelectPhotos` DSL action and its validation.
- [x] Unit 3 — coordinate-tap actuation for a picker grid cell (see *Detailed design*), and the
  structural confirm-button resolution.
- [x] Unit 4 — the `SELECT_PHOTOS` capability, the other backends' `UnsupportedAction`, and the
  showcase fixture (`PermissionsView.swift`, fixture images, `select_photos.yaml`, `SPEC.md`).

Log:

- 2026-09-16 — investigated Unit 3 against the showcase app (Xcode 26.6, iOS 26.5 and iOS 18.6
  Simulators, Apple silicon Mac); every actuation technique tried, including a fourth prototyped
  after the first pass (a coordinate press, `tapPoint`'s `duration` extended and tested, then
  reverted since it did not unblock the item), failed to select a cell. Deferred the item rather
  than shipping the other three units alone. No PR: nothing merges from an item with no working
  core mechanism.
- 2026-09-24 — resolved Unit 3 by scoping rather than by finding a new actuation technique:
  `selectPhotos` shipped using the ordinary handle-based tap every other element uses, gated off an
  Apple silicon Simulator by `capabilities_for_run`. Implemented all four units, the showcase
  fixture, and their tests; `make check` green.
- 2026-09-25 — superseded the previous day's scoping: a manual, interactive re-investigation of
  Unit 3 found that a coordinate tap at a grid cell's exact resolved frame center reliably selects
  it, measured across all six cells of the showcase's seeded grid on the same Apple silicon
  Simulator the earlier investigation used, with the on-screen selection count matching every time.
  The prior attempt's coordinate tap had not targeted the frame's exact center. Replaced the
  handle-based tap in `XcuitestDriver.select_photos` with a coordinate tap at the resolved frame
  center, and removed the Apple-silicon-Simulator capability narrowing
  (`apple_silicon_simulator_host`, `capabilities_for_run`) — `Capability.SELECT_PHOTOS` no longer
  restricts by Simulator host architecture. `make check` green.

## References

- `bajutsu/common/scenario/models/actions/handle_system_alert.py` — the action-shape and
  locale-table precedent this item's Unit 2 and Unit 3 each partly follow and partly depart from.
- `bajutsu/common/drivers/base/_functions.py` — `resolve_unique`'s existing `index` handling
  (line 321), and `frame_center` (line 362), both of which this item reuses rather than extends.
- [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)
  — the frame-center coordinate-tap mechanism this item's Unit 3 actuation reuses.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) — the
  ordinal, label-free button addressing this item's cell indexing follows.
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) — the
  `permissions:` pre-grant that answers every OS prompt this flow raises *except* the picker's own
  grid, which is this item's subject.
- [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md) — the
  preflight check that rejects a `selectPhotos` step up front on a backend lacking
  `SELECT_PHOTOS`.
