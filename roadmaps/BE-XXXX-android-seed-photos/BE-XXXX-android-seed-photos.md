**English** · [日本語](BE-XXXX-android-seed-photos-ja.md)

# BE-XXXX — Seed the Android photo library before a run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-seed-photos.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2382](https://github.com/bajutsu-e2e/bajutsu/pull/2382) |
| Topic | Platform support |
| Related | [BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md), [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md), [BE-0469](../BE-0469-runson-destination-blocks/BE-0469-runson-destination-blocks.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds `runsOn.android.seedPhotos` to an Android
[target](../../docs/glossary.md#target-app-device), and `preconditions.device.android.seedPhotos` to a
[scenario](../../docs/glossary.md#scenario-authoring). Before the app launches, the Android environment puts
the listed images, in order, into a directory Bajutsu owns in the device's shared storage, and asks
the media scanner to publish them. A photo picker opened by the app under test then shows a known set
of images, the way `runsOn.ios.seedPhotos` already does on the iOS Simulator.

## Motivation

A scenario that exercises an image picker needs images to pick. On iOS, `runsOn.ios.seedPhotos`
seeds the Simulator's photo library with `simctl addmedia` on the erase path, and `selectPhotos`
([BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos.md)) then addresses a
known grid. An Android target has no counterpart. A fresh emulator holds no images at all, and a
reused one holds whatever earlier runs left behind, so an Android scenario cannot open a picker and
find a stable set. Authors either skip the flow on Android or push images by hand in a
`hooks.setup` shell step, outside the deterministic preconditions that every other device state
goes through. The settings table of
[BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md)
records the gap: its `seedPhotos` row reads `unsupported` on the Android route.

The iOS design cannot carry over unchanged, because it relies on a wipe that Android lacks. iOS seeds
only under `subject.reinstall: erase`, since `simctl erase` is what guarantees that the library holds
exactly the seeded images. On Android, `erase` means uninstalling the app and running `pm clear`;
neither touches shared storage, so images seeded by one scenario would survive into the next. The
Android design needs its own source of idempotence.

Once this item ships, the change is observable in two places. An Android target that sets
`runsOn.android.seedPhotos` launches with exactly those images in
`/storage/emulated/0/Pictures/Bajutsu`, in the listed order, on every reinstall level. The settings
table's Android row for `seedPhotos` reads `supported`, so a target no longer needs a
`runsOn.ignoredSettings` entry to declare the gap.

## Detailed design

### Configuration and scenario fields

`runsOn.android` gains `seedPhotos`, a list of image paths. Like `runsOn.ios.seedPhotos`, the field
is device state rather than a condition, so device matching ignores it, and its paths resolve
against the config file (a template's paths are rebased the same way). A scenario replaces the
target's list with `preconditions.device.android.seedPhotos`, whose paths resolve against the scenario
file and are confined to the suite root, as the iOS field's paths are. `preconditions.device` was an
iOS-only block; it now holds an `android` block beside `ios`, and a block for a platform the run does
not drive keeps having no effect.

### Idempotence without a wipe

The Android environment owns one directory, `/storage/emulated/0/Pictures/Bajutsu`, and resets it on
every seed. The seed runs these steps in order:

1. Derive each device-side name from the list position and the source file's extension, such as
   `000.png`. A source without a plain alphanumeric extension fails before the device is touched.
2. Create the directory if it is missing and empty it, so no image from an earlier scenario
   survives. The directory itself stays, so the scan in step 4 walks it and drops the rows of the
   removed images.
3. Push each image with `adb push`, then stamp it with `touch -t`, giving the images fixed,
   ascending modification times one minute apart.
4. Broadcast `MEDIA_SCANNER_SCAN_FILE` for the directory, with `--receiver-include-background` so the
   implicit broadcast reaches MediaProvider's manifest receiver on Android 8 and later.

Resetting a directory of its own, instead of the whole media store, keeps the seed from deleting
images the device already holds. That matters on a physical device, where those images belong to a
person rather than to the test.

The fixed timestamps make the order reproducible. A picker that sorts by date shows the last listed
image first, which matches the order `simctl addmedia` produces on iOS. Without the stamp, `adb push`
would carry over each local file's own modification time, and the order would depend on the
checkout. The media store prefers an image's EXIF capture date to its modification time, so the
order holds for fixtures without one; a fixture exported from a camera keeps its own date.

### When the seed runs, and what it requires

`AndroidEnvironment.start` seeds after the runtime permission grants and before `am start`. The seed
runs on every reinstall level. Because the directory reset supplies the idempotence that the wipe
supplies on iOS, an Android target needs no `subject.reinstall: erase`. `check_preconditions` and the
`record` / `crawl` / `repl` guard therefore apply the erase requirement to iOS targets alone. The
primary-target rule stays on both platforms: in a scenario that drives several targets, only the
primary seeds.

An empty list still empties the directory when an earlier seed created it, then rescans it. A
scenario that seeds nothing therefore never sees another scenario's images, and a device that was
never seeded keeps its shared storage untouched. The media scan finishes asynchronously, after the
launch, so a scenario waits for a seeded image to appear before it selects one, the same condition
wait the iOS showcase scenario already uses.

### Settings table

The `seedPhotos` row of the Android route moves from `unsupported` to `supported`, and the setting's
source field reads `runsOn.<platform>.seedPhotos` in messages, since two platforms now carry it.

## Alternatives considered

- **Require `subject.reinstall: erase`, as iOS does.** The rule would read the same on both
  platforms. However, Android's `erase` clears the app's own data and never the shared storage, so
  the requirement would give no guarantee about the images. It would also force a reinstall on
  every scenario that only wants a known picker.
- **Delete every image in the media store before seeding.** A picker would then show the seeded
  images alone. On a physical device, that step would delete a person's photos, which a test tool
  should never risk.
- **Push into the app's own storage.** A picker reads the shared media store, not an app's private
  directory, so the images would never appear in it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] The `runsOn.android.seedPhotos` and `preconditions.device.android.seedPhotos` fields.
- [x] Path resolution and the scenario-over-target merge.
- [x] The adb command builders and `Env.add_media`: directory reset, ordered push, fixed timestamps,
  and the media scan.
- [x] Seeding in `AndroidEnvironment.start`, and the erase requirement narrowed to iOS.
- [x] The settings table's Android `seedPhotos` row set to `supported`.
- [x] Documentation in English and Japanese.
- [x] Confirm the media scan and the picker order on an emulator.

Log:

- 2026-11-10 — implemented the fields, the seed, and the documentation, with unit tests over the
  command sequence. No emulator was attached during development, so the media scan's behavior on a
  device is not yet confirmed; the item stays In progress until the last checklist box is ticked.
  That check covers whether a directory scan drops the rows of removed images, and the picker order.
- 2026-11-10 — confirmed on an API 34 arm64 emulator (`bajutsu-api34-arm64`, booted read-only).
  Seeded rows reached the media store about 2 seconds after the scan, with ascending
  `date_modified` and no `datetaken`. Reseeding one image dropped the three earlier rows, and an
  empty list cleared them all. The system photo picker (`PICK_IMAGES`) showed the last listed image
  first, and showed the new image after a reseed under the same file name. Other API levels are
  not yet checked.

## References

- [BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos.md): `selectPhotos` and
  the iOS `seedPhotos` it relies on.
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the target
  config groups that `seedPhotos` belongs to.
- [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md): the
  per-route settings table this item updates.
- [BE-0469](../BE-0469-runson-destination-blocks/BE-0469-runson-destination-blocks.md): the
  `runsOn` destination blocks and the scenario's `preconditions.device`.
