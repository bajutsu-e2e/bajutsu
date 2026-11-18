**English** · [日本語](BE-XXXX-android-push-notification-ja.md)

# BE-XXXX — Deliver a push notification to the app on Android

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-push-notification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), [BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location.md) |
<!-- /BE-METADATA -->

## Introduction

The `push` step delivers a notification to the app under test on iOS and fails on Android. On the
iOS Simulator, bajutsu runs `simctl push` with an Apple Push Notification service (APNs) payload.
Android has no command that hands a notification to an app, so the adb backend leaves the
`deviceControl.push` capability unadvertised.

We propose to deliver the payload through BajutsuAndroid, the test-support library that the app
under test embeds. bajutsu sends the payload in an ordered broadcast, the same way the clipboard and
location steps already reach the app. A new push receiver inside the app hands the payload to a
handler the app registers. An app that registers no handler gets a local notification instead. The
scenario keeps the APNs-shaped payload, so one `push` step runs on both platforms.

## Motivation

A notification flow cannot be tested on Android today. The capability preflight rejects a `push`
step on the adb backend before any device work. The showcase keeps `push.yaml` iOS-only as a result.
A team that ships the same app on both platforms must keep the notification scenarios in an iOS-only
copy of its suite. The Android half of the flow goes untested.

The closest Android mechanism does not suit a deterministic run. A real Firebase Cloud
Messaging (FCM) delivery needs network access and server credentials. Its latency is outside the
run's control. A run must not depend on either.

BajutsuAndroid already solves the delivery half. Since [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md),
bajutsu reaches code inside the app process with an ordered `am broadcast`. A receiver there sets a
result code, so bajutsu can tell "handled" from "no receiver" and fail loudly.
[BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location.md) reused the
same channel for location. A push receiver needs no new channel either.

The open question is what the receiver does with the payload. Apps handle a push in two places:

- **In the background**, the system shows a notification, and tapping it opens the app through an
  intent.
- **In the foreground**, the app's own messaging code receives the message, for example
  `FirebaseMessagingService.onMessageReceived`.

A fixed choice tests one place and leaves the other untested. A handler hook lets the app route the
payload into its own messaging code. A default local notification covers an app that registers no
handler. BajutsuAndroid stays free of any Firebase dependency.

Once this item ships, the difference is observable on an Android emulator. The basic
scenario of `push.yaml` runs on the adb backend and passes. A new Android scenario pushes a titled payload, then asserts
the title the showcase app displays. The `deviceControl.push` row in `docs/platform-parity.md` shows
Android as supported.

## Detailed design

### Payload mapping

The scenario keeps the APNs-shaped payload that iOS uses. bajutsu maps it to three fields on the
host, in Python, so the fast test suite covers the mapping without a device.

| APNs payload | Android field | Notes |
|---|---|---|
| `aps.alert` as a string | `body` | `title` stays unset |
| `aps.alert.title` | `title` | |
| `aps.alert.body` | `body` | |
| every top-level key other than `aps` | `data` entry | a string value passes as-is; other values pass as JSON text |
| `aps.badge`, `aps.sound`, `aps.alert.subtitle`, other `aps` keys | — | ignored, with no Android counterpart |

`data` is a string-to-string map, like the data map of an FCM message. An app that rebuilds an FCM
message from it needs no type conversion.

bajutsu rejects a payload larger than 4,096 bytes of JSON before it sends anything, on every backend.
APNs applies the same limit, so any payload a real app can receive passes. The iOS path through
`simctl push` enforces no limit of its own today, so the check moves into the shared `push` step
validation. The limit also keeps the broadcast's `adb shell` argument well inside the shell's length
limit.

### BajutsuAndroid: the push receiver

`PushReceiver` joins `ClipboardReceiver` and `LocationReceiver` in
`BajutsuAndroid/src/main/java/dev/bajutsu/android/`. It listens on a new action,
`dev.bajutsu.PUSH`. The broadcast carries three extras, each base64-encoded so the shell argument
needs no quoting:

- `title` — the title text, absent when unset or empty
- `body` — the body text, absent when unset or empty
- `data` — the `data` map as a JSON object

The receiver decodes the extras into a new value type, `BajutsuPush(title, body, data)`. It then
takes one of two paths:

| App setup | What the receiver does |
|---|---|
| A handler registered with `Bajutsu.startPush(context, handler)` | calls `handler.onPush(push)` and nothing else |
| No handler, `Bajutsu.startPush(context)` | posts a local notification |

