**English** · [日本語](BE-XXXX-android-device-control-parity-ja.md)

# BE-XXXX — Close the simctl gaps on Android: background, status bar, app locale, and appTrace

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-device-control-parity.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md), [BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) |
<!-- /BE-METADATA -->

## Introduction

On iOS, Bajutsu drives the Simulator's device state through `simctl`. The Android backend reaches
part of that surface through adb. This item closes four gaps that adb fills by itself. None of
the four needs cooperating code inside the app under test:

- The `background` step sends the app home with the `KEYCODE_HOME` key event.
- The `overrideStatusBar` and `clearStatusBar` steps drive the System UI demo mode.
- A new `runsOn.android.locale` setting applies a per-app language with `cmd locale
  set-app-locales`. The command exists from Android application programming interface (API)
  level 33.
- The `appTrace` evidence kind reads the app's own log lines from `logcat`, filtered by tag.

The adb driver advertises `background`, the status-bar pair, and `appTrace` through the existing
capability tokens. Preflight then admits them on Android as it does on iOS. The locale needs no
token. The locale is a config field, which preflight checks against the API level. A scenario that uses these steps runs unchanged on both
platforms. Today the same scenario fails on Android before any device work.

## Motivation

Bajutsu shares one scenario language across platforms. A scenario written once should run on the
iOS Simulator and the Android emulator alike. Per-app differences stay in config (prime
directive 3). Device-control steps break that promise today. Of the device-control tokens, the adb backend declares
`setLocation`, `clipboard`, and `foreground` alone
(`bajutsu/common/drivers/adb/adb_driver.py:1947`). The Android `DeviceControl` raises
`UnsupportedAction` for the rest (`bajutsu/common/platform_lifecycle/device_control.py:117-133`).
Preflight then rejects any scenario that uses `background` or `overrideStatusBar` on Android.

That gate rests on a claim from
[BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md). The claim says these
operations have "no faithful emulator equivalent". For two of them the claim no longer holds.
`KEYCODE_HOME` backgrounds an app without terminating it, which is the iOS Home button's exact
meaning. The System UI demo mode freezes the clock, battery, and signal icons for screenshots,
which is the purpose of `simctl status_bar`. Android also gained `foreground` since then
([BE-0447](../BE-0447-install-app-step/BE-0447-install-app-step.md)). `background` is now the
missing half of a pair.

Two further gaps sit outside the step list. The first is locale. iOS applies `runsOn.ios.locale` at
launch. The Android config has no locale field at all
(`bajutsu/common/config/schema/platform/android.py`). A localized Android app runs in whatever
language the emulator image carries. The second is the `appTrace` evidence kind.
That kind turns the app's "`<name>` started" and "`<name>` finished" log lines into timed
intervals. The adb driver returns no interval for it (`AdbDriver.driver_interval`). An Android
report never shows the app's own timings, even when the app logs them.

Authors work around the gaps in one of two ways. They split a journey into an iOS scenario and an
Android copy, or they drop the steps from the shared scenario. The first costs a maintained
duplicate; the second weakens the iOS test. Once this item ships, a reader can check the effect directly. The showcase's shared
scenarios can use `background`, `overrideStatusBar`, and a non-default locale. They still run
unchanged on the Android emulator lane. The Android report then carries an `appTrace` section
whenever the app logs the markers.

## Detailed design

The four additions share one shape. Each one adds a pure adb command builder, a method on
`adb.Env`, and the wiring that already exists for its iOS counterpart. None of them changes the
scenario schema of a step, and none puts a model call on the `run` path.

### `background` over the Home key

The adb `DeviceControl.home()` sends `input keyevent KEYCODE_HOME` (key code 3). A new
`KEYCODE_HOME` constant joins `KEYCODE_BACK` in `bajutsu/common/backend_cli/adb/_shared.py`. The
adb driver then adds `Capability.DC_BACKGROUND` to its `CAPABILITIES`.

