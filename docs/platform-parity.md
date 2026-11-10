**English** · [日本語](ja/platform-parity.md)

# iOS and Android driver parity

Bajutsu runs each platform on its own [backend](glossary.md#driver-backend-actuator-platform).
iOS runs on `xcuitest`, and Android runs on `adb`. The two backends do not support the same
features. This page lists every difference between them. Each difference is either a gap we intend to
close or a feature the platform does not need. The page records which kind each one is. The page serves
as the working list for closing the gaps.

A test keeps the first table honest. The test compares each mark with the capabilities each driver
declares. A gap cannot open or close unless this page changes with it.

## What the two tables cover

A **capability** is a named feature a backend declares through `Driver.capabilities()`. Two
examples are `multiTouch` and `deviceControl.push`. The preflight check runs before a run leases a
device. It stops a scenario that needs a capability the chosen backend lacks.
[Drivers](drivers.md#capabilities-capability) lists every capability with its meaning.

The two tables split the differences by whether a capability can express them:

- **Capability parity** lists every capability, with one mark per backend. The test checks this
  table.
- **Differences in behavior** lists differences that no capability names. Both backends can
  declare a feature that still behaves differently on one of them. People keep this table by hand.

The iOS column describes the iOS Simulator, and the Android column describes the Android emulator.
A real iOS device drops the capabilities that `simctl` provides.
[iOS on a real device and in a device cloud](ios-device-cloud.md) describes that narrowing.

## How to read a mark

Each cell of the capability table starts with one of four marks:

| Mark | Meaning |
|---|---|
| ✅ | The backend declares the capability. |
| gap | The backend does not declare it yet, and the platform could support it. The notes name the route. |
| n/a | The backend does not need it. The notes say why. |
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
| `appTrace` | ✅ | gap | iOS streams the app's `os_log`. Android could filter `logcat` to the app's process ID. |
| `multiTouch` | ✅ | ✅ | Android needs a rooted device. See the behavior table. |
| `textSelection` | ✅ | ✅ | No Android run has checked the result on a device. See the behavior table. |
| `webView` | per-run | gap | iOS grants it when the app embeds the BajutsuKit WebView bridge. Android has no bridge. Chrome's WebView debugging port is a possible route. |
| `selectOption` | n/a | n/a | Sets a web page's `<select>` element, which the web backend alone has. |
| `urlNavigation` | n/a | n/a | Opens a web address mid-scenario in a browser. Both mobile backends open a deep link as a launch precondition instead. |
| `elementAttribute` | n/a | n/a | Reads an attribute of a web page element, which the web backend alone has. |
| `handleSystemAlert` | ✅ | n/a | An Android system dialog appears in the dumped tree, so an ordinary `tap` reaches it. |
| `resolveAlerts` | ✅ | n/a | Same reason as `handleSystemAlert`. |
| `pickerWheel` | ✅ | gap | Android's `NumberPicker` and spinner-mode `DatePicker` are the nearest controls. No setter drives them yet. |
| `appContext` | ✅ | gap | UI Automator reads any app's window, and `am start` can bring another package forward. |
| `selectPhotos` | ✅ | gap | Android's photo picker is system UI that a tap can reach. No step picks a photo by position yet. On iOS, an Apple Silicon Simulator drops the capability at run time. |
| `handleTipkitTip` | ✅ | n/a | TipKit is an iOS framework. |
| `handleNotificationBanner` | ✅ | gap | An Android heads-up notification appears in the tree as a SystemUI node. |
| `deviceGroup` | ✅ | ✅ | |
| `deviceControl.setLocation` | ✅ | ✅ | Android sets it through the emulator console alone. See the behavior table. |
| `deviceControl.clipboard` | ✅ | ✅ | Android needs the BajutsuAndroid library in the app. See the behavior table. |
| `deviceControl.push` | ✅ | gap | The BajutsuAndroid library could post the notification from inside the app, the way it already handles the clipboard. |
| `deviceControl.clearKeychain` | ✅ | n/a | Android has no keychain. The app's Android Keystore keys go away with its data on a clean reinstall. |
| `deviceControl.background` | ✅ | gap | Pressing the home key (`KEYCODE_HOME`) is the candidate route. [BE-0211](../roadmaps/BE-0211-android-device-control/BE-0211-android-device-control.md) judged it unfaithful without recording a reason. |
| `deviceControl.foreground` | ✅ | ✅ | |
| `deviceControl.statusBar` | ✅ | gap | Android's SystemUI demo mode overrides the clock, battery, and network icons. |
| `deviceControl.permissions.location` | ✅ | ✅ | |
| `deviceControl.permissions.camera` | ✅ | ✅ | |
| `deviceControl.permissions.microphone` | ✅ | ✅ | |
| `deviceControl.permissions.contacts` | ✅ | ✅ | |
| `deviceControl.permissions.photos` | ✅ | ✅ | |
| `deviceControl.permissions.calendar` | ✅ | ✅ | |
| `deviceControl.permissions.notifications` | gap | ✅ | `simctl privacy` has no notifications service. A `handleSystemAlert` step answers the prompt meanwhile. |

## Differences in behavior

The capability table cannot show the differences below. Each one sits inside a feature both
backends declare, or outside the capability set altogether.

| Behavior | iOS | Android | What it means today |
|---|---|---|---|
| Text in an editable field | The element's `value` holds the text. | The text lands in `label`, and `value` holds the `content-desc`. | A `value` assertion on a text field fails to read the text. A `clear` step reads `value` first and does nothing when it is empty. |
| `select` and `copy` | XCUITest selects and copies natively. | adb sends Ctrl+A and Ctrl+C through `input keycombination`, which needs Android 12 or later. | No Android scenario runs the pair, and the conformance test checks only that neither call raises. |
| `pinch` and `rotate` | XCUITest performs both natively. | adb writes raw touch events with `sendevent`, which needs root. | Both steps fail on an unrooted device. |
| `setLocation` | `simctl` sets it on the Simulator. | The emulator console sets it. | Neither backend sets the location on a real device yet. [BE-0475](../roadmaps/BE-0475-ios-runner-set-location/BE-0475-ios-runner-set-location.md) covers iOS. |
| Clipboard steps | `simctl` reads and writes the pasteboard. | A receiver inside the app does the work. The app must link BajutsuAndroid, and Android 10 or later lets only the focused app read the clipboard. | An app without BajutsuAndroid fails every clipboard step, and so does a read while the app is in the background. |
| `locale`, `launchArgs`, and `seedPhotos` | The launch applies all three. | The launch applies none of them, and forwards `launchEnv` as intent extras. | A scenario that relies on one of the three runs on Android without it. |

## How the test checks the capability table

`tests/test_platform_parity.py` reads the rows after the `parity-table` marker. It reads them from
this page and from its mirror under `docs/ja/`. Four checks run on every `make check`:

- The rows name every capability token, no more and no fewer.
- A cell reads ✅ if the backend's `CAPABILITIES` constant holds the token, and not otherwise.
- Every mark other than ✅ carries a note.
- Both language versions of the page carry the same marks.

When a change makes a backend declare a new capability, the test fails until the cell turns to ✅.
The same holds the other way. A new capability token needs a new row before the test passes.