The handler is a `fun interface BajutsuPushHandler`. The receiver calls the handler on the main
thread, inside `onReceive`. The handler must return promptly, like any broadcast receiver. An app that uses FCM can build a `RemoteMessage` from `BajutsuPush` and call its own
`onMessageReceived`. That code lives in the app's test build, so BajutsuAndroid never depends on
Firebase.

The default path posts the notification on a channel named `bajutsu`, created on first use. The
notification uses the app's launcher icon. Its content intent is the app's launch intent, with each
`data` entry as a string extra. Tapping the notification then exercises the app's intent
handling.

The receiver reports one of four results. The new codes `4` and `5` follow the location receiver's `2` and `3`, so every existing code keeps its meaning.

| Result | Meaning | bajutsu's error message points at |
|---|---|---|
| `RESULT_OK` (`1`) | the handler returned, or the notification was posted | — |
| `RESULT_NO_NOTIFICATION_PERMISSION` (`4`) | default path, and notifications are not enabled for the app | `permissions: { notifications: grant }` |
| `RESULT_PUSH_FAILED` (`5`) | the handler threw; the result data carries the message, base64-encoded like the clipboard `get` result | the handler's message |
| unset (`0`) | no receiver handled the broadcast | `BajutsuAndroid/README.md`, and that the app must be running |

The permission check uses `NotificationManagerCompat.areNotificationsEnabled()`. That call covers
the `POST_NOTIFICATIONS` runtime permission on API 33 and later. It also covers a user who turned
notifications off on an older API level. bajutsu never grants the permission itself. A scenario
controls the permission state through the `permissions` precondition, which the adb backend already
maps to `POST_NOTIFICATIONS`. The handler path skips the check, because what a handler does with the
payload is the app's decision.

The app registers the receiver at run time in `Application.onCreate`, like the other two receivers.
A broadcast reaches the receiver while the app's process runs, and at no other time. iOS can deliver a push to an
app that is not running; this item does not cover that case on Android. The "no receiver" message
names it, so a scenario that pushes before launch fails with a clear cause.

`Bajutsu.startPush` mirrors `Bajutsu.startClipboard`. The app calls it from `Application.onCreate`,
in a test or debug build alone. The receiver is exported with no permission, so a release build must
never register it. A second call is a no-op.

### Host: commands and environment

This section covers `bajutsu/common/backend_cli/adb/`.

- `_functions.py` gains `PUSH_ACTION` and the two new result codes, mirrored from `Bajutsu`.
- `_functions.py` gains `push_broadcast_cmd(serial, package, title, body, data)`. It mirrors
  `_clipboard_broadcast_cmd`.
- `_functions.py` gains `map_push_payload(payload)`, which applies the mapping table above and the
  size limit.
- `_functions.py` gains `parse_push_result(out)`, which raises `DeviceError` with the cause from the
  result table above.
- `Env` in `env.py` gains `push(package, payload)`, which maps, broadcasts, and parses.

### Host: device control and capability

`android_device_control` in `bajutsu/common/platform_lifecycle/device_control.py` routes `push` to
`Env.push(package, payload)`, in place of the current `UnsupportedAction`. `AdbDriver.CAPABILITIES`
in `bajutsu/common/drivers/adb/adb_driver.py` gains `deviceControl.push`. The capability preflight
then admits a `push` step on the adb backend. `clearKeychain` stays unsupported.

### Showcase

Both Android twins, `demos/showcase/android/compose` and `demos/showcase/android/views`, call
`Bajutsu.startPush` with a handler. The handler stores the pushed title in the app model. A screen
shows it in an element with the id `push.received`. The Views twin names it `push_received`,
because an Android resource id allows no dot. Each twin places that element where its existing
layout allows; the id is the contract.

- The foreground-banner scenario moves out of `push.yaml` into a new iOS-only
  `demos/showcase/scenarios/push_banner.yaml`. The scenario needs `handleSystemAlert` and a
  SwiftUI-only launch environment. The Android lanes run each file whole, with no tag filter.
- `push.yaml` keeps the basic scenario alone and drops its iOS-only note. Its selectors gain the
  Views form, `[stable.row.1, stable_row_1]`, as the selectors of `device.yaml` already do.
- The `actuation` job in `.github/workflows/ios-e2e.yml` lists `push_banner.yaml` after `push.yaml`.
  The `systemalert` exclusion comments in `demos/showcase/Makefile` name the new file.
- A new `demos/showcase/scenarios/push_android.yaml` pushes `{ aps: { alert: { title, body } } }`
  and asserts the value of `[push.received, push_received]`. The file carries `tags: [android]`,
  like every other `*_android.yaml`, so the iOS bulk lanes exclude it.
