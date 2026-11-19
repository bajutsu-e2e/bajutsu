**English** · [日本語](BE-XXXX-android-heads-up-notification-dismiss-ja.md)

# BE-XXXX — Report and swipe away an Android heads-up notification

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-heads-up-notification-dismiss.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md), [BE-0416](../BE-0416-ios-notification-banner-swipe-dismiss/BE-0416-ios-notification-banner-swipe-dismiss.md), [BE-0478](../BE-0478-android-device-control-parity/BE-0478-android-device-control-parity.md) |
<!-- /BE-METADATA -->

## Introduction

On iOS, Bajutsu clears a foreground notification banner that sits over the app under test
([BE-0416](../BE-0416-ios-notification-banner-swipe-dismiss/BE-0416-ios-notification-banner-swipe-dismiss.md)).
The driver reports the banner's frame. A backend-agnostic guard in the orchestrator then swipes the
banner away and waits for the banner to disappear. The Android (adb) driver reports no banner at all. An Android
heads-up notification is the banner that SystemUI slides over the running app. Such a banner can sit
on a step's target and take the step's tap.

This proposal makes the adb driver report the heads-up notification's frame and declare the
`handleNotificationBanner` capability. The existing guard then clears the banner on Android with no Android-specific code.
The proposal also adds one check that iOS does not need: before a gesture on a resolved target, the adb driver
clears a heads-up notification that covers the gesture's own point.

## Motivation

A heads-up notification can appear at any moment in a run. A server sends a push on its own schedule,
and an app's local notification follows wall-clock time, not the run's steps. When
the notification lands over a step's target, `adb shell input tap` delivers the tap to the topmost
window, which is SystemUI's heads-up window. The tap opens the notification's app and carries the run
away from the screen the scenario was testing. The run then fails on a later step, with nothing in
the report that names the banner. The defect reads as flakiness.

The gap is one declaration and one missing read, not missing machinery. The orchestrator's banner
guard already runs for any backend that declares `handleNotificationBanner`. The guard lives in
`_clear_notification_banner` (`bajutsu/common/orchestrator/loop/_functions.py`). It reads the frame,
computes an upward swipe, swipes, and polls until the banner disappears. The adb driver's
`notification_banner_frame` returns `None`, and `AdbDriver.CAPABILITIES` omits the capability. The
guard skips Android every time.

The data the driver needs already reaches the host on the resident channel. The resident server
answers a read with `UiDevice.dumpWindowHierarchy()`, which walks every window, SystemUI's
included. `narrowed_root` in `bajutsu/common/backend_cli/adb_resident/_functions.py` then discards
every `com.android.systemui` window, so that the resident read yields the same elements as
`uiautomator dump`. The heads-up window goes out along with the status bar. The comment on the adb
driver's `notification_banner_frame` claims the opposite: that a heads-up notification "reaches the
app's own tree". The resident narrowing contradicts that claim.

One showcase scenario on the Android emulator lane shows whether this proposal has landed.
The scenario posts a local notification and then taps a control that the heads-up notification
covers. Before this proposal, the tap opens the notification and the next assertion fails. After this
proposal, the tap lands on the control. The run's report also carries a banner dismissal of kind
`notificationBanner`, whichever path cleared the banner.

## Detailed design

The work splits into eight units. Unit 1 measures the device. Units 2–5 change the host. Units 6–8
cover the showcase, the tests, and the docs.

### Unit 1 — the heads-up tree, measured

Measure the hierarchy of a heads-up notification on emulators at API 33, 34, and 35, and record the
result in this item. Unit 2 keys on the result, so the measurement comes first. Record four facts:

- which SystemUI window holds the heads-up notification;
- the resource ids that mark the notification row and its bounds;
- what tells the heads-up notification apart from the expanded notification shade, which draws the
  same rows; and
- whether an upward swipe from the row's center, travelling the distance Unit 4 computes, dismisses
  the notification.

BE-0416's Unit 1 did the same for iOS, and its measurements shaped every later unit. A guessed
identifier would make Unit 2 match the wrong node or none.

### Unit 2 — the frame, read from the resident dump

