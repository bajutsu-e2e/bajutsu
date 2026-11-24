**English** · [日本語](BE-0496-android-heads-up-notification-dismiss-ja.md)

# BE-0496 — Report and swipe away an Android heads-up notification

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0496](BE-0496-android-heads-up-notification-dismiss.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0496") |
| Implementing PR | [#2498](https://github.com/bajutsu-e2e/bajutsu/pull/2498) |
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
The proposal also adds one check that iOS does not need: before a gesture on a resolved target or a literal point,
the adb driver clears a heads-up notification that covers the gesture's own point.

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
result in this item. Unit 2 keys on the result, so the measurement comes first. Record seven facts:

- which SystemUI window holds the heads-up notification;
- the resource ids that mark the notification row and its bounds;
- what tells the heads-up notification apart from the expanded notification shade, which draws the
  same rows; and
- whether an upward swipe from the row's center, travelling the distance Unit 4 computes, dismisses
  the notification;
- the resource id that carries the notification's title and text;
- the cost of one hierarchy read on the resident channel; and
- the time from the dismissing swipe until the banner is gone, including one read.

BE-0416's Unit 1 did the same for iOS, and its measurements shaped every later unit. A guessed
identifier would make Unit 2 match the wrong node or none.

#### Unit 1 result

We measured on Pixel emulators at API 33, 34, and 35, all at 1080 by 2400 pixels and 420 dpi. The
showcase app posted on a high-importance channel, and the resident server read every window. The
dumps are in `tests/fixtures/be0496/`.

| Fact | Measured |
|---|---|
| Window | The notification shade's own SystemUI window, cropped to the banner while a heads-up is pinned: `[42,0][1038,387]` on API 33, `[42,0][1038,371]` on API 34, and `[42,0][1038,302]` on API 35. |
| Row and its bounds | `com.android.systemui:id/expandableNotificationRow` inside `notification_stack_scroller`: `[42,157][1038,387]`, `[42,153][1038,371]`, and `[42,84][1038,302]`. Only the pinned row is visible to the dump. |
| Heads-up versus shade | The status bar's `com.android.systemui:id/heads_up_status_bar_view`, which shows the posting app's name, is present while a heads-up is pinned. The expanded shade draws the same rows in one full-screen window with no such view. |
| Upward swipe | A swipe from the row's center to 20 dp above its top edge dismissed the banner on all three levels. A 20-pixel swipe did not, and the banner stayed until its own timeout, about six seconds later. |
| Title and text | `android:id/title` and `android:id/text`, the platform's notification template. |
| Cost of one read | 41–45 ms at the median and 56–85 ms at the 95th percentile, on a still screen. A read during the banner's slide-in waits for the animation and takes up to 1.8 s. |
| Swipe until gone | At most 2.3 s from the start of the swipe until a read that confirmed the banner gone ended, that read included. |

Two findings shaped the later units beyond the seven facts:

- A heads-up swiped away snoozes heads-ups from the same app for a minute
  (`heads_up_snooze_length_ms`), and SystemUI does not alert again for an update to a notification
  already in the shade. Unit 6's button therefore clears the shade and posts under a fresh id.
- The read is far cheaper than the 400–600 ms this item assumed when it chose to answer from the
  current tree. `notification_banner_frame` therefore reads afresh on every call, as the Log below
  explains.

### Unit 2 — the frame, read from the resident dump

`narrowed_root` keeps dropping the SystemUI windows, so the app's elements stay unchanged. Before it
drops them, it extracts the frame of the heads-up row that Unit 1 identified and returns the frame
alongside the narrowed tree. The adb driver stores the frame with the tree it read.
The same extraction pulls the notification's title and text from the row's text node (the resource id
that Unit 1 measured). The driver therefore holds the frame and the text from one read.

`notification_banner_frame` answers from the driver's current tree while that tree is still current
(`_tree_current` in `adb_driver.py`, which any actuation invalidates). It takes a fresh read otherwise.
The orchestrator's guard still sees a banner leave, because its swipe is an actuation, so every poll
after the swipe is a fresh read. On the resident channel a read is a full round trip (400–600 ms).
Always reading fresh would add about half a second to every Android step. Answering from a current tree
avoids that cost when no gesture happened since the last read. The price of the choice is that a banner
that appears while nothing actuates stays unseen until the next read.

Unit 5's in-driver check gets the frame free with every read. The read already carries the SystemUI
windows, and the extraction walks only the windows that `narrowed_root` already visits. The unit needs
no new endpoint and no change to the resident server's Kotlin code.

The `uiautomator dump` fallback reads the active window alone, so it never sees the heads-up window.
On that path `notification_banner_frame` returns `None`, and the driver logs one warning per lease.
The warning says that the banner guard is off until the resident channel returns. The fallback already
warns that it runs degraded. The new warning names one more consequence. The driver also records the
fallback in the run's `manifest.json`, so that Unit 6's check can tell a run whose guard was off.
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
the driver clears the banner first. It swipes by Unit 4's points and polls until the banner is gone.
It then re-resolves the target, because the screen may have moved under the banner. The check costs no
extra read, because the frame arrived with the tree.

The check also covers the coordinate gestures, `swipe` and `drag` with literal points. They call
`driver.swipe` without resolving a selector (the coordinate form in `gestures.py`). The driver checks
the gesture's start point against the frame the same way. The swipe that the driver itself performs
to dismiss the banner bypasses this check, because it targets the banner deliberately.

The driver's clearance wait has its own named budget. It does not reuse the orchestrator's
`_BANNER_CLEARANCE_TIMEOUT` (2 s), which is short by design because a leftover banner there costs one
screenshot. Before a gesture, a leftover banner fails the step. On this backend one absence check is a
full hierarchy read (400–600 ms per round trip per `AdbDriver.query`), so 2 s buys only three or four
polls. Unit 1 measures the dismissal animation plus one read on the emulator lane, and the budget is
sized from that measurement with margin.

A heads-up notification still sliding in has no settled frame yet, and
`notification_banner_swipe_points` returns `None` for it. The driver then reads again within the
same budget and swipes once the frame settles. The driver never proceeds with a
gesture while a banner covers its point, unlike the orchestrator's sweep, which logs a warning and
moves on. The sweep protects a screenshot, where a leftover banner costs one image. Here a leftover
banner takes the tap and opens the notification's app. When the wait runs out with the banner still
covering the point, settled or not, the driver raises a new, distinct exception,
`NotificationBannerBlocking`, defined in `bajutsu/common/drivers/base/`. The step then fails with the
banner named in the report, instead of failing later on a screen it never meant to reach.

The type is not a subclass of `ElementNotTappable`, on purpose. `_tap_with_recovery`
(`bajutsu/common/orchestrator/actions/handlers/gestures.py`) catches `ElementNotTappable` and answers
with a bounded scroll down then up. A SystemUI heads-up window is anchored to the screen, so scrolling
cannot clear it and would only scroll the app under test. A banner that auto-dismisses mid-recovery
would also let the retried tap succeed on a screen the scenario never asked to scroll. A separate type
means the scroll recovery never sees the failure.

The orchestrator's sweep handles a banner that covers nothing the gesture touches. Clearing every
banner before every gesture would spend a swipe and a condition wait on banners that block nothing.
The adopted check covers every gesture whose point the banner covers, and no other.

Each dismissal must reach the report. A new narrow protocol in `bajutsu/common/drivers/base/` carries
one method, `drain_banner_dismissals()`, which returns the notification text of each banner the
driver cleared since the last call. The text is the one Unit 2 extracted, so a driver dismissal's
`AlertEvent` label is the notification's own text, matching the contract of `AlertEvent` in
`bajutsu/common/orchestrator/types/alert_event.py`. The orchestrator records each entry as an
`AlertEvent` of kind `notificationBanner`, the record iOS produces.

The orchestrator checks this protocol independently of `InterruptionPolicyTarget`. The
`drain_interruptions` function in `bajutsu/common/orchestrator/types/_functions.py` returns an empty
drain for any driver that is not an `InterruptionPolicyTarget`, so the new drain cannot live behind
that guard. The adb driver does not implement `InterruptionPolicyTarget`, because that protocol also
carries a button policy that only XCUITest applies. The orchestrator drains from `cfg.driver` (the
native driver), not `active_driver`, at the same per-step point. A dismissal during a `web`-block step
therefore does not strand in the native driver's store. `_step_runner.py` already applies the same
rule to the sweep's own actuation.

The orchestrator's own sweep records its dismissals too. Today `_clear_notification_banner` leaves
only a swipe in the actuation log, so a banner the sweep clears leaves no `notificationBanner`
record. The function now returns an `AlertEvent` of kind `notificationBanner` when its swipe is
confirmed, with an empty label, since the sweep never reads the notification's text. The two paths stay
distinguishable in the report by that label. Each caller
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
`scripts/assert_app_crash_evidence.py` already checks crash evidence the same way. While the resident channel is up, Unit 5 makes the
check deterministic: both clearing paths record the dismissal, so a banner that appeared leaves the
record whichever path cleared it.

The resident channel is a precondition of that check. When the driver reads through the
`uiautomator dump` fallback, the manifest carries the marker from Unit 2, and the script fails with
that cause named ("the resident channel was unavailable, so the banner guard was off"). It does not
report a missing dismissal. A run that could not verify the guard never passes.

The Android emulator lane runs the scenario and then the script. The lane is the one
[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) added. The
off-device gate cannot prove that a swipe dismisses a real heads-up notification.

### Unit 7 — tests

Cover the host side in the fast Python suite, with dump fixtures recorded in Unit 1:

- `narrowed_root` extracts the heads-up frame and still drops every SystemUI window;
- the shade, expanded with the same rows, yields no frame;
- the fallback path returns `None` and warns once per lease;
- the extraction yields the notification's text along with the frame;
- `notification_banner_swipe_points` scales both distances by `scale`;
- `notification_banner_frame` answers from the current tree with no read, and a fresh read follows any
  actuation;
- the adb driver clears a covering banner before a tap, leaves a banner that covers nothing, and
  re-resolves the target after the swipe;
- a coordinate swipe whose start point lies under the banner clears it first, and the dismissing
  swipe itself is not re-checked;
- a banner caught mid-slide is swiped once its frame settles, and a banner that never settles or
  never clears within the wait fails the gesture with `NotificationBannerBlocking` naming the banner,
  and `_tap_with_recovery` does not scroll on it;
- each driver dismissal drains into an `AlertEvent` of kind `notificationBanner` that carries the
  notification's text as its label;
- a dismissal on a driver that is not an `InterruptionPolicyTarget` still reaches the step's alerts,
  including during a `web`-block step;
- each of the orchestrator's three sweep sites records an `AlertEvent` of kind `notificationBanner`
  for a confirmed swipe, and none for an unconfirmed one; and
- `assert_notification_banner_evidence.py` passes on a manifest that carries the record and fails on
  one that does not, and fails with the resident-channel cause on a manifest that carries the
  fallback marker.

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
| Clear any banner before every gesture, covering or not | The rule is simpler, but every gesture pays a swipe and a condition wait for banners that block nothing. The adopted design covers every gesture whose point the banner covers, literal points included. |
| Implement `InterruptionPolicyTarget` on the adb driver to report dismissals | No new type is needed, but `set_interruption_policy` would become an empty method. The protocol assumes that only XCUITest needs it, and the orchestrator branches on it. |
| Record the dismissal as a plain swipe in the actuation log | The change is smallest, but the report would show an unexplained swipe. A reader could not tell that a banner had appeared. The orchestrator's sweep records exactly that today, which Unit 5 changes. |
| Prove the banner's appearance with an app-side signal and assert on the tap alone | BE-0416's iOS scenario does this, and no host change is needed. The signal proves the app posted a notification, not that SystemUI showed it or that Bajutsu cleared it. |
| Add a scenario assertion kind that reads the run's `AlertEvent`s | The proof would live in the scenario, but the scenario schema would grow for a fact that only this test harness needs. A scenario cannot observe a banner, so no scenario author needs the assertion. |
| Disable heads-up notifications on the test device (`settings put global heads_up_notifications_enabled 0`) | Simpler, but it hides a behavior real users see instead of handling it. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — measure the heads-up tree, read cost, and dismissal time on API 33–35
- [x] Unit 2 — extract the frame and text from the resident dump; warn and mark the manifest on the fallback path
- [x] Unit 3 — declare `handleNotificationBanner` on the adb driver
- [x] Unit 4 — scale the swipe distances by display density
- [x] Unit 5 — clear a covering banner before a gesture, coordinate gestures included, and record every dismissal, the orchestrator sweep's included
- [x] Unit 6 — showcase button on both Android apps, the banner scenario, and the manifest check
- [x] Unit 7 — unit tests with heads-up dump fixtures
- [x] Unit 8 — docs (en + ja)

Log:

- 2026-11-20 — Units 1–8 landed together ([#2498](https://github.com/bajutsu-e2e/bajutsu/pull/2498)). Unit 1's measurements are under *Unit 1 result*
  above, and the scenario passed with the driver clearing the banner on API 33, 34, and 35 emulators,
  for both showcase apps. Deviations from the Detailed design:
  - The fallback's manifest marker is a channel note, which the manifest already carries per
    scenario and per step. The driver adds it once per lease, and the evidence script looks for it.
    A new manifest field would have recorded the same fact twice.
  - The log line beside that note is a warning only when the resident channel was lost or failed to
    start. A lease that opted out of the server, or never built it, logs the line as information,
    since that run chose the dump on purpose.
  - `narrowed_root` returns the banner as a third element of its tuple, instead of a second value
    beside the tree.
  - The check before a gesture also covers `tapPoint` and the directional `swipe`'s scroll, since
    both touch a literal point the banner can cover.
  - The clearance wait is six seconds. A swipe that has not cleared the banner after 2.5 seconds is
    sent again. One gesture clears at most three banners before it fails.
  - The banner target sits in the title band above the scrolling page: the top app bar in Compose,
    the title row in Views. A content row there starts below the banner on API 35. The post button
    and the tap counter sit at the foot of the page, below the Photos section. Placed above it, they
    pushed the photo count out of view once the picker's return scrolled the page to its top, and
    `select_photos` timed out on CI.
  - The scenario grants `notifications`, since the Views target does not pre-grant it. Its
    `systemAlert` entry sets a 30-second `pollInterval`, so the per-step sweep does not clear the
    banner before the driver's own check meets it. A capped three-second `sleep` waits for the
    banner to slide in, because nothing in the app's tree shows it. Two seconds missed the banner
    once on an emulator that had just booted.
  - The evidence script also fails a run whose scenario failed, naming the tap that did not land.
  - `notification_banner_frame` takes a fresh read on every call, instead of answering from the
    current tree. The sweep polls it after its swipe and before it, with nothing actuating in
    between, so a cached answer kept a banner that had left: the confirmation timed out, and the
    re-check before the swipe could flick at the app. The check before a gesture still reuses the
    tree its resolve read, and both answer from the post-gesture tree a device-side gesture's reply
    carried while that tree waits for the next read.
  - A read that falls back to `uiautomator dump` while the driver waits for a banner to clear fails
    the gesture rather than counting as a clearance, and the sweep records no dismissal on a degraded
    channel. A pinned heads-up whose row the extraction cannot read logs a warning once.
  - `record`, `crawl`, the interactive shell, `serve`'s capture endpoint, and the enrichment replay
    handle `NotificationBannerBlocking` wherever they already handle `ElementNotTappable`.

## References

- [Issue #2396](https://github.com/bajutsu-e2e/bajutsu/issues/2396) — the parity gap this item closes.
- [BE-0416](../BE-0416-ios-notification-banner-swipe-dismiss/BE-0416-ios-notification-banner-swipe-dismiss.md)
  — the iOS banner guard, its measured swipe distances, and the `notificationBanner` report record.
- [`docs/platform-parity.md`](../../docs/platform-parity.md) — the parity table this item updates.
- [`BajutsuAndroidUIAutomatorServer/README.md`](../../BajutsuAndroidUIAutomatorServer/README.md) —
  why the resident dump carries SystemUI windows and why the host drops them.