- `demos/showcase/android/Makefile` adds `push` and `push_android` to both `E2E_SCENARIOS` and
  `E2E_VIEWS_SCENARIOS`.

The showcase registers a handler, so its scenarios exercise the handler path. The default path
gets its on-device check in Unit 1 instead. That probe posts a notification on an emulator, then
confirms the permission failure with notifications revoked. Unit 1 records both results in this
item's Progress log.

### Documentation

- `docs/platform-parity.md` and its `docs/ja/` mirror flip the `deviceControl.push` row to
  supported on Android.
- The capability table and the device-control notes of `docs/drivers.md` (en + ja) add Android to
  `deviceControl.push`. `tests/test_platform_parity.py` fails until both documents match
  `AdbDriver.CAPABILITIES`.
- The `push` reference in `docs/scenarios.md` (en + ja) describes the Android mapping and the
  permission rule.
- The Flutter note in `docs/drivers.md` and the `android-e2e.yml` row of `docs/ci.md` (en + ja) stop
  calling `push` iOS-only. They give the reason the Flutter Android lane still excludes `push`: the
  Flutter app does not link BajutsuAndroid.
- `BajutsuAndroid/README.md` (en + ja) documents `Bajutsu.startPush`, the handler, and the result
  codes.

### Work breakdown

| # | Unit | Files | Done when |
|---|---|---|---|
| 1 | Probe the default path on an emulator | none (Progress log) | a posted notification and the permission failure are both observed |
| 2 | Push receiver, handler, and `startPush` | `BajutsuAndroid/` | the library builds; the README covers the API |
| 3 | Payload mapping, shared size check, command builder, result parser, `Env.push` | `bajutsu/common/backend_cli/adb/` and the `push` step validation | unit tests cover every row of both tables |
| 4 | Device control and capability | `device_control.py`, `adb_driver.py` | preflight admits `push` on adb; unit tests pass |
| 5 | Showcase handler and scenarios | `demos/showcase/`, `.github/workflows/ios-e2e.yml` | `push.yaml` and `push_android.yaml` pass on the Android emulator; `push_banner.yaml` passes on iOS |
| 6 | Documentation | `docs/`, `docs/ja/` | `tests/test_platform_parity.py` passes |

## Alternatives considered

| Alternative | Why not adopted |
|---|---|
| Post a local notification and offer no handler | Simplest, but the app's foreground handling, such as `onMessageReceived`, stays untested. The handler hook adds one small interface to cover that path. |
| Hand the payload to the app's messaging service as a `RemoteMessage` | Closest to a real delivery, but it ties BajutsuAndroid to Firebase. An app without FCM could not use the step. The handler lets an FCM app do this in its own test code. |
| Send a real FCM message | Faithful, but it needs network access and server credentials. A deterministic run must depend on neither. |
| Add Android-specific keys (`notification`, `data`) to the step | More faithful to FCM, but scenarios would split per platform and the step schema would grow. Mapping the APNs shape keeps one scenario for both platforms. |
| Grant `POST_NOTIFICATIONS` over adb before each push | Shorter scenarios, but it changes the app's permission state silently. A scenario that tests the denied state would break. The `permissions` precondition already grants it explicitly. |
| Also tap the posted notification in the system shade | Covers the full tap-to-screen flow, but it drives System UI outside the app. Selector stability and device differences there deserve their own item. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — probe the default notification path and the permission failure on an emulator
- [ ] Unit 2 — `PushReceiver`, `BajutsuPushHandler`, and `Bajutsu.startPush` in BajutsuAndroid
- [ ] Unit 3 — payload mapping, the shared 4,096-byte check, command builder, result parser, and `Env.push`
- [ ] Unit 4 — `push` in the Android device control and `deviceControl.push` in `AdbDriver.CAPABILITIES`
- [ ] Unit 5 — showcase handler, portable `push.yaml`, `push_banner.yaml`, and `push_android.yaml`
- [ ] Unit 6 — platform parity, drivers, scenarios, and BajutsuAndroid documentation (en + ja)

## References

- Issue [#2397](https://github.com/bajutsu-e2e/bajutsu/issues/2397) — the gap this item closes.
- [`docs/platform-parity.md`](../../docs/platform-parity.md) — the iOS and Android capability gaps.
- [`BajutsuAndroid/src/main/java/dev/bajutsu/android/ClipboardReceiver.kt`](../../BajutsuAndroid/src/main/java/dev/bajutsu/android/ClipboardReceiver.kt)
  — the receiver pattern this item follows.
- [`demos/showcase/scenarios/push.yaml`](../../demos/showcase/scenarios/push.yaml) — the scenario
  this item makes portable.