The Home key keeps the app's process alive, which matches the iOS meaning ("backgrounds without
terminating"). Two existing behaviors follow from that and need no new code:

- The app-exit poll reads the app's process and exit records. A backgrounded app keeps its
  process, so the poll reports no exit.
- A later `foreground` brings the app back. When the system has reclaimed the process in the
  meantime, `foreground` relaunches it, as it already does after an `installApp`.

After `background`, the device shows the launcher. A selector for an element of the app under test
finds nothing, as it does on iOS while SpringBoard is in front.

### `overrideStatusBar` and `clearStatusBar` over the demo mode

Android System UI ships the demo mode as a broadcast interface for screenshots. It needs no root. The control first allows the mode with `settings put global
sysui_demo_allowed 1`. It then sends `am broadcast -a com.android.systemui.demo -e command enter`.
It then sends one command for each field the step sets. The table maps the step's fields, which follow
`simctl status_bar`.

| Step field | iOS range | Demo-mode command | Mapping rule |
|---|---|---|---|
| `time` | free text | `clock -e hhmm HHMM` | `H:MM` or `HH:MM` alone; the colon is dropped and the hour is zero-padded |
| `batteryLevel` | 0–100 | `battery -e level N` | unchanged |
| `batteryState` | `charging`, `charged`, `discharging` | `battery -e plugged true\|false` | `charging` and `charged` map to `true`; `discharging` maps to `false` |
| `cellularBars` | 0–4 | `network -e mobile show -e level N` | unchanged |
| `wifiBars` | 0–3 | `network -e wifi show -e level N` | `round(N × 4 / 3)`: 0→0, 1→1, 2→3, 3→4, so a full iOS signal stays full |

Entering the demo mode also sends `notifications -e visible false`. The iOS status bar shows no
notification icons, and a hidden icon row keeps a screenshot identical from run to run.
`clearStatusBar` sends `-e command exit`, which restores the live status bar.

The demo mode also differs from `simctl status_bar` in how it treats unset fields. Entering the
mode freezes every icon, so a field the step leaves unset takes the demo mode's own fixed value rather than the live
one. The result stays deterministic, which is the point of the step. The scenario reference
documents the difference.

Android accepts a narrower `time` than iOS. On an Android target, preflight fails an
`overrideStatusBar` whose `time` is neither `H:MM` nor `HH:MM`, and names the step. That check runs before
any device work. The adb control raises `UnsupportedAction` for the same input as a backstop. Once
the mapping is in place, the adb driver adds `Capability.DC_STATUS_BAR`.

Like the iOS override, the demo mode persists on the device until `clearStatusBar` runs. This item
keeps that behavior on both platforms rather than adding a teardown only to Android.

### `runsOn.android.locale` over per-app locales

`AndroidDestination` gains `locale: str | None`. A scenario may replace the value under
`preconditions.device.android.locale`, as it may on iOS. The value takes the syntax and validation
of `runsOn.ios.locale` (`ja_JP`). The Android environment converts it to a language tag in the
Best Current Practice 47 (BCP 47) form (`ja-JP`).

Before every launch, the environment runs `cmd locale set-app-locales <package> --user current
--locales <tag>`. It then reads the value back with `cmd locale get-app-locales`. A mismatch fails
the start with a `DeviceError` that names the package and the locale. The setting survives an app
restart, and repeating it costs one shell call. The environment applies it on every launch rather
than tracking what the device already holds.

Unlike iOS, an unset Android `locale` sets no language. iOS falls back to a built-in `en_US`. An
Android default would change every existing Android run and would require API level 33 or later of
every device. Unset therefore keeps the device's own language, as today.