`narrowed_root` keeps dropping the SystemUI windows, so the app's elements stay unchanged. Before it
drops them, it extracts the frame of the heads-up row that Unit 1 identified and returns the frame
alongside the narrowed tree. The adb driver stores the frame with the tree it read.
`notification_banner_frame` always takes a fresh read, because the orchestrator's guard calls it
repeatedly to watch the banner leave.

Unit 5 therefore gets the frame free with every read. The read already carries the SystemUI windows, and the
extraction walks only the windows that `narrowed_root` already visits. The unit needs no new endpoint and
no change to the resident server's Kotlin code.

The `uiautomator dump` fallback reads the active window alone, so it never sees the heads-up window.
On that path `notification_banner_frame` returns `None`, and the driver logs one warning per lease.
The warning says that the banner guard is off until the resident channel returns. The fallback already
warns that it runs degraded. The new warning names one more consequence.
`handleNotificationBanner` stays declared on both paths, since a capability that flips mid-lease
would break the static parity table that `docs/platform-parity.md` and
`tests/test_platform_parity.py` keep.

This unit also corrects the comment on `notification_banner_frame` in
`bajutsu/common/drivers/adb/adb_driver.py`, which claims that a heads-up notification reaches the
app's own tree.

### Unit 3 — the capability

Add `Capability.HANDLE_NOTIFICATION_BANNER` to `AdbDriver.CAPABILITIES`. The orchestrator's per-step
sweep and its sweep before a `visual` capture then run on Android with no Android-specific branch in the orchestrator.

### Unit 4 — a swipe distance in the device's own unit

`notification_banner_swipe_points` in `bajutsu/common/drivers/base/_functions.py` ends the swipe
20 units above the banner's top edge and never closer than 8 units to the screen's top. BE-0416
measured both distances in iOS points. The adb driver works in raw display pixels, where 20 pixels on
a high-density screen is too short a flick to dismiss anything.

The function gains a `scale` argument, the number of device units per point, which defaults to `1.0`.
A new narrow protocol in `bajutsu/common/drivers/base/`, following the shape of `ViewportProvider`,
lets a backend report that scale. The adb driver reports `wm density` divided by 160, the number of
pixels per density-independent pixel (dp). It caches the value for the run, as it caches `wm size`.
An iOS point and an Android dp describe nearly the same physical length, so the distances BE-0416
measured carry over. The orchestrator passes the reported scale, or `1.0` for a backend without the
protocol, and iOS behaves exactly as before.

### Unit 5 — clearing a banner before the gesture it would take

On iOS, XCUITest's interruption monitor catches a banner that blocks an interaction. Android has no
such monitor. The orchestrator's per-step sweep runs after a step's gesture, not before it, and at most once per
poll interval. A banner that appears after the previous step's sweep therefore still takes this step's tap.

After the driver resolves the target, the adb driver checks the heads-up frame from the same read.
It does so in `_device_act`'s resolve loop on the resident path and in `_resolve_frame_and_screen`
on the coordinate path. When that frame contains the point the gesture is about to touch,
the driver clears the banner first. It swipes by Unit 4's points and polls until the banner is gone,
under the same bounded condition wait the orchestrator uses. It then re-resolves the target, because
the screen may have moved under the banner. The check costs no extra read, because the frame arrived with the tree.

A heads-up notification still sliding in has no settled frame yet, and
`notification_banner_swipe_points` returns `None` for it. The driver then reads again within the
same bounded condition wait and swipes once the frame settles. The driver never proceeds with a
gesture while a banner covers its point, unlike the orchestrator's sweep, which logs a warning and
moves on. The sweep protects a screenshot, where a leftover banner costs one image. Here a leftover
banner takes the tap and opens the notification's app. When the wait runs out with the banner still
covering the point, settled or not, the driver raises `ElementNotTappable` and names the heads-up
notification as the cause. The step then fails with that reason in the report, instead of failing
later on a screen it never meant to reach.

The orchestrator's sweep handles a banner that covers nothing the gesture touches. Clearing every
banner before every gesture would spend a swipe and a condition wait on banners that block nothing.

