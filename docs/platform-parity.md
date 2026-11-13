**English** · [日本語](ja/platform-parity.md)

# iOS and Android driver parity

Bajutsu runs each platform on its own [backend](glossary.md#driver-backend-actuator-platform).
iOS runs on `xcuitest`, and Android runs on `adb`. The two backends do not support the same
features. This page lists every difference between them. Each difference is either a gap we intend to
close or a feature the platform does not need. The page records which kind each one is. The page serves
as the working list for closing the gaps.

A test keeps the first table honest. The test compares each mark with the capabilities each driver
declares. A gap cannot open or close unless this page changes with it.

## What the three tables cover

A **capability** is a named feature a backend declares through `Driver.capabilities()`. Two
examples are `multiTouch` and `deviceControl.push`. The preflight check runs before a run leases a
device. It stops a scenario that uses a gated step the chosen backend has no capability for. The
check leaves some capabilities ungated, because the core supplies each one another way. Examples are
`conditionWait`, `network`, `semanticTap`, and `webView`. The interval-evidence capabilities stay
ungated too.
[Drivers](drivers.md#capabilities-capability) lists every capability with its meaning.

The three tables split the differences by what kind of difference each one is:

- **Capability parity** lists every capability, with one mark per backend. The test checks this
  table.
- **Launch settings** lists what a run sets up before the app starts. The test checks this table
  too.
- **Differences in behavior** lists differences that no capability names. Both backends can
  declare a feature that still behaves differently on one of them. People keep this table by hand.

The iOS column describes the iOS Simulator, and the Android column describes the Android emulator.
A real iOS device drops the capabilities that `simctl` provides.
[iOS on a real device and in a device cloud](ios-device-cloud.md) describes that narrowing.

## How to read a mark

Each cell of the capability table holds one of four marks and nothing else. Any detail goes in the notes
column.

| Mark | Meaning |
|---|---|
| ✅ | The backend declares the capability. |
| gap | The backend does not declare it yet, and the platform could support it. The notes name the route. |
| n/a | The backend does not need it, or the platform cannot provide it. The notes say why. |
| per-run | The backend does not declare it statically, and a run grants it when the app is set up for it. |

## Capability parity

<!-- parity-table -->
| Capability | iOS | Android | Notes |
|---|---|---|---|
| `query` | ✅ | ✅ | |
| `elements` | ✅ | ✅ | |
| `screenshot` | ✅ | ✅ | |
| `semanticTap` | ✅ | gap | adb taps the frame center of the element a host-side query resolves. The on-device step executor ([BE-0410](../roadmaps/BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md)) is the route. |
| `conditionWait` | ✅ | gap | adb waits by polling the tree from the machine that runs Bajutsu. BE-0410 moves the wait onto the device. |
| `network` | n/a | n/a | The token means native in-process observation, which the web backend alone has. Both mobile backends answer `request` assertions through an app-side collector instead. |
| `video` | ✅ | ✅ | |
| `deviceLog` | ✅ | ✅ | |
| `appTrace` | ✅ | ✅ | iOS streams the app's `os_log`. Android reads the app's own log lines from `logcat`, filtered by tag ([BE-0478](../roadmaps/BE-0478-android-device-control-parity/BE-0478-android-device-control-parity.md)). |
| `multiTouch` | ✅ | ✅ | Android injects both fingers through the resident UI Automator server. Without the server, it needs a rooted device. |
| `textSelection` | ✅ | ✅ | Android needs version 12 or later. See the behavior table. |
| `webView` | per-run | gap | iOS grants it when the app embeds the BajutsuKit WebView bridge. Android has no bridge. Chrome's WebView debugging port is a possible route. |
| `selectOption` | n/a | n/a | Sets a web page's `<select>` element, which the web backend alone has. |
| `urlNavigation` | n/a | n/a | Opens a web address mid-scenario in a browser. Both mobile backends open a deep link as a launch precondition instead. |
| `elementAttribute` | n/a | n/a | Reads an attribute of a web page element, which the web backend alone has. |
| `handleSystemAlert` | ✅ | n/a | An Android system dialog appears in the dumped tree, so an ordinary `tap` reaches it. |
| `resolveAlerts` | ✅ | n/a | Same reason as `handleSystemAlert`. |
| `pickerWheel` | ✅ | ✅ | The resident server walks an Android `NumberPicker` row by row, spinner-mode `DatePicker` and `TimePicker` wheels included (BE-0480). A wheel that Jetpack Compose draws itself stays out of scope, since Compose ships no standard wheel. |
| `appContext` | ✅ | ✅ | Android starts the other package's launcher activity with `am start` and waits until that package holds the resumed activity. UI Automator reads whichever app's window is in front. |
| `selectPhotos` | ✅ | ✅ | adb finds the Android 13+ photo picker's cells and add button by resource id, so the same steps run on both platforms. On iOS, an Apple Silicon Simulator drops the capability at run time. |
| `handleTipkitTip` | ✅ | n/a | TipKit is an iOS framework. |
| `handleNotificationBanner` | ✅ | gap | An Android heads-up notification appears in the tree as a SystemUI node. |
| `deviceGroup` | ✅ | ✅ | |
| `deviceControl.setLocation` | ✅ | ✅ | Off a local emulator, Android needs the BajutsuAndroid library in the app. See the behavior table. |
| `deviceControl.clipboard` | ✅ | ✅ | Android needs the BajutsuAndroid library in the app. See the behavior table. |
| `deviceControl.push` | ✅ | gap | The BajutsuAndroid library could post the notification from inside the app, the way it already handles the clipboard. |
| `deviceControl.clearKeychain` | ✅ | n/a | Android has no keychain. The app's Android Keystore keys go away with its data on a clean reinstall. |
| `deviceControl.background` | ✅ | ✅ | Android sends the home key (`KEYCODE_HOME`), which keeps the app's process alive (BE-0478). The step then waits until the app is no longer the resumed activity. |
| `deviceControl.foreground` | ✅ | ✅ | |
| `deviceControl.statusBar` | ✅ | ✅ | Android drives the SystemUI demo mode (BE-0478). A field the step leaves unset takes the demo mode's fixed value, and `time` accepts `H:MM` or `HH:MM` alone. |
| `deviceControl.permissions.location` | ✅ | ✅ | |
| `deviceControl.permissions.camera` | ✅ | ✅ | |
| `deviceControl.permissions.microphone` | ✅ | ✅ | |
| `deviceControl.permissions.contacts` | ✅ | ✅ | |
| `deviceControl.permissions.photos` | ✅ | ✅ | |
| `deviceControl.permissions.calendar` | ✅ | ✅ | |
| `deviceControl.permissions.notifications` | n/a | ✅ | iOS keeps notification authorization outside the privacy database that `simctl privacy` edits, so the command has no service to drive. A `handleSystemAlert` step answers the prompt instead. |

## Launch settings

A **launch setting** reaches the device before the app starts. Two examples are a clean reinstall
and a seeded photo library. Each platform route records the settings it can apply in a
support table, `settings_support.py`. A run fails before any device work when it
needs a setting the route cannot apply. A target can opt out by listing the setting under
`runsOn.ignoredSettings` with a reason. The run then goes ahead without the setting.

This table leaves out the permission settings. The capability table
already covers them. A ✅ means the route applies the setting.

<!-- settings-table -->
| Setting | iOS | Android | Notes |
|---|---|---|---|
| `erase` | ✅ | ✅ | Android has no device-wide wipe. `erase` uninstalls the app and clears its data with `pm clear`. |
| `install` | ✅ | ✅ | |
| `seedPhotos` | ✅ | ✅ | Android empties a directory Bajutsu owns in shared storage, pushes the images, and rescans it ([BE-0479](../roadmaps/BE-0479-android-seed-photos/BE-0479-android-seed-photos.md)). |
| `locale` | ✅ | ✅ | Android sets `runsOn.android.locale` as a per-app locale, on Android 13 (API level 33) and later (BE-0478). System dialogs keep the emulator's language. |
| `launchEnv` | ✅ | ✅ | Android passes the variables to the app as intent extras. |
| `launchArgs` | ✅ | n/a | `am start` takes no launch arguments. `launchEnv` carries values to the app instead. |
| `deeplink` | ✅ | ✅ | |

## Differences in behavior

Neither table above can show the differences below. Each one sits inside a feature both backends
declare, or outside what the two tables cover.

| Behavior | iOS | Android | What it means today |
|---|---|---|---|
| Text in an editable field | The element's `value` holds the text. | `value` holds the text too (#2388), with two gaps: the `uiautomator dump` fallback carries no `hint`, so an empty field's hint reads as its value; and no dump carries `isShowingHintText`, so text equal to the field's hint reads as no value. | On the fallback read path a `value` assertion on an empty field reads its hint, and a `clear` step does nothing on a field whose text equals its hint. See [drivers](drivers.md). |
| `select` and `copy` | XCUITest selects and copies natively. | adb sends Ctrl+A and Ctrl+C through `input keycombination`, which needs Android 12 or later. | The Android end-to-end (E2E) lane runs the pair on Compose and Views. It reads the copied text back from the clipboard. |
| `paste` | XCUITest taps the edit menu's Paste item, raised by a long press on the field. The menu follows the app's localization, so the driver accepts UIKit's Paste label in any language. | adb sends Ctrl+V through `input keycombination`, which needs Android 12 or later. | iOS does not use Cmd+V, which pastes nothing from a clipboard another process wrote, such as a `setClipboard` seed. Depending on the Simulator build, the edit-menu paste raises the paste-consent prompt, which a `paste` system-alert interrupt answers. On the iOS Simulator a `setClipboard` also leaves in-app select-all and copy doing nothing until the device is erased, so the iOS lane runs `paste.yaml` after `text_editing.yaml`. |
| `setLocation` | `simctl` sets it on the Simulator. | The emulator console sets it on a local emulator. Elsewhere, a receiver inside the app mocks the `gps` and `network` providers after adb grants the mock-location app-op ([BE-0481](../roadmaps/BE-0481-android-device-set-location/BE-0481-android-device-set-location.md)). | iOS does not set the location on a real device yet; [BE-0475](../roadmaps/BE-0475-ios-runner-set-location/BE-0475-ios-runner-set-location.md) covers it. On a real Android device, an app without BajutsuAndroid fails the step. |
| Clipboard steps | `simctl` reads and writes the pasteboard. | A receiver inside the app does the work. The app must link BajutsuAndroid, and Android 10 or later lets only the focused app read the clipboard. | An app without BajutsuAndroid fails every clipboard step, and so does a read while the app is in the background. |

## How the test checks the tables

`tests/test_platform_parity.py` reads the rows after the `parity-table` marker. It reads them from
this page and from its mirror under `docs/ja/`. Four checks run on every `make check`:

- The rows name every capability token, no more and no fewer.
- A cell reads ✅ if the backend's `CAPABILITIES` constant holds the token, and not otherwise.
- Every mark other than ✅ carries a note.
- Both language versions of the page carry the same marks.

When a change makes a backend declare a new capability, the test fails until the cell turns to ✅.
The same holds the other way. A new capability token needs a new row before the test passes.

The test checks the rows after the `settings-table` marker the same way. Their source is the
`ios-simulator` and `android` routes of the support table. A cell reads ✅ when the route supports
the setting or already satisfies it. A new setting key needs a new row. A route that starts to apply
a setting needs its cell turned to ✅.

The same test also checks the four-backend capability table in
[Drivers](drivers.md#capabilities-capability). That table needs a row for every token.
Each ✅ in it must match what the backend declares, for all four backends.
