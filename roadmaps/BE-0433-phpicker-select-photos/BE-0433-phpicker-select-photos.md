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

`PHPickerViewController` is the system photo picker an iOS app presents to let a user attach an
image — a profile photo, a post's picture. Bajutsu can already drive OS-owned UI outside an app's
own screens: a SpringBoard permission prompt through `handleSystemAlert`
([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)), and the
separate-process in-app browser through a merged element tree
([BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)).
It cannot drive the photo picker at all: there is no step that selects an image, so a scenario
either stops short of that action or has to have the picked state injected from outside.

This item adds a `selectPhotos` step that picks one or more images from the picker's grid by
ordinal position and confirms the selection, plus a `seedPhotos` precondition that seeds the
Simulator's photo library with known fixture images so the grid's content is deterministic. The
picker turned out to need neither a second process handle nor a tree merge — it is measured to run
inside the host app's own process, unlike the two precedents above. A handle-based tap against a
grid cell, the same mechanism every other element uses, is measured to fail; a raw coordinate tap at
the cell's exact resolved frame center is measured to reliably select it instead. `selectPhotos`
ships using the latter, on every Simulator and device this item's `Capability.SELECT_PHOTOS` is
otherwise advertised for.

## Motivation

The gap is not a missing capability so much as a missing *step*. `permissions: { photos: grant }`
already exists (BE-0276) — `photos` is an ordinary `simctl privacy` TCC service, like `camera` or
`location` — and pre-grants the OS-level photo access an app-scoped query cannot observe. But
granting that access answers a different question from picking an image: nothing then reaches
inside the picker itself to choose one, and the picker's own grid raises no separate permission
prompt `permissions` could pre-answer even in principle — there is no OS-level consent gate between
"the app may see the library" and "which photo did the user pick", only the grid itself. A scenario
for a profile-photo or
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
  Measured against the showcase app on Xcode 26.6 / iOS 26.5, on an Apple silicon Simulator:
  resolving a grid cell by `{ id: "PXGGridLayout-Info", index: 0 }` (every cell shares that one
  identifier, so `index` is the only way to name one — see Unit 2) and tapping it through the
  driver's existing handle-based `/tap` reproducibly fails as `element vanished (stale handle)`,
  even after the driver's own stale-retry loop (`_STALE_MAX_ATTEMPTS` re-resolutions) is exhausted;
  resolving a different index the same way instead reports the handle live but refuses the tap as
  `ElementNotTappable` (`element resolved but not hittable`). A `Cancel` tap resolved and actuated
  the same way, immediately after opening the same picker, succeeds every time; only the recycled
  grid cells are affected. A raw coordinate tap at a cell's exact resolved frame center, by
  contrast, is measured to reliably select it: across all six cells of a seeded two-row grid, each
  tap selected its intended photo, and the on-screen selection count matched every time. A
  coordinate on the boundary shared with an adjacent cell, rather than the frame's own center, can
  register that neighboring cell instead. Unit 3 records the full investigation and the coordinate
  actuation `select_photos` uses.

The showcase's own scenario (`select_photos.yaml`, Unit 4) exercises Units 1 through 4 end to end
against a seeded, two-image library, verified directly on an Apple silicon Simulator. It has not
been run on a real device or an Intel Simulator, which this investigation had no access to; a later
reader with such hardware can confirm it directly by running that same scenario there and checking
that the app mirrors `Selected: 2`.

## Detailed design

### Unit 1 — Seed the Simulator's photo library

`bajutsu/common/backend_cli/simctl/_functions.py` gains `addmedia_cmd(udid: str, media_path: str)
-> list[str]`, in the same argv-builder shape as `privacy_cmd` / `push_cmd`:

```python
def addmedia_cmd(udid: str, media_path: str) -> list[str]:
    return ["xcrun", "simctl", "addmedia", validated_udid(udid), media_path]
```

One path per call, not a batch: the newest-first ordering below was measured only across *separate*
invocations, each landing before the next started, and nothing establishes the relative order
`simctl` assigns to several assets handed to one invocation — a batch call could just as well leave
two fixtures sharing one timestamp, making the grid order the invocation happened to produce, not
the order `seedPhotos` lists. `Env.add_media` (`bajutsu/common/backend_cli/simctl/env.py`) loops
over the given paths and calls `addmedia_cmd` once per path, in order, so the measured ordering is
what every caller actually gets.