Each dismissal must reach the report. A new narrow protocol in `bajutsu/common/drivers/base/` carries
one method, `drain_banner_dismissals()`, which returns the notification text of each banner the
driver cleared since the last call. The orchestrator drains it where it already drains interruptions
and records each entry as an `AlertEvent` of kind `notificationBanner`, the record iOS produces. The
adb driver does not implement `InterruptionPolicyTarget`, because that protocol also carries a
button policy that only XCUITest applies.

The orchestrator's own sweep records its dismissals too. Today `_clear_notification_banner` leaves
only a swipe in the actuation log, so a banner the sweep clears leaves no `notificationBanner`
record. The function now returns an `AlertEvent` of kind `notificationBanner` when its swipe is
confirmed, with an empty label, since the sweep never reads the notification's text. Each caller
appends that record where it already keeps alerts:

- the per-step sweep, to the step's `outcome.alerts`;
- the sweep before a step-level `visual` capture, to the same step's `outcome.alerts`; and
- the sweep before an `expect`-phase `visual` capture, to `RunResult.expect_alerts`.

This change reaches iOS as well. An iOS run whose banner the sweep clears gains the same record that
the interruption monitor already produces. Every dismissal, on either platform and by either path,
then reaches the report as the same kind of record.

### Unit 6 — showcase fixture and on-device verification

Add a "Post notification" button to both Android showcase apps, the Views app and the Compose app.
The button posts a local notification on a channel of high importance, which Android presents as a
heads-up notification. Both apps already hold `POST_NOTIFICATIONS`.

Add `demos/showcase/scenarios/notification_banner_android.yaml`. The scenario taps the button, then
taps a control that sits inside the heads-up frame that Unit 1 measured. The scenario asserts that
the second tap reached its control.

The tap alone cannot prove that a banner appeared. A run in which the notification never showed
would pass on the tap and exercise nothing. No scenario assertion reads the run's `AlertEvent`s, so
the proof runs after the scenario instead. A new script,
`scripts/assert_notification_banner_evidence.py`, reads the finished run's `manifest.json` and fails
unless the scenario carries a dismissal of kind `notificationBanner`.
`scripts/assert_app_crash_evidence.py` already checks crash evidence the same way. Unit 5 makes the
check deterministic: both clearing paths record the dismissal, so a banner that appeared leaves the
record whichever path cleared it.

The Android emulator lane runs the scenario and then the script. The lane is the one
[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) added. The
off-device gate cannot prove that a swipe dismisses a real heads-up notification.

### Unit 7 — tests

Cover the host side in the fast Python suite, with dump fixtures recorded in Unit 1:

- `narrowed_root` extracts the heads-up frame and still drops every SystemUI window;
- the shade, expanded with the same rows, yields no frame;
- the fallback path returns `None` and warns once per lease;
- `notification_banner_swipe_points` scales both distances by `scale`;
- the adb driver clears a covering banner before a tap, leaves a banner that covers nothing, and
  re-resolves the target after the swipe;
- a banner caught mid-slide is swiped once its frame settles, and a banner that never settles or
  never clears within the wait fails the gesture with `ElementNotTappable` naming the banner;
- each driver dismissal drains into an `AlertEvent` of kind `notificationBanner`;
- each of the orchestrator's three sweep sites records an `AlertEvent` of kind `notificationBanner`
  for a confirmed swipe, and none for an unconfirmed one; and
- `assert_notification_banner_evidence.py` passes on a manifest that carries the record and fails on
  one that does not.

Replace `test_notification_banner_frame_is_none` in `tests/test_adb.py` with these tests.

### Unit 8 — docs

Flip the `handleNotificationBanner` mark for Android in `docs/platform-parity.md` and in the
capability table of `docs/drivers.md`, with both `docs/ja/` mirrors.
Replace the parity table's note, which claims that a heads-up notification appears in the tree, and
drop "iOS only" from the `docs/drivers.md` row.
Update the proactive banner path in `docs/architecture.md` and its Japanese mirror. Drop the claim
that only XCUITest declares `HANDLE_NOTIFICATION_BANNER`, state that the sweep records a
`notificationBanner` `AlertEvent`, and describe the adb driver's clearing before a gesture.
`tests/test_platform_parity.py` fails until both tables match the new declaration. Describe the
Android behavior, including the fallback limitation, where `docs/scenarios.md` and its Japanese mirror
describe the banner guard.