Keeping that promise takes one reset. The per-app setting survives an app restart, and the pool
reuses an emulator across scenarios. Without a reset, a scenario that sets `ja_JP` would leave the
next scenario in Japanese even when that one sets no locale. The `reinstall: overwrite` path skips
both the uninstall and `pm clear`, so the leak is certain there. On API level 33 and later, an unset
locale thus clears the per-app setting before launch. The command is `cmd locale set-app-locales
<package> --user current --locales ""`. Below API level 33 the environment issues nothing,
because no earlier run could have set a per-app locale there. The environment reads the level with
`_read_api_level` on every start for this decision, whatever `apiLevel` states. A level it cannot
read fails the start with a `DeviceError`. Skipping the reset could leak a previous locale, and
issuing the reset could fail below 33.

The per-app locale reaches the app alone. System dialogs, such as a runtime permission prompt, stay
in the emulator's own language. iOS goes further and also pins the Simulator's system language
([BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)).
Android has no comparable step without root and a runtime restart. The *Alternatives* section
weighs that option. The configuration reference states the difference. It also advises an `id`
selector for a system dialog's buttons, because the dialog's text follows the image.

`cmd locale` exists from API level 33. Preflight runs before the pool acquires a device, so it
cannot read the device's level. The check runs in two stages:

1. **From config, before any device work.** When `runsOn.android.apiLevel` admits a level below
   33, preflight fails a target that sets `locale`. The message names both fields. It suggests
   narrowing `apiLevel` to 33 or later. The check sits beside the existing `seedPhotos` check in
   `bajutsu/common/config/preconditions.py`.
2. **From the device, before the first step.** When `apiLevel` is unset, the environment reads
   `ro.build.version.sdk` at start, as `_read_api_level` already does. A level below 33 fails the
   start with a `DeviceError` that names the locale and the level.

### `appTrace` over tag-filtered `logcat`

On iOS, `appTrace` streams the app's `os_log` subsystem. `parse_app_trace` pairs "`<name>`
started" with "`<name>` finished" into intervals. The Android twin streams `logcat` filtered
to one tag:

```
adb -s <serial> logcat -v year,usec,zone -T 1 -s <tag>:V
```

The tag is the value of `--log-subsystem`, else the Android package. `_log_subsystem_default`
returns the package for an Android target, as it returns the bundle id for iOS. The filter keys on
the tag rather than on a process id. The stream thus continues across a `relaunch`.

A new `parse_logcat_app_trace` reads each line's `year,usec,zone` timestamp and message. `-T 1`
replays one line already in the buffer, so the parser drops any line stamped before the stream
started. It then reuses the existing `_BEGIN` and `_END` patterns, so both platforms recognize the same
markers. The app emits them with `android.util.Log`, for example `Log.i(tag, "checkout started")`.

The sink hands an interval kind to a driver-supplied lane through `driver_interval(kind, path)`.
That seam carries neither the subsystem nor the path of the parsed JavaScript Object Notation (JSON)
file. This item extends the seam with both as keyword arguments. The Playwright and XCUITest live
drivers, which implement the same seam, ignore them. The adb driver then
returns the new interval for `appTrace`, and adds `Capability.APP_TRACE`.

### Work breakdown (MECE)

1. **`background`.** Add the `KEYCODE_HOME` constant, an adb builder, and `adb.Env.home()`. Wire
   the control and declare `DC_BACKGROUND`. Fast-gate tests cover three points:
   - the command shape
   - preflight admitting `background` on adb
   - the exit poll staying quiet while the app sits in the background
2. **Status bar.** Add the demo-mode builders and the field mapping as a pure function. Wire the
   control, add the Android `time` preflight check, and declare `DC_STATUS_BAR`. Fast-gate tests
   cover three points:
   - each row of the mapping table
   - each `time` form that preflight fails
   - the `exit` command that `clearStatusBar` sends
3. **Locale.** Add the `runsOn.android.locale` and `preconditions.device.android.locale` fields.
   Resolve them into the effective config. Add the set-and-verify launch path and the two-stage
   API-level check. Fast-gate tests cover four points:
   - the BCP 47 conversion
   - the read-back mismatch
   - both failing stages of the API-level check
   - an unset locale issuing the reset on API level 33 and later, nothing below it, and an
     unreadable level failing the start