Unlike `privacy` / `push`, `addmedia` is not bundle-scoped — it seeds the whole device's photo
library — and re-running it against the same paths adds duplicate library entries rather than being
a no-op. `Preconditions` (`bajutsu/common/scenario/models/scenario/preconditions.py`), not
`Scenario`, gains `seed_photos: list[str]` (YAML `seedPhotos`), beside the `erase`/`reinstall`
fields that already govern this same reset. Placing it there — rather than on `Scenario`, alongside
`permissions` — is what lets it reach `_prepare_simulator` (below) with no new plumbing: `pre:
Preconditions` is already one of that method's own parameters, where a `Scenario`-level field would
need threading through `launch_driver` and `RunEnvironment.start` the way `permissions` is
(`bajutsu/common/runner/pool.py:419`), for a value `_prepare_simulator` never otherwise needs the
full `Scenario` to read.

A non-empty `seed_photos` requires `erase: true` on the same `Preconditions`, checked by a
`model_validator` that raises at scenario-load time — loud, not a skipped seed. Without this, a
scenario that sets `seedPhotos` but not `erase` would seed nothing (the gate below never opens), no
error would surface, and `selectPhotos: { indices: [0, 1] }` would silently address whatever the
Simulator's ambient library happens to contain — the exact non-reproducible state the *Alternatives*
section rejects, just reached by omission instead of by design. Paths are resolved the same way
`dataFile` already is: relative to the scenario file's own directory, contained within the suite
root by the shared `contained_ref` choke point
(`bajutsu/common/scenario/load_expanded.py:21-45`) — the *root* is the containment boundary, not the
base a path is joined against, matching `dataFile`'s own resolution exactly. `bajutsu run`'s own
loader (`bajutsu/run/cli.py`) resolves `dataFile` and `use` refs through this identical function
rather than a separate implementation, so `seedPhotos` inherits the same containment on both entry
points without extra work.

`_prepare_simulator` (`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py:866-939`)
seeds only on the cold-and-erase path (`cold and pre.erase`) — now guaranteed non-empty-only-with-erase
by the validator above — the same path that already wipes the Simulator's prior state. Reusing it
here is what keeps re-seeding from duplicating entries on a warm-resumed lease, where `cold` is
`False` and the block does not run at all.

The picker sorts the library newest-first (measured: three fixtures added a few seconds apart, each
via its own `addmedia` invocation, appeared in reverse of their addition order — the
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

### Unit 3 — Actuate a cell by coordinate tap

`XcuitestDriver.select_photos(indices: list[int], *, timeout: float) -> None` queries `/elements`,
resolves each requested index against `{ id: "PXGGridLayout-Info", index: i }`, taps each resolved
cell's exact frame center (`base.frame_center`) by a raw coordinate `/tap`, then taps the confirm
control (below) if the picker is still up. No dedicated Swift-side endpoint: the same `/tap`
endpoint's `point` field the DSL's `tapPoint` action already uses.

The picker was presented exactly as Unit 4 specifies — `selectionLimit = 0` (unlimited), through
`UIViewControllerRepresentable` from SwiftUI. Measured against the showcase app, on Xcode 26.6, on
an Apple silicon Simulator (iOS 26.5):

| Technique | `via` | Result |
|---|---|---|
| `XCUIElement.tap()` on a handle-resolved cell | handle | `element vanished (stale handle)`, reproducible after the driver's own stale-retry loop is exhausted |
| A handle-resolved tap on a different cell (same identifier, a different `index`) | handle | The handle resolves to a live element, but the tap is refused: `ElementNotTappable` (`element resolved but not hittable`) |
| A raw coordinate tap at a cell's exact resolved frame center (`base.frame_center`) | coordinate | Selects the intended cell. Repeated across all six cells of a seeded two-row grid: every cell selected correctly, and the on-screen selection count matched the number tapped every time |
| A coordinate tap at a point on the boundary shared with an adjacent cell, rather than the frame's own center | coordinate | Selected the neighboring cell instead of the intended one |

A structurally identical handle-based tap against a *non-recycled* control — `Cancel`, in the same
picker, at the same moment — succeeds every time (Motivation), so the handle-based failure is
specific to the grid's cells, not to the picker or to XCUITest taps in general.

`select_photos` therefore taps each resolved cell's exact frame center by raw coordinate rather than
by handle, re-resolving the frame fresh before every tap in the same call: nothing about this
recycled collection view guarantees a cell's frame stays put while an earlier index in the same call
is still being tapped. `Capability.SELECT_PHOTOS` carries no restriction by Simulator host
architecture — every target this driver's static `CAPABILITIES` advertises the token for gets the
same actuation.

**What this investigation could and could not verify.** The actuation is verified directly, on an
Apple silicon Simulator: a coordinate tap at each resolved cell's exact frame center selected the
intended photo, across all six cells of a seeded two-row grid, with the on-screen selection count
matching every time. Real-device and Intel-Simulator behavior is unverified — neither was available
to this investigation. A later reader with either can confirm it directly: run `select_photos.yaml`
(Unit 4) there and check that the app mirrors `Selected: 2`.

The confirm button is resolved **structurally**, not by its label — the one button inside the
picker's navigation bar (`traits: ["navigationBar"]`, the only bar the picker presents — not named
by its own title, `Photos`, which `PHPickerViewController` localizes the same way it would localize
any label) whose identifier is not `Cancel`. Measured: the picker's dismiss control carries the
stable identifier `Cancel`, but the confirm control carries no identifier and only the label `Done`
(a checkmark glyph in this iOS version, not the word "Add"). Resolving by elimination inside the bar
itself, rather than by either control's label, needs no per-locale lookup table anywhere in the
rule — the bar is found by trait, not by its localized title, so nothing in the resolution path
reads a string that changes with the scenario's locale. Unlike SpringBoard's alert buttons, the
confirm control's identifier absence, not its label, is the stable fact. This resolution keeps the
ordinary handle-based path unchanged: a navigation-bar button is static chrome, not a recycled cell,
and the `Cancel` measurement above confirms static chrome actuates fine through it.

