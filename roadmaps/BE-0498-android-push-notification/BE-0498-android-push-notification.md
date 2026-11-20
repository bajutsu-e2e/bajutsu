**English** · [日本語](BE-0498-android-push-notification-ja.md)

# BE-0498 — Deliver a push notification to the app on Android

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0498](BE-0498-android-push-notification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0498") |
| Implementing PR | [#2492](https://github.com/bajutsu-e2e/bajutsu/pull/2492) (units 1–6) |
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

Each app chooses one of the two paths when it calls `startPush`. The app's own wiring decides, not
Bajutsu. An app that registers a handler gets its foreground code exercised. An app that registers
none gets a local notification, which exercises the background route. BajutsuAndroid stays free of
any Firebase dependency.

The showcase registers a handler, so its suite exercises the handler path. CI does not cover the
default path. The Unit 4 emulator probe covers it.

Once this item ships, the difference is observable on an Android emulator. The basic
scenario of `push.yaml` runs on the adb backend and passes. A new Android scenario pushes a titled payload, then asserts
the title the showcase app displays. The `deviceControl.push` row of the parity table shows Android as supported. The table lives in
`docs/platform-parity.md` and `docs/ja/platform-parity.md`.

## Detailed design

### Payload mapping

The scenario keeps the APNs-shaped payload that iOS uses. bajutsu maps it to three fields on the
host, in Python, so the fast test suite covers the mapping without a device.

| APNs payload | Android field | Notes |
|---|---|---|
| `aps.alert` as a string | `body` | `title` stays unset |
| `aps.alert.title` | `title` | |
| `aps.alert.body` | `body` | |
| no `aps.alert`, or an alert with neither `title` nor `body` | `title` and `body` unset | see the data-only rule below |
| every top-level key other than `aps` | `data` entry | a string value passes as-is; other values pass as JSON text |
| `aps.badge`, `aps.sound`, `aps.alert.subtitle`, other `aps` keys | — | ignored, with no Android counterpart |

`data` is a string-to-string map, like the data map of an FCM message. An app that rebuilds an FCM
message from it needs no type conversion.

The `Push` model in `bajutsu/common/scenario/models/actions/push.py` validates three rules. They fail
at scenario load on every backend, before any device work. The rules measure the authored payload:

- The payload must carry an `aps` key, as `simctl push` requires. The `aps` value must be an object.
  `aps.alert`, when present, is a string or an object. When `aps.alert` is an object, its `title` and
  `body`, when present, are strings. The types are the same on every backend. A wrong shape fails at load.
- The payload must not exceed 4,096 bytes. The measure is the length of `json.dumps(payload)` with
  default arguments. The iOS path writes that same text to the file it hands to `simctl push`. The text
  escapes characters above code point 127, so characters equal bytes.
- Every payload value must be serializable by `json.dumps`.
  The scenario loader resolves timestamps, so an unquoted `at: 2024-01-01` loads as a `datetime.date`.
  `json.dumps` raises `TypeError` on it. Pydantic wraps `ValueError` and `AssertionError` from a
  validator but not `TypeError`, so that error would escape as a traceback.
  The shared size function catches it and raises `ValueError`. The message names the offending key path
  and no value. An unquoted date then fails at load with a clean validation error.

`simctl push` already rejects a missing `aps` key and an oversized payload itself, after device work
starts and with its own message.
The model validator fails earlier and names the cause. The mapping holds no copy of either rule.
The measure counts the escaped text that the iOS path hands to `simctl push`.
That matches what `simctl` checks.
For characters above code point 127 the limit is stricter than the APNs limit.
APNs counts encoded bytes.
The limit also keeps the broadcast's `adb shell` argument well inside the shell's length limit.

A `${vars.*}` or `${secrets.*}` token can grow the payload after load. The runner re-validates a
substituted step. Here `_interp_step` follows the `eval` precedent (BE-0472). It lives in
`bajutsu/common/orchestrator/substitution.py`. That code hands `Step.model_validate` the authored
`eval`, not the substituted one. A `ValidationError` would carry `input_value=` with the substituted
text, which may be secret.

`_interp_step` does the same for `push`. It restores the authored `push` before `model_validate`.
The model validator never sees substituted text.
`_interp_step` then attaches the substituted payload with `model_copy`, as for `extractBody`.
It raises nothing. A raise there would escape the step. The step runner catches
`UncoveredSystemAlertLocale` around it. `run_scenario` converts three errors: `ControlChannelError`, `UnsupportedAction`, and
`RunCancelled`. A `ValidationError` or a `DeviceError` would abort `run_all`.
The abort discards every scenario's result.

The push handler `_do_push` re-checks instead, as the `eval` handler does.
It measures the payload with the size function that the validator shares.
Over the limit, it raises `base.SelectorError`. The step runner turns that into a failing step.
The error names the measured size and the limit.
For example: "push payload is 4,180 bytes; the limit is 4,096". It quotes no payload and no value.
Substitution rewrites values, never keys, so it cannot add or remove `aps`. The `aps` presence rule needs
no second check. The type rules need none either.
Every run-time binding is a string (`_interp_step` takes `Mapping[str, str]`).
So a `${vars.*}` or `${secrets.*}` token yields a string.
Load time substitutes a non-string `${params.*}` or `${row.*}` value.
The `Push` validator then runs again on that expanded step (`expand.py`).
The mapper can assume `aps` is an object and the three fields have validated types.
It can also assume `alert`, `title`, and `body` have the validated types.
The measure after substitution keeps the `adb shell` argument bounded.

A payload can be valid yet carry no alert, such as a `content-available` silent push. Then the
mapping yields neither `title` nor `body`. The handler path still receives the `BajutsuPush`, with
`title` and `body` null and `data` filled. The default path posts no notification and returns
`RESULT_OK`, because there is nothing to show. It returns before it checks the notification permission.

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
| No handler, `Bajutsu.startPush(context)` | posts a local notification, or nothing when `title` and `body` are both unset |

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
| `RESULT_PUSH_FAILED` (`5`) | the handler threw; the result data carries the exception message, base64-encoded like the clipboard `get` result. bajutsu adds no payload text of its own and passes the handler's message through verbatim: the app author controls it, and it is the most useful diagnostic. The handler holds the *substituted* payload, so bajutsu cannot tell a safe message from one that echoes `${secrets.*}` text. Unit 1's README states that a handler's exception message must not include the payload, and bajutsu does not redact it | the handler's message |
| unset (`0`) | no receiver handled the broadcast | `BajutsuAndroid/README.md`, and that the app must be running |

On the default path, the order matters. A payload with neither `title` nor `body` returns
`RESULT_OK` without a permission check. A payload with something to show checks the permission, and
that payload alone can return `4`. The check uses `NotificationManagerCompat.areNotificationsEnabled()`.
That call covers the `POST_NOTIFICATIONS` runtime permission on API 33 and later. It also covers a
user who turned notifications off on an older API level.

An app on the default path must declare this in its debug or test manifest:
`<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>`. Without it, `areNotificationsEnabled()` stays false on API 33 and later, and `pm grant`
fails. The library manifest declares nothing, because it merges into release builds. The showcase
apps already declare the permission. The Unit 1 README update and the result `4` message say so.

bajutsu never grants the permission itself. A scenario sets the permission state with the
`permissions` precondition. The adb backend already maps it to `POST_NOTIFICATIONS`. The handler
path skips the check, because what a handler does with the payload is the app's decision.

The app registers the receiver at run time in `Application.onCreate`, like the other two receivers.
A broadcast reaches the receiver while the app's process runs, and at no other time. iOS can deliver a push to an
app that is not running; this item does not cover that case on Android. The "no receiver" message
names it, so a scenario that pushes before launch fails with a clear cause.

Delivery after a `background` step needs a check on API 34, the CI emulator image. Android can defer
broadcasts to a receiver registered by context in a cached process. An ordered `am broadcast` sent
after a `background` step may then block or arrive late. The Unit 4 probe tests this case, and Progress
records the outcome. The outcome decides the wording of the documentation:

- If the broadcast arrives without delay, the docs unit lists the case as supported.
- Otherwise, the docs unit lists the case as unsupported.

The host bounds the broadcast in both outcomes, as the next section describes.

`Bajutsu.startPush` mirrors `Bajutsu.startClipboard`. The app calls it from `Application.onCreate`,
in a test or debug build alone. The receiver is exported with no permission, so a release build must
never register it. A second call is a no-op.

### Host: commands and environment

This section covers `bajutsu/common/backend_cli/adb/`.

- `_functions.py` gains `PUSH_ACTION` and the two new result codes, mirrored from `Bajutsu`.
- `_functions.py` gains `push_broadcast_cmd(serial, package, title, body, data)`. It mirrors
  `_clipboard_broadcast_cmd`.
- `_functions.py` gains `map_push_payload(payload)`, which applies the mapping table above. The size
  limit lives in the `Push` model, not here.
- `_functions.py` gains `parse_push_result(out)`, which raises `DeviceError` with the cause from the
  result table above. The message omits the broadcast argv, which carries the base64 payload.
  The parser builds the cause from the result code and data.
- `Env` in `env.py` gains `push(package, payload)`, which maps, broadcasts, and parses.
- The broadcast always runs under a timeout. Today `real_run` sets none, and `RunFn` is
  `(list[str]) -> str`. `_functions.py` gains `PUSH_BROADCAST_TIMEOUT_S = 10` and a runner that
  passes it to `subprocess.run`. The runner turns `subprocess.TimeoutExpired`, `subprocess.CalledProcessError` (a non-zero adb exit, such as
  a device that is offline or not found), and `OSError` into a `DeviceError`. The message carries the exit
  code and stderr, and never the argv. The runner builds it itself and does not call `device_error()`,
  which quotes the command. It raises the error `from None`, so the traceback chain does not carry the
  argv either.
- The device-control comment block in `env.py` (lines 256-262) leaves `push` and `clearKeychain`
  unwired. The block opens "Device control: the Android peer". The comment now says BajutsuAndroid's push receiver delivers `push`.
  `clearKeychain` alone stays unwired.
- `Env.__init__` in `env.py` gains an optional `push_run: RunFn | None` parameter. `Env.push` calls
  the runner it resolves and never `subprocess.run`. When the caller passes no `push_run`, `Env`
  derives it from `run`. Under `real_run` it takes the bounded runner. Under any other `run` it takes
  that `run` itself. A caller that fakes `run` thus fakes the push broadcast too. None of the
  `adb.Env(serial, run=…)` sites in `bajutsu/` and `tests/` changes. None of them can reach a real
  device by leaving `push_run` out. A unit test fakes `subprocess.run` under the bounded runner to
  cover the timeout error.
- The clipboard (BE-0233) and location (BE-0481) broadcasts stay on the unbounded `real_run`. Their
  errors keep the `device_error()` shape. Push alone needs the bound, because its receiver calls the app's
  handler. A handler runs arbitrary app code and can block. The other two receivers run library code
  that answers at once. One bounded runner for all three would give one error shape. It would also
  change two shipped steps, so it stays out of scope here. The Unit 4 probe gives evidence for it. If a
  push broadcast blocks after `background` on API 34, the other two can block the same way.
- `bajutsu/common/scenario/models/actions/push.py` gains a validator on `Push`. It enforces the three rules above.
  The size measure lives in one function that the validator and `_do_push` share. That function catches the
  `TypeError` from `json.dumps` and raises `ValueError` naming the offending key path, never the value.
- The `Push` model docstring in `bajutsu/common/scenario/models/actions/push.py` changes. It names
  both routes: `simctl push` on iOS and the BajutsuAndroid receiver on Android.
- `bajutsu/common/orchestrator/substitution.py` changes in `_interp_step`, as for `eval`. It
  validates the authored `push`, then attaches the substituted payload. It raises nothing.
- `_do_push` measures the payload with the shared size function.
  It lives in `bajutsu/common/orchestrator/actions/handlers/device.py`. Over the limit, it raises `base.SelectorError`.
  The message holds the size and the limit, nothing else.
- Existing fixtures and examples that lack `aps` change in the same unit. One exists: the payload
  `{}` in `test_device_control_and_helper_steps_are_labeled_todo`, in
  `tests/test_codegen_uiautomator.py`. It becomes `{ aps: { alert: hi } }`. Every other push payload
  in `tests/`, `demos/`, `docs/`, and `bajutsu/` already carries `aps`.
- The UI Automator generator, `bajutsu/codegen/uiautomator.py`, keeps its labeled `// TODO` for
  `push`, but the reason it gives is now false. The comment says there is "no adb device control". It
  becomes `// TODO: push — not generated; run it with bajutsu run`. The same test asserts a
  prefix, `// TODO: push`, today. It now asserts the full new string.

### Host: device control and capability

`android_device_control` in `bajutsu/common/platform_lifecycle/device_control.py` routes `push` to
`Env.push(package, payload)`, in place of the current `UnsupportedAction`. It builds `Env` from its
existing `env_run`, as today, and passes no `push_run`. The default above then applies. Under
`real_run` the push takes the bounded runner, and under a fake `env_run` it reaches that fake.
`tests/test_android_device_control.py` already fakes `env_run`, so its push cases need no new
parameter.

`AndroidEnvironment` builds this control in `controller`. It passes its `adb_run` on as `env_run`.
Some tests build environments with a fake `adb_run`. They live in `tests/test_adb_lifecycle.py` and
`tests/test_android_mock_location.py`. A push there reaches that fake. Neither `android_device_control` nor
`AndroidEnvironment` gains a parameter. The class lives in
`platform_lifecycle/environments/android/android_environment.py`.

The `push` of `android_device_control` also converts errors.
It catches `adb.DeviceError` around `e.push(package, payload)`.
It then raises `base.SelectorError(str(exc)) from None`.
This covers results `0`, `4`, and `5`. It covers a timeout and a non-zero adb exit.
The message is the cause.

Nothing between `_do_push` and `run_all` handles `DeviceError`.
It subclasses `RuntimeError`, so the run would abort.
The step loop in `loop/_functions.py` handles `SelectorError`.
Each outcome then fails its step with the cause, and the run continues.

The conversion does not live in `_do_push`. The orchestrator is backend-agnostic
(`orchestrator/types/device_control.py`). It imports neither `backend_cli` nor `devices.errors`.
A catch of the base `DeviceError` there would change iOS.
Every `simctl` push error would fail its step, including `simctl.DeviceTimeout`.
BE-0363 lets that one propagate. So `_do_push` gains no catch, and iOS keeps its behavior.
The size check in `_do_push` keeps raising `base.SelectorError` for every backend.

`AdbDriver.CAPABILITIES` in `bajutsu/common/drivers/adb/adb_driver.py` gains `deviceControl.push`.
The capability preflight then admits a `push` step on the adb backend. `clearKeychain` stays
unsupported.

### Generated UI Automator code

This item does not generate `push` in UI Automator code. The generator keeps its labeled
`// TODO`, reworded as above. The APNs-to-Android mapping lives in Python, on the machine that runs
bajutsu. Generated code would need its own copy of it, and the two copies could drift. A later
item can add the route, following the clipboard codegen precedent.

### Showcase

Both Android twins, `demos/showcase/android/compose` and `demos/showcase/android/views`, call
`Bajutsu.startPush` with a handler. The handler stores the pushed title in the app model. A screen
shows it in an element with the id `perm.push.received`. The Views twin names it `perm_push_received`,
because an Android resource id allows no dot. The `perm.` prefix places it in the Permissions
namespace, beside `perm.location.reading`.

Each twin places that element on the Permissions tab, the screen BE-0481 used for
`perm.location.reading`. No visual baseline or golden covers that tab. `visual_android.yaml`
compares `stable.png`, and `golden.yaml` checks `goldens_android/lists.json`. Both cover the Stable
tab, so the element stays off it. The element exists before any push, with an empty value. The
scenario waits on that value to change. The id is the contract.

- `demos/showcase/SPEC.md` and `SPEC.ja.md` list `perm.push.received` in section 5.4, the
  Permissions tab. The entry sits beside `perm.location.reading`. The readout is Android-specific and
  shows the last pushed title. It stays empty until a push arrives.

- The foreground-banner scenario moves out of `push.yaml` into a new iOS-only
  `demos/showcase/scenarios/push_banner.yaml`. The scenario needs `handleSystemAlert` and a
  SwiftUI-only launch environment. The Android lanes run each file whole, with no tag filter.
- `push.yaml` keeps the basic scenario alone and drops its iOS-only note. Its selectors gain the
  Views form, `[stable.row.1, stable_row_1]`, as the selectors of `device.yaml` already do.
- The `actuation` job in `.github/workflows/ios-e2e.yml` lists `push_banner.yaml` after `push.yaml`.
  The `systemalert` exclusion comments in `demos/showcase/Makefile` name the new file.
- A new `demos/showcase/scenarios/push_android.yaml` pushes `{ aps: { alert: { title, body } } }`
  then waits for the title to show:
  `wait: { for: { id: [perm.push.received, perm_push_received], value: <title> } }`. It reaches the tab as
  `location_android.yaml` does: it taps `{ label: "Permissions", traits: [button] }`, waits for
  `[perm.requestNotif, perm_requestNotif]`, then scrolls to the readout. It asserts after the
  wait, as `location_android.yaml` does. The file carries the `android` tag, as the other on-device
  `*_android.yaml` scenarios do, so the iOS bulk lanes exclude it.
- `demos/showcase/android/Makefile` adds `push` and `push_android` to both `E2E_SCENARIOS` and
  `E2E_VIEWS_SCENARIOS`.
- Stale comments change to match, one edit each:
  - `demos/showcase/scenarios/device.yaml` (lines 11-15) says push has no Android counterpart.
    The comment includes "has no faithful". Line 16
    is the run command and stays.
  - `demos/showcase/android/Makefile` (line 86, "half lives in push.yaml") limits the push half to iOS.
  - `.github/workflows/ios-e2e.yml` (lines 621-624) counts the `actuation` job's scenarios.
    The comment says "push.yaml holds two since BE-0416". `push.yaml` no longer holds two.
    The total stays at twenty-three scenario runs, because the banner scenario moves between files.
    The file count goes from eleven to twelve.
    "eleven files" becomes "twelve files", and the "push.yaml holds two" clause goes.
  - `.github/workflows/ios-e2e.yml` (lines 680-681, `push.yaml (BE-0281)`) limits `push.yaml` to iOS.
    Unit 5 rewords this bullet.
    It adds a `push_banner.yaml (BE-0416)` bullet to the per-file comment list (lines 670-700).
  - `.github/workflows/ios-e2e.yml` (lines 612-614) lists the scenarios that trigger the BE-0322
    `actuation` job. The list gains `push_banner`.
  - `demos/showcase/ios/scenarios-noax/push.yaml` (lines 1-4) and
    `demos/showcase/ios/scenarios-noax/device.yaml` (line 5) say `push` has no Android counterpart.
  - `demos/showcase/ios/swiftui/Sources/ShowcaseApp.swift` (lines 18-20) comments on the delegate.
    It says `push.yaml`'s "first scenario" would raise a banner with the delegate on for every mode.
    That scenario is the basic one. After the split, `push.yaml` holds one scenario.
    The comment now says `push.yaml`'s scenario.
  - `demos/showcase/scenarios/push.yaml` (line 24) has a BE-0416 comment.
    It moves with the banner scenario.
    It says the first scenario above never raises a banner.
    No scenario sits above it in the new file.
    The comment names the scenario in `push.yaml` instead.
  - `tests/test_android_device_control.py` (lines 406-407) has a BE-0416 comment.
    It says `push.yaml` carries a second scenario. After the split the file holds one.
    The sentence goes, and the test keeps its lookup by name.

  The `-noax` twin `scenarios-noax/push.yaml` holds the basic scenario alone. It has no banner twin.
  The `-noax` lanes run the whole directory with `--exclude android` and no other filter. Nothing
  there needs splitting, so the item adds no `scenarios-noax/push_banner.yaml`. Its comments change.

`tests/test_showcase_fixtures.py` gains two entries in its fixture test.
The test is `test_dedicated_lane_scenarios_carry_their_exclusion_tag`. The entries are
`("push_banner.yaml", "systemalert")` and `("push_android.yaml", "android")`.
They keep the new files out of a bulk lane that would fail them.

The showcase registers a handler, so its scenarios exercise the handler path. The default path
gets its on-device check in Unit 4 instead. The probe builds a showcase twin locally. Its
`startPush(this)` call registers no handler, and the build is never committed. The probe posts a
notification on an emulator, then confirms the permission failure with notifications revoked.

The push after a `background` step on API 34 runs through bajutsu, once Unit 3 has landed. Unit 4
records all three results in this item's Progress log.

### Documentation

Each documentation change lands in the unit whose code makes the old text false. Between two PRs, a
page would otherwise contradict the code it describes. The same-change rule in `CLAUDE.md`
(BE-0113) exists to prevent that.

Unit 2 adds the payload rules, so it updates the pages that state the payload's shape:

- `docs/dsl-grammar.md` (line 261) and `docs/ja/dsl-grammar.md` (line 259) state the `push` grammar.
  The payload there is `map(string,any)`, with no constraint. Both pages now record the load-time
  rules. The payload needs an `aps` object. `alert` is a string, or an object with string `title` and `body`. The
  payload stays within 4,096 bytes.
- The `### setLocation / push (device control)` section documents the same rules. It sits at
  `docs/scenarios.md` lines 1621-1630 and `docs/ja/scenarios.md` lines 1150-1158. The measure is
  the default `json.dumps` text. A step whose substituted payload grows past the limit
  fails at run time.

Unit 3 makes `push` work on Android. Every page that says otherwise changes in the same unit:

- The capability tables and the note under them. The note says the emulator backs all but `push`
  and `clearKeychain`. It sits at `docs/drivers.md` lines 106-108 and `docs/ja/drivers.md` lines
  99-100. It now names `clearKeychain` alone. In both parity pages, the `deviceControl.push` row
  turns to ✅ on Android. The work breakdown gives the details.
- The adb section of `docs/drivers.md` lists what adb advertises (lines 120-131). The list gains
  `deviceControl.push`, delivered through BajutsuAndroid. The next sentence (line 133) says `push`
  and `clearKeychain` stay unadvertised. It now names `clearKeychain` alone. `docs/ja/drivers.md`
  (line 122) changes the same way.
- The adb section of `docs/drivers.md` has a **Device control** bullet at lines 900-901. It ends
  with "The rest of the family stays unsupported". The bullet adds `push`, delivered through
  BajutsuAndroid. Its mirror at line 700 of `docs/ja/drivers.md` changes the same way.
  `clearKeychain` stays the one unsupported operation.
- Two `push` passages in `docs/scenarios.md` and `docs/ja/scenarios.md`.
  - The step-table row: `docs/scenarios.md` line 1049, `docs/ja/scenarios.md` line 753. It says
    `simctl push` on iOS and the in-app BajutsuAndroid receiver on Android.
  - The `### setLocation / push (device control)` section gains the Android mapping. It also gains
    the permission rule. On iOS both steps drive the Simulator through `simctl`. On Android,
    `setLocation` uses the emulator console or the in-app receiver (BE-0481). `push` uses the
    BajutsuAndroid push receiver.
- `docs/codegen.md` (line 712) and `docs/ja/codegen.md` (line 693) claim `push` has no adb route.
  They now say it works under `run`. Generated UI Automator code leaves it to `bajutsu run`.
- `docs/architecture.md` (about line 441) and `docs/ja/architecture.md` (about line 430) claim
  `push` needs a receiver in the app's process. Both describe the route through BajutsuAndroid.
- `docs/platform-parity.md` and `docs/ja/platform-parity.md` (about line 17) name
  `deviceControl.push` as an example capability gap. They name `handleNotificationBanner` instead,
  because it stays a gap in the table.
- Two Flutter notes stop limiting `push` to iOS: `docs/drivers.md` (about line 1081) and
  `docs/ja/drivers.md` (about line 838). Both say why the Flutter Android lane still excludes
  `push`. The Flutter app does not link BajutsuAndroid.

Unit 5 changes the showcase lanes, so it updates the pages that describe them:

- The `android-e2e.yml` row of `docs/ci.md` (en + ja) says the smoke job now runs `push.yaml` and
  `push_android.yaml`. It no longer limits the `push` half to iOS.
- The `ios-e2e.yml` row of `docs/ci.md` (en + ja) names `push_banner.yaml` beside `push.yaml`.
- The comment in `demos/showcase/Makefile` (about lines 553-560) stops limiting `push` to iOS. It
  says why the Flutter Android lane still excludes `push`, as the Flutter notes do.

Unit 6 records the Unit 4 outcome. A push after a `background` step on API 34 is either supported or
unsupported:

- The **Device control** bullet of `docs/drivers.md` and `docs/ja/drivers.md` states the outcome.
- The "Differences in behavior" table gains a `push` row, in `docs/platform-parity.md` and its
  `docs/ja/` mirror.
  - iOS: `simctl push` delivers the APNs payload.
  - Android: the in-app receiver maps the payload. It then calls the app's handler or posts a
    local notification.
  - Notes: the app must be running and link BajutsuAndroid. The default path needs `notifications`
    granted. The row also gives the Unit 4 outcome.

`BajutsuAndroid/README.md` and `README.ja.md` belong to Unit 1, not to this section. Unit 1
documents `Bajutsu.startPush`, the handler, the result codes, and the `POST_NOTIFICATIONS`
declaration.

### Work breakdown

| # | Unit | Files | Done when |
|---|---|---|---|
| 1 | Push receiver, handler, and `startPush` | `BajutsuAndroid/`, including `README.md` and `README.ja.md` | the library builds; both READMEs cover the API and the permission, and state that a handler's exception message must not include the payload |
| 2 | Mapping, command builder, result parser, bounded run path, `Env.push`, `Push` model validator, fixtures without `aps`, the true reason in the UI Automator `push` placeholder | `bajutsu/common/backend_cli/adb/`, `bajutsu/common/scenario/models/actions/push.py`, `bajutsu/common/orchestrator/substitution.py`, `bajutsu/common/orchestrator/actions/handlers/device.py`, `bajutsu/codegen/uiautomator.py`, `tests/test_codegen_uiautomator.py`, new unit tests under `tests/`, `docs/dsl-grammar.md` and `docs/ja/dsl-grammar.md`, and the payload-rule passage of `docs/scenarios.md` and `docs/ja/scenarios.md` | unit tests cover every row of both tables, the `aps`, type, and size rules (a validator test fails each of `{aps: "x"}`, `{aps: {alert: 5}}`, and `{aps: {alert: {title: {a: 1}}}}` at load; a payload carrying an unquoted date fails at load with a `ValidationError`), and the timeout error and the non-zero-exit error; an `Env` built with a fake `run` and no `push_run` sends the push broadcast through that fake; a test grows a `${vars.*}` and a `${secrets.*}` payload past 4,096 bytes during substitution, and the test asserts a failing step, not an aborted run, and the error text that bajutsu writes holds no payload text; unit tests show that results `0`, `4`, `5`, a timeout, a non-zero adb exit, each raise `adb.DeviceError`, and the text bajutsu writes into the message holds no payload text (result `5` passes the handler's message through verbatim); the placeholder test asserts the full new string, not its prefix; the existing suite passes |
| 3 | Device control, capability, capability tables | `device_control.py`, `android_environment.py`, `adb_driver.py`, `bajutsu/common/drivers/base/capability.py`, `bajutsu/common/capability/capability_preflight.py`, `docs/platform-parity.md` and `docs/ja/platform-parity.md` (the behavior-table row aside), `docs/drivers.md`, `docs/ja/drivers.md`, the step row and Android passage of `docs/scenarios.md` and `docs/ja/scenarios.md`, `docs/codegen.md`, `docs/ja/codegen.md`, `docs/architecture.md`, `docs/ja/architecture.md`, `tests/test_android_device_control.py`, `tests/test_capability_preflight.py` | preflight admits `push` on adb; results `0`, `4`, `5`, a timeout, a non-zero adb exit, each give a failing step through `android_device_control`, not an aborted run, and the text bajutsu writes into the message holds no payload text (result `5` passes the handler's message through verbatim); `tests/test_platform_parity.py` and the unit tests pass |
| 4 | Probe the default path and background delivery | none committed (Progress log) | a posted notification, the permission failure, and a push after `background` on API 34 are observed |
| 5 | Showcase handler, readout, scenarios, stale comments | `demos/showcase/`, `demos/showcase/SPEC.md`, `demos/showcase/SPEC.ja.md`, `.github/workflows/ios-e2e.yml`, `tests/test_showcase_fixtures.py`, `tests/test_android_device_control.py` (comment), `docs/ci.md` and `docs/ja/ci.md` | `push.yaml` and `push_android.yaml` pass on the Android emulator; `push_banner.yaml` passes on iOS; both showcase spec files list `perm.push.received`; the fixture test pins the two new tags |
| 6 | The Unit 4 outcome in the documentation | the **Device control** bullet of `docs/drivers.md` and `docs/ja/drivers.md`, the behavior table of `docs/platform-parity.md` and `docs/ja/platform-parity.md` | `make check` passes |

Unit 4 builds a throwaway showcase twin whose `startPush(this)` call registers no handler. That is
the one way to reach the default path, and the build stays local. Unit 4 follows Unit 3, because the
push-after-`background` case runs through bajutsu, which needs the capability.

Unit 3 inverts the tests that pin the old rejection in `tests/test_android_device_control.py`:

- `test_push_scenario_is_rejected_by_preflight_on_adb` becomes
  `test_push_scenario_is_admitted_by_preflight_on_adb` and expects preflight to admit `push.yaml`.
- `test_adb_advertises_the_device_control_subset_it_can_honor` now asserts `DC_PUSH` is in `caps`.
- The `push` parameter of `test_preflight_rejects_unsupported_steps_on_adb` moves to
  `test_preflight_admits_supported_steps_on_adb`.
- The `push` cases that expect `UnsupportedAction` now expect delivery through `Env.push`.
- The comments that restrict `push.yaml` to iOS change with them.
- The module docstring of that test file (lines 7-8) says `push` stays unsupported. It changes to say Android
  delivers it through the in-app receiver.
- The docstring of `android_device_control` (`device_control.py`, lines 90-93) says `push` raises.
- The `CAPABILITIES` comment (`adb_driver.py`, lines 2543-2544) says `push` is not advertised. It reads "all but `push`".
- The `AndroidEnvironment` docstring is in `android_environment.py`, lines 111-114.
  It lists the supported subset without `push`.
  It ends "the rest of the family stays unsupported".
  It adds `push`, delivered through BajutsuAndroid's push receiver.
  `clearKeychain` stays the one unsupported operation.
- The `controller` comment (`android_environment.py`, lines 591-596) says `push` raises. It opens "The subset Android can honor".
- The comments in `bajutsu/common/drivers/base/capability.py` (lines 94-96) and
  `bajutsu/common/capability/capability_preflight.py` (lines 43-45) name the Android emulator as a
  backend that backs a subset of the family. They describe such a backend generically instead.
- `tests/test_capability_preflight.py` describes `_SUBSET` as the Android emulator (lines 46-48).
  It also uses `push` as what the emulator cannot honor (lines 270 and 289). `_SUBSET` becomes a
  synthetic partial backend. The test data stays unchanged, because `_SUBSET` is hard-coded.

The capability tables belong to Unit 3. The parity test (`tests/test_platform_parity.py`) fails until they match
`AdbDriver.CAPABILITIES`.

In both parity pages, the `deviceControl.push` row turns to ✅ on Android. Its note copies the neighbouring rows. Android needs BajutsuAndroid. See the behavior
table.

The parity test reads the rows after the `parity-table` and `settings-table` markers. The behavior
table is outside it. Its new row belongs to Unit 6.

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

- [x] Unit 1 — `PushReceiver`, `BajutsuPushHandler`, `Bajutsu.startPush`, and the READMEs (en + ja)
- [x] Unit 2 — the adb command path and the `Push` validator, with fixtures and payload docs
- [x] Unit 3 — Android `push` and its capability, inverted tests, and the docs that limit it to iOS
- [x] Unit 4 — probe the default path, the permission failure, and a push after `background` on API 34
- [x] Unit 5 — the showcase handler, readout, and scenarios, with their docs and comments
- [x] Unit 6 — the background-delivery outcome in the driver and parity docs (en + ja)

Log:

- 2026-11-20 — Units 1–6 landed together ([#2492](https://github.com/bajutsu-e2e/bajutsu/pull/2492)). Unit 4 ran on an API 34 emulator (Pixel 8 image) through
  `bajutsu run`, with a local showcase build whose `startPush(this)` registers no handler. The
  probe showed three things:
  - The default path posted a notification on the `bajutsu` channel. It carried the pushed title
    and body, and a content intent that opens the app.
  - With `permissions: { notifications: revoke }`, the receiver answered `4`. The step failed with
    the message that names the `permissions` precondition, and the run went on.
  - A push right after a `background` step arrived without delay, and so did one sent 25 seconds
    later. The push step took 2.6 seconds against 2.2 seconds in the foreground. The process sat in
    the previous-app state (out-of-memory adjustment 700) and was not frozen. Unit 6 lists the case
    as supported.

  Two deviations from the Detailed design:
  - The showcase handler stores the title in a process-wide `PushInbox`, not in the app model. The
    handler registers in `Application.onCreate`, before any Activity and its model exist.
  - The Android lanes ran `push.yaml` and `push_android.yaml` on both twins locally. `push_banner.yaml`
    moved unchanged, so the `actuation` job of `ios-e2e.yml` verifies it on iOS.

## References

- Issue [#2397](https://github.com/bajutsu-e2e/bajutsu/issues/2397) — the gap this item closes.
- [`docs/platform-parity.md`](../../docs/platform-parity.md) (and its [ja mirror](../../docs/ja/platform-parity.md)) — the iOS and Android capability gaps.
- [`BajutsuAndroid/src/main/java/dev/bajutsu/android/ClipboardReceiver.kt`](../../BajutsuAndroid/src/main/java/dev/bajutsu/android/ClipboardReceiver.kt)
  — the receiver pattern this item follows.
- [`demos/showcase/scenarios/push.yaml`](../../demos/showcase/scenarios/push.yaml) — the scenario
  this item makes portable.