4. **`appTrace`.** Add the tag-filtered `logcat` builder and `parse_logcat_app_trace`. Extend the
   `driver_interval` seam, set the Android default subsystem, and declare `APP_TRACE`. Fast-gate
   tests parse a recorded `logcat` sample into intervals. They also check that the Playwright
   and XCUITest live drivers ignore the new arguments, and that a replayed pre-start line is
   dropped.
5. **Documentation.** Update each page below together with its `docs/ja/` mirror:
   - `docs/scenarios.md`: Android support for the new steps, and the demo-mode difference
   - `docs/configuration.md`: `runsOn.android.locale`, and its system-dialog note
   - `docs/evidence.md`: the Android `appTrace`
   - `docs/architecture.md`: every list of the adb backend's capabilities
6. **On-device confirmation.** Add a showcase scenario to the Android emulator lane. The scenario
   runs `background`, `foreground`, `overrideStatusBar`, and `clearStatusBar` under a non-default
   locale. A `visual` check confirms the frozen status bar.

## Alternatives considered

- **Background with `am start` of the launcher's `HOME` intent.** Not adopted. The intent reaches
  the same launcher through the activity manager's own start rules. The key event takes the path of
  a user's press, which keeps the step's meaning identical to the iOS Home button.
- **Status bar by editing System UI resources or with a root-only overlay.** Not adopted. Both
  need root or a custom image. The demo mode ships with Android System UI for screenshots and needs
  neither.
- **System-wide locale through `setprop persist.sys.locale` and a runtime restart.** Not adopted
  for now. The restart needs root and takes tens of seconds per change. It also fails on emulator
  images that include the Play Store. In exchange, a system dialog's text would become
  predictable. That benefit could justify a separate item if authors need text selectors there.
- **Default an unset Android locale to `en_US`, as iOS does.** Not adopted. The default would
  change every existing Android run. It would also demand API level 33 of every device.
- **Check the API level at device start alone.** Not adopted. A config that states its API range
  would meet the conflict after the pool acquires and boots a device. Reading the config first
  fails that case in milliseconds.
- **Filter `appTrace` by process id (`logcat --pid`).** Not adopted. The process id changes on
  every `relaunch`, so the stream would go silent mid-scenario. A tag survives the restart.
- **Deliver `push` and `clearKeychain` in this item.** Not adopted, for scope. Both need a
  receiver in the app's process, as the clipboard does. They belong to a separate item that
  extends the Android in-app library.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `background` over `KEYCODE_HOME`, with `DC_BACKGROUND`.
- [ ] `overrideStatusBar` / `clearStatusBar` over the demo mode, with `DC_STATUS_BAR`.
- [ ] `runsOn.android.locale` over per-app locales, with the two-stage API-level check.
- [ ] `appTrace` over tag-filtered `logcat`, with `APP_TRACE`.
- [ ] Documentation in `docs/` and `docs/ja/`.
- [ ] On-device confirmation on the Android emulator lane.

## References

- [BE-0211 — Android device control (setLocation, clipboard)](../BE-0211-android-device-control/BE-0211-android-device-control.md):
  the item that first declared these operations unsupported on Android.
- [BE-0212 — Split the coarse deviceControl capability into per-operation tokens](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md):
  the tokens this item advertises.
- [BE-0035 — Device-control steps (background, status-bar override)](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md):
  the iOS steps this item ports.
- [BE-0320 — iOS system-alert locale determinism](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md):
  the iOS system-language pin that Android does not match.
- Android Open Source Project, [System UI demo mode](https://android.googlesource.com/platform/frameworks/base/+/master/packages/SystemUI/docs/demo_mode.md).
- Android Developers, [Per-app language preferences](https://developer.android.com/guide/topics/resources/app-languages).