### Unit 4 — Capability, other backends, and the showcase fixture

`Capability.SELECT_PHOTOS = "selectPhotos"` (`bajutsu/common/drivers/base/capability.py`) is
declared by `XcuitestDriver.CAPABILITIES` (and by `FakeDriver.CAPABILITIES` for unit tests) as its
static set, carrying no run-time narrowing. It is gated through
`bajutsu/common/capability/capability_preflight.py` the same way `HANDLE_SYSTEM_ALERT` is — an
Android or web target using `selectPhotos` fails preflight with a named-capability error rather than
an opaque runtime one. `playwright_driver.py`,
`adb_driver.py`, `xcuitest_live_driver.py`, and `web_context_driver.py` each raise
`UnsupportedAction` from their own `select_photos`, matching every other iOS-only action.

The showcase's `PermissionsView.swift` (SwiftUI only; UIKit parity is out of scope — see
*Alternatives*) gains a "Photos" section: an `Open Photo Picker` button
(`perm.openPhotoPicker`) presents a `PHPickerViewController` wrapped in
`UIViewControllerRepresentable` with `selectionLimit = 0` (unlimited, so the confirm-tap path is
always exercised), and a mirrored `Text` (`perm.photos.value`) reports the picked count. Both ids
join the existing `perm` namespace — no `idNamespaces` change needed.
`demos/showcase/scenarios/fixtures/photos/` carries a handful of distinguishable fixture images
(solid colours) — inside `scenarios/`, not a sibling of it, since a `seedPhotos` ref is confined to
the suite root like every other scenario ref
([BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md)).
Seeded by `demos/showcase/scenarios/select_photos.yaml` via `preconditions: { erase: true,
seedPhotos: [...] }` (Unit 1's validator requires `erase: true` alongside `seedPhotos`), which taps
`perm.openPhotoPicker`, runs `selectPhotos: { indices: [0, 1] }`, and asserts `perm.photos.value`
equals `2`. `demos/showcase/SPEC.md` §5.4 documents the two new ids next to the section's existing
ones.

## Alternatives considered

- **Reach the grid with a generic `tap` step instead of a dedicated action.** The selector half of
  this actually works today — `{ id: "PXGGridLayout-Info", index: 0 }` is an ordinary selector, no
  new field needed. Rejected anyway, on the actuation half: the DSL's generic `tap` step actuates by
  handle, exactly the path Unit 3 measured failing against this collection view, and a scenario
  author has no way to ask it for a coordinate tap at a resolved element's frame center instead. A
  generic step cannot route to a dedicated actuation path without the DSL knowing it is addressing a
  picker cell specifically, which is what makes this a dedicated action rather than scenario-authored
  composition of existing steps.
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
