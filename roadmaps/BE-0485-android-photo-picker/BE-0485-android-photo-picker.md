**English** · [日本語](BE-0485-android-photo-picker-ja.md)

# BE-0485 — Pick images from the Android photo picker with selectPhotos

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0485](BE-0485-android-photo-picker.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0485") |
| Implementing PR | [#2411](https://github.com/bajutsu-e2e/bajutsu/pull/2411) |
| Topic | Platform support |
| Related | [BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos.md), [BE-0479](../BE-0479-android-seed-photos/BE-0479-android-seed-photos.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets the adb driver run the `selectPhotos` step. The step picks images by position from
the system photo picker that the app under test has opened. On Android 13 and later, the driver
waits for the picker's grid, taps the requested cells, and confirms the selection. It finds every
control by its resource id rather than by localized text. The two showcase scenarios that cover the
step then run unchanged on iOS and Android.

## Motivation

A [scenario](../../docs/glossary.md#scenario-authoring) that tests an image upload needs to pick
an image. On iOS, `selectPhotos`
([BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos.md)) picks cells from
the `PHPickerViewController` grid by position. On Android, the adb driver raises `UnsupportedAction`
for the same step, and the preflight check rejects any scenario that contains it. An author must
then skip the flow on Android or tap the picker's cells by raw coordinate, which ties the scenario
to one screen size.

[BE-0479](../BE-0479-android-seed-photos/BE-0479-android-seed-photos.md) closed half of this gap.
An Android run can now seed a known set of images before the app launches, in a known order. The
remaining half is the picker itself. The system photo picker (`MediaStore.ACTION_PICK_IMAGES`) runs
in its own process. Its grid cells carry no label that an author could name, and its confirm button
reads "Add (2)" in English and something else in every other locale.

Once this item ships, the change is observable in two places. `AdbDriver.CAPABILITIES` contains
`selectPhotos`, so the preflight check admits the step on an Android target. The showcase
scenarios `select_photos.yaml` and `select_photo_single.yaml` pass on the Compose and Views twins
of the showcase app, with the same steps that pass on iOS.

## Detailed design

### What the picker looks like

We surveyed the picker on an API 34 `google_apis` emulator, the same image that the Android
end-to-end (E2E) lane in continuous integration (CI) boots. The table lists what the driver relies
on. Every resource id lives under the picker's own package. That package is
`com.google.android.providers.media.module` on a Google build and
`com.android.providers.media.module` on an Android Open Source Project (AOSP) build.

| Element | Resource id | What the driver reads |
|---|---|---|
| Grid cell thumbnail | `icon_thumbnail` | Bounds, and `selected="true"` once picked |
| Cell check mark | `icon_check` | Present only when the picker allows more than one image |
| Confirm button | `button_add` | Appears after the first pick in a multi-selection picker |

A single-selection picker shows no check marks and no confirm button. It returns its one image and
closes on the first tap, as the iOS picker does with a selection limit of one. Both pickers list the
newest image first. A set seeded by BE-0479 therefore shows its last listed image at position 0,
the same order the iOS Simulator shows after `simctl addmedia`.

### The driver operation

`AdbDriver.select_photos(indices, timeout)` runs these steps:

1. Refuse a device below API 33 with `UnsupportedAction`. The photo picker is built into Android 13
   and later. On Android 11 and 12 it arrives only through a Google system update. Earlier versions
   fall back to a Google Play services backport or to the document picker (`ACTION_OPEN_DOCUMENT`).
   None of these has been surveyed, so this item does not cover them.
2. Wait, up to `timeout`, until the picker's grid holds more cells than the largest requested index.
   The cells are the `icon_thumbnail` nodes in the picker's two packages, ordered row by row: by top
   edge, then by left edge within a row. When the wait ends short, the step fails with
   `ElementNotFound` and names how many cells the grid showed. The wait also covers BE-0479's media
   scan, which finishes after the app launches. The driver treats the picker as multi-selection when
   the grid shows `icon_check` nodes, and as single-selection otherwise. A single-selection picker
   given more than one index fails here, before any tap.
3. Wait until the picker's own window holds input focus (`dumpsys window`'s `mCurrentFocus`). The
   tree shows the picker as soon as its window attaches, while input can still go to the app
   beneath it. A tap taken then lands on whatever app view sits under the cell.
4. Tap each requested cell at the center of its bounds. In a multi-selection picker, wait after
   each tap until that cell reads `selected="true"`, so the next tap never races the previous one.
5. Confirm. In a multi-selection picker, wait for `button_add` and tap it. In a single-selection
   picker, the tap in step 4 already confirmed.
6. Wait until no node of the picker's packages remains in the tree, so the next step reads the
   app's own screen.

Each wait is a condition wait bounded by the step's own `timeout`. No step guesses: an index beyond the grid fails and names how many cells the grid showed, and a
multi-selection picker that never shows its confirm button fails when the wait times out.

The driver reads the tree that the resident channel or `uiautomator dump` returns, before the
driver's own element parsing. The parsed elements drop a node's package, and the package is what
keeps an app's own view that happens to be named `icon_thumbnail` out of the grid. Only the
resource id's local name is matched. The step needs no per-locale table.

Some cells sit off the visible grid when the library holds more images than one screen shows. This
item resolves the visible cells alone and does not scroll the grid. A seeded library of a few images
fits on the first screen.

### Capability

`AdbDriver.CAPABILITIES` gains `selectPhotos`. The capability stays static, as `multiTouch` does
on this driver. The driver enforces the API-level requirement when the step runs, because the
preflight check reads the capability set without a device.

### Showcase and scenarios

The Compose and Views twins of the showcase app gain a Photos section on their Permissions screen.
The section holds two buttons and one value:

- `perm.openPhotoPicker` opens a multi-selection picker (`PickMultipleVisualMedia`).
- `perm.openPhotoPickerSingle` opens a single-selection picker (`PickVisualMedia`).
- `perm.photos.value` shows the number of images that came back.

Both pickers ask for images alone. These ids and the behavior match the SwiftUI twin's Photos
section. The Views twin spells the ids with underscores, since `android:id` does not allow a dot.

The two scenarios become portable with three changes:

- Each seeds the same fixtures with `preconditions.device.android.seedPhotos` beside the iOS block.
- The wait for the grid lists both platforms' cell ids,
  `[PXGGridLayout-Info, icon_thumbnail]`.
- Each id selector lists the dotted and the underscore form.

The Android E2E lane runs both scenarios against both twins.

### Documentation

The `selectPhotos` rows of `docs/platform-parity.md` and of the capability table in
`docs/drivers.md` change to supported on Android. The step reference in `docs/scenarios.md` names
both pickers. Each page changes in English and Japanese.

## Alternatives considered

- **Cover the document picker (`ACTION_OPEN_DOCUMENT`) too.** A device without the system photo
  picker would then work as well. That picker is a different tree with its own ids, owned by
  DocumentsUI, so the driver would keep two layouts current for a shrinking set of devices.
- **Match the confirm button by its text.** The text changes with the locale and with the count
  ("Add (2)"), so the driver would need a per-locale table, which a resource id makes unnecessary.
- **Scroll the grid to reach a cell beyond the first screen.** A scroll makes a cell's ordinal
  depend on how far the grid moved and which rows it recycled. A seeded library fits on one screen,
  so the step fails loudly instead.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Survey the picker tree on an API 34 emulator.
- [x] `AdbDriver.select_photos`: the API check, the grid wait, the focus wait, the taps, and the
  confirmation.
- [x] `selectPhotos` in `AdbDriver.CAPABILITIES`.
- [x] Unit tests over recorded picker dumps.
- [x] The Photos section in the Compose and Views twins.
- [x] The portable scenarios and the Android E2E lane.
- [x] Documentation in English and Japanese.
- [x] Run both scenarios on an emulator.

Log:

- 2026-11-11 — surveyed the picker on an API 34 `google_apis` arm64 emulator. No API 33 or API 35
  system image was installed, so those levels are not yet checked.
- 2026-11-11 — implemented the driver, the capability, the showcase twins, the portable scenarios,
  and the documentation, with unit tests over the recorded dumps. On the same emulator, one Views
  run tapped a cell while the tree already showed the picker, and the tap reached the app's own
  location button beneath it. The driver now waits for the picker's window to hold input focus
  first. After that change, both scenarios passed on the Compose and Views twins, and the Views
  multi-selection run passed three times in a row.

## References

- [BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos.md): the `selectPhotos`
  step and its iOS driver.
- [BE-0479](../BE-0479-android-seed-photos/BE-0479-android-seed-photos.md): seeding the Android photo
  library that this picker lists.
- [Photo picker](https://developer.android.com/training/data-storage/shared/photopicker): the
  Android documentation for `ACTION_PICK_IMAGES` and its contracts.
