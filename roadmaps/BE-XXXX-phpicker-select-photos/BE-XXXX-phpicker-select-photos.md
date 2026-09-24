**English** · [日本語](BE-XXXX-phpicker-select-photos-ja.md)

# BE-XXXX — Select photos from PHPickerViewController

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-phpicker-select-photos.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2008](https://github.com/bajutsu-e2e/bajutsu/pull/2008) |
| Topic | Platform support |
| Related | [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md) |
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
inside the host app's own process, unlike the two precedents above — but every actuation technique
tried against its grid cells on an Apple silicon Simulator failed to reliably select one. That
finding, recorded in full in *Detailed design*, Unit 3, does not rule the action out: it is
independently reported as an Apple-silicon-Simulator-specific limitation, so `selectPhotos` ships
scoped to where the ordinary handle-based tap every other element already uses is expected to
work — a real device or an Intel Simulator — and raises a named capability error everywhere else,
including on an Apple silicon Simulator, rather than failing non-deterministically on-device.

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
  silently and selects nothing. Unit 3 records the full investigation and the capability scoping
  it decides.

The showcase's own scenario (`select_photos.yaml`, Unit 4) exercises Units 1, 2, and 4 against a
seeded, two-image library on every Simulator, and exercises Unit 3's capability rejection on the
one this investigation's Mac carries — an Apple silicon host. It cannot exercise Unit 3's actuation
succeeding, since that needs hardware (a real device, or an Intel Mac) this investigation had no
access to; a later reader with such hardware can confirm it directly by running that same scenario
there and checking that the app mirrors `Selected: 2`.

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

### Unit 3 — Actuate a cell, scoped to where it works

`XcuitestDriver.select_photos(indices: list[int], *, timeout: float) -> None` queries `/elements`,
resolves each requested index against `{ id: "PXGGridLayout-Info", index: i }`, taps each resolved
cell through the ordinary handle-based `/tap` every other element uses, then taps the confirm
control (below) if the picker is still up. No dedicated actuation primitive and no new Swift-side
endpoint: the same mechanism the DSL's existing `tap` action already uses. This is only sound
because of the scoping the rest of this unit derives — building it on a mechanism proven broken
against this specific view would repeat the earlier investigation's mistake, not fix it.

The picker was presented exactly as Unit 4 specifies — `selectionLimit = 0` (unlimited), through
`UIViewControllerRepresentable` from SwiftUI. Every actuation technique tried against a grid cell,
on Xcode 26.6, on the one Mac this investigation had access to (Apple silicon, an M-series chip),
failed to select anything:

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

Determinism (prime directive 2) rules out shipping a step whose one essential action does not work
on the architecture most contributors now run — an Apple silicon Mac — with no way for a scenario
author to know that up front. The fourth attempt closes off the most obvious remaining avenue:
[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)'s
own fix, generalized from a tap to a press, still does not reach this collection view. Rather than
wait on a fix or a workaround from Apple, or a still-untried actuation technique, this item scopes
`selectPhotos` to where the ordinary tap mechanism is corroborated to work — a real device or an
Intel Simulator — and rejects the scenario up front everywhere else, the same fail-fast promise
`capabilities_for_run` already makes for a real device's missing simctl-backed capabilities
(BE-0238 Unit 3).

`backends.apple_silicon_simulator_host() -> bool` (`bajutsu/common/backends.py`) reads
`platform.machine() == "arm64"` on the machine running bajutsu itself — not a `xcrun simctl` query
against the target device. The two are the same fact: a Simulator's guest OS runs natively on the
host CPU rather than emulating a separate one, so "an Apple silicon Simulator" and "a Simulator on
an Apple silicon Mac" name the same thing, and a plain host-architecture read is free of the
device-fault flakiness a `simctl` call would risk (prime directive 2). `capabilities_for_run`
(already the run-time narrowing point for a real device, BE-0238) drops `SELECT_PHOTOS` from the
static `XcuitestDriver.CAPABILITIES` set whenever the target is a Simulator (not a real device) on
an Apple silicon host — an Intel host, or a real device, keeps it. Capability preflight
(BE-0082) then rejects a `selectPhotos` step there before any device work, the same fail-fast
`unsupported()` already gives `pickerWheel` / `selectOption`.

**What this investigation could and could not verify.** The rejection path is fully verified here:
every test in this item's test suite that exercises `apple_silicon_simulator_host`,
`capabilities_for_run`'s narrowing, and capability preflight runs device-free and passes. The
success path — the actuation actually landing on a real device or an Intel Simulator — could not be
verified on real hardware: neither was available to this investigation. What stands behind it
instead is that it is the exact mechanism already proven to work against this same picker for a
*different* control (`Cancel`, above) and against every other element type this driver addresses,
and that the Apple Developer Forums / Bitrise reports name the failure as specific to Apple silicon
Simulators, not to XCUITest taps in general. A later reader with the hardware this investigation
lacked can close that gap directly: run `select_photos.yaml` (Unit 4) on a real device or an Intel
Mac and confirm `Selected: 2`.

The confirm button is resolved **structurally**, not by its label — the one button inside the
picker's navigation bar (`traits: ["navigationBar"]`, the only bar the picker presents — not named
by its own title, `Photos`, which `PHPickerViewController` localizes the same way it would localize
any label) whose identifier is not `Cancel`. Measured: the picker's dismiss control carries the
stable identifier `Cancel`, but the confirm control carries no identifier and only the label `Done`
(a checkmark glyph in this iOS version, not the word "Add"). Resolving by elimination inside the bar
itself, rather than by either control's label, needs no per-locale lookup table anywhere in the
rule — the bar is found by trait, not by its localized title, so nothing in the resolution path
reads a string that changes with the scenario's locale. Unlike SpringBoard's alert buttons, the
confirm control's identifier absence, not its label, is the stable fact. This resolution is
unaffected by the actuation scoping above: a navigation-bar button is static chrome, not a recycled
cell, and the `Cancel` measurement already confirms static chrome actuates fine through the ordinary
handle-based path on the one architecture this investigation could test — the same reasoning the
scoping itself rests on.