### Prime directives preserved

- **AI never judges.** Recognition compares resource ids that Unit 1 measured, and dismissal is a
  swipe. This item adds no model call.
- **Determinism first.** The driver confirms each dismissal by a bounded poll of the banner's absence.
  No step waits out the notification's own timeout.
- **App-agnostic.** The extraction keys on SystemUI's own identifiers, the same for every app. The
  item adds no per-app code or configuration.

## Alternatives considered

| Alternative | Why we rejected it |
|---|---|
| A dedicated resident endpoint that reads the heads-up window through `UiAutomation.getWindows()` | It mirrors iOS's `/notificationBanner/query`, but it adds Kotlin code, server tests, and one round trip per check. The read the driver already takes carries the same window. The fallback path would stay blind either way. |
| Detect the banner through `dumpsys` (the `HeadsUpManager` state) and assume a fixed region at the top of the screen | It works on both read paths, but the frame becomes a guess instead of a measurement, against prime directive 2. |
| Drop `handleNotificationBanner` from the capabilities while the driver reads through the fallback path | The declaration would match reality, but it would change in the middle of a lease. The static parity table and its test assume a fixed declaration. |
| Let each driver compute its own swipe points | Each platform could tune its distances, but the `Driver` interface would grow and the iOS driver would change. Scaling one function by density reuses distances that BE-0416 already measured. |
| Rely on the orchestrator's per-step sweep alone | The change is smallest, but the sweep runs after each step's gesture. A banner that appears after the previous step's sweep still takes this step's tap. |
| Clear any banner before every gesture, covering or not | The rule is simpler, but every gesture pays a swipe and a condition wait for banners that block nothing. |
| Implement `InterruptionPolicyTarget` on the adb driver to report dismissals | No new type is needed, but `set_interruption_policy` would become an empty method. The protocol assumes that only XCUITest needs it, and the orchestrator branches on it. |
| Record the dismissal as a plain swipe in the actuation log | The change is smallest, but the report would show an unexplained swipe. A reader could not tell that a banner had appeared. The orchestrator's sweep records exactly that today, which Unit 5 changes. |
| Prove the banner's appearance with an app-side signal and assert on the tap alone | BE-0416's iOS scenario does this, and no host change is needed. The signal proves the app posted a notification, not that SystemUI showed it or that Bajutsu cleared it. |
| Add a scenario assertion kind that reads the run's `AlertEvent`s | The proof would live in the scenario, but the scenario schema would grow for a fact that only this test harness needs. A scenario cannot observe a banner, so no scenario author needs the assertion. |
| Disable heads-up notifications on the test device (`settings put global heads_up_notifications_enabled 0`) | Simpler, but it hides a behavior real users see instead of handling it. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — measure the heads-up tree on API 33–35
- [ ] Unit 2 — extract the frame from the resident dump; warn on the fallback path
- [ ] Unit 3 — declare `handleNotificationBanner` on the adb driver
- [ ] Unit 4 — scale the swipe distances by display density
- [ ] Unit 5 — clear a covering banner before a gesture, and record every dismissal, the orchestrator sweep's included
- [ ] Unit 6 — showcase button on both Android apps, the banner scenario, and the manifest check
- [ ] Unit 7 — unit tests with heads-up dump fixtures
- [ ] Unit 8 — docs (en + ja)

## References

- [Issue #2396](https://github.com/bajutsu-e2e/bajutsu/issues/2396) — the parity gap this item closes.
- [BE-0416](../BE-0416-ios-notification-banner-swipe-dismiss/BE-0416-ios-notification-banner-swipe-dismiss.md)
  — the iOS banner guard, its measured swipe distances, and the `notificationBanner` report record.
- [`docs/platform-parity.md`](../../docs/platform-parity.md) — the parity table this item updates.
- [`BajutsuAndroidUIAutomatorServer/README.md`](../../BajutsuAndroidUIAutomatorServer/README.md) —
  why the resident dump carries SystemUI windows and why the host drops them.