### Unit 4 — Capability, other backends, and the showcase fixture

`Capability.SELECT_PHOTOS = "selectPhotos"` (`bajutsu/common/drivers/base/capability.py`) is
declared by `XcuitestDriver.CAPABILITIES` (and by `FakeDriver.CAPABILITIES` for unit tests) as its
*static* set, then narrowed away at run time by `capabilities_for_run` on an Apple silicon Simulator
specifically (Unit 3). Either way it is gated through
`bajutsu/common/capability/capability_preflight.py` the same way `HANDLE_SYSTEM_ALERT` is — an
Android or web target, or an Apple silicon Simulator target, using `selectPhotos` fails preflight
with a named-capability error rather than an opaque runtime one. `playwright_driver.py`,
`adb_driver.py`, `xcuitest_live_driver.py`, and `web_context_driver.py` each raise
`UnsupportedAction` from their own `select_photos`, matching every other iOS-only action.

The showcase's `PermissionsView.swift` (SwiftUI only; UIKit parity is out of scope — see
*Alternatives*) gains a "Photos" section: an `Open Photo Picker` button
(`perm.openPhotoPicker`) presents a `PHPickerViewController` wrapped in
`UIViewControllerRepresentable` with `selectionLimit = 0` (unlimited, so the confirm-tap path is
always exercised), and a mirrored `Text` (`perm.photos.value`) reports the picked count. Both ids
join the existing `perm` namespace — no `idNamespaces` change needed. `demos/showcase/fixtures/photos/`
carries a handful of distinguishable fixture images (solid colours), seeded by
`demos/showcase/scenarios/select_photos.yaml` via `preconditions: { erase: true, seedPhotos: [...]
}` (Unit 1's validator requires `erase: true` alongside `seedPhotos`), which taps
`perm.openPhotoPicker`, runs `selectPhotos: { indices: [0, 1] }`, and asserts `perm.photos.value`
equals `2`. `demos/showcase/SPEC.md` §5.4 documents the two new ids next to the section's existing
ones.

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
- **Wait for a fix or documented workaround from Apple, or a still-untried actuation technique,
  before shipping anything.** Rejected: nothing in this investigation's four attempts, nor the
  external reports it matches, points to a fix arriving on any predictable timeline, and Units 1, 2,
  and 4 need no rework once one does — they are unaffected by which architectures Unit 3 can
  eventually cover. Holding the whole item hostage to an external fix of unknown timing would cost
  every contributor with the hardware this already works on (a real device, an Intel Mac) a feature
  they could use today.
- **Detect the Simulator's own architecture via an `xcrun simctl` query instead of the host's.**
  Rejected: a Simulator's guest OS runs natively on the host CPU, so the two questions have the same
  answer, and a `simctl` round trip risks the device-fault flakiness (BE-0363) a plain
  `platform.machine()` read cannot — checking the host is strictly simpler for an identical result.
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
- [x] Unit 3 — actuation for a picker grid cell, scoped to a real device / Intel Simulator via
  `apple_silicon_simulator_host` (see *Detailed design*), and the structural confirm-button
  resolution.
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
  `selectPhotos` ships using the ordinary handle-based tap every other element uses, gated off an
  Apple silicon Simulator by `capabilities_for_run` (`apple_silicon_simulator_host`, a host
  `platform.machine()` read). Implemented all four units, the showcase fixture, and their tests;
  `make check` green. The success path (a real device or an Intel Simulator) is unverified on real
  hardware — none was available to this investigation — and is left for a later reader to confirm
  by running `select_photos.yaml` there.

## References

- `bajutsu/common/scenario/models/actions/handle_system_alert.py` — the action-shape and
  locale-table precedent this item's Unit 2 and Unit 3 each partly follow and partly depart from.
- `bajutsu/common/drivers/base/_functions.py` — `resolve_unique`'s existing `index` handling
  (line 321), which this item reuses rather than extends.
- [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)
  — the frame-centre actuation and `Tappable` mechanism Unit 3 tried, and why it does not carry
  over to the picker's cells.
- [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) — the
  retirement of `idb`, the one alternative iOS actuator that might have routed around the Apple
  silicon Simulator limitation Unit 3 measures; XCUITest is the only one left.
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
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md) — the
  real-device run-time capability narrowing (`capabilities_for_run`) this item's own Apple-silicon
  narrowing extends, in the opposite direction (removing a capability from the Simulator default
  rather than restoring one for a real device).
- [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md) — the
  preflight check that rejects a `selectPhotos` step up front on a backend, or a Simulator/host
  combination, lacking `SELECT_PHOTOS`.
- [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) — the
  `simctl` device-fault flakiness a host `platform.machine()` read avoids paying, unlike a `simctl`
  query for the same fact (*Alternatives considered*).
