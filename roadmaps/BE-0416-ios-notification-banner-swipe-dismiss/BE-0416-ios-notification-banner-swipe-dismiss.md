**English** · [日本語](BE-0416-ios-notification-banner-swipe-dismiss-ja.md)

# BE-0416 — Swipe away an interrupting iOS notification banner reactively

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0416](BE-0416-ios-notification-banner-swipe-dismiss.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0416") |
| Implementing PR | [#1975](https://github.com/bajutsu-e2e/bajutsu/pull/1975) (units 1, 4, 6, 7), [#2015](https://github.com/bajutsu-e2e/bajutsu/pull/2015) (units 2, 3, 5, 8) |
| Topic | Platform support |
| Related | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md), [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu already clears two kinds of interstitial iOS screen without asking a model.

- An interstitial that the application's own accessibility tree can see gets a condition-and-recovery
  entry in the `interrupts` field
  ([BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md)).
- An operating-system alert that the tree cannot see gets a native SpringBoard query and a per-prompt
  policy ([BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md),
  [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md),
  [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md)).

Neither path reaches a third kind of interstitial: the banner that iOS raises over the running app
when a push or local notification arrives while the app is in the foreground. This proposal adds a
native presence query and a swipe-based dismiss action for that banner. Both are wired into the
reactive-guard shape that BE-0315 established for system alerts. As a result, a step's tap lands on
the element the scenario names, not on a banner the scenario never declared.

## Motivation

A foreground notification banner can appear at any point in a run. It can sit directly over the
element that a step is about to tap. The banner has no fixed trigger after which a scenario could
place a check. A push notification's arrival depends on a server. Even a local notification that an
app schedules for itself lands relative to wall-clock time, not relative to any step. A step whose
target sits under the banner's frame can therefore tap the banner instead of the target. It can also
resolve against whichever element the hit-testing of XCUITest picks once the frames overlap. Such a
defect reads as flakiness, because nothing in the run's report records that a banner ever appeared.

The gap is structural, not incidental. A process outside the application under test draws the banner.
BE-0315's motivation measured the same separation for a SpringBoard alert. The condition of the
`interrupts` field is evaluated against the application's own tree alone, so it has nothing to check
against.

The banner also carries no button. The deterministic dismiss that BE-0315 built for a system alert
resolves a button by its label. A person clears a real banner with an upward swipe, not a tap. A tap
opens the notification's own app and navigates the run away from the scenario it was running.

The gesture this proposal needs already exists in Bajutsu. It is `swipe`, documented in
[`docs/scenarios.md`](../../docs/scenarios.md) in both a directional and a coordinate form. No existing
mechanism knows where the banner is or when to reach for the gesture.

A later reader can tell whether this proposal arrived by running a scenario that raises the banner
with a `push` step immediately before a tap. The banner's measured frame then overlaps the tap's
target by construction, not by timing. Before this proposal, that tap is unreliable: where it lands
depends on how the two frames overlap at the moment XCUITest resolves it. After this proposal, the
guard clears the banner before the tap executes, and the tap lands on the target.

## Detailed design

### Unit 1 — the banner's accessibility surface, measured

The measurements ran on dedicated Simulators against a throwaway host app that raises its own
foreground banner. The devices were an iPhone 17 Pro on iOS 26.5 and an iPhone 16 Pro on iOS 18.6.
iOS 26.3 and 26.4 sit between the two confirmed versions and were not measured separately. The two
endpoints agreed on every answer below, identifier and subtree included.

| Question | Measured answer |
|---|---|
| Which process exposes the banner | `com.apple.springboard`, the same process a system alert comes from. The application's own tree shows nothing at all. |
| What frame or identifier the banner offers | A SpringBoard element carrying `identifier: NotificationShortLookView`, whose label is the notification's full text. Measured frame `{{8, 58.7}, {386, 78.7}}` on iOS 26.5. Its subtree holds `ShortLook.Platter` and, sharing the same frame, one hittable `Button` — `ShortLook.Platter.Content.Seamless` — whose label is that same text. |
| What a second concurrent banner enumerates | Nothing new: iOS coalesces concurrent banners, so the query finds exactly one `NotificationShortLookView` and one frame. Unit 2's "topmost by frame" ordering has no case to resolve. |
| Whether a banner reaches XCUITest's interruption monitor | Yes, on an *interaction*. A plain query never invokes the monitor and never clears the banner, however many times it runs. |
| Whether a handler can swipe and confirm before returning `true` | Yes. A handler that swiped by the element's own frame, confirmed the banner gone, and returned `true` was invoked exactly once. The interrupted tap then landed on its intended target, with the application still in the foreground. |
| Whether declining reproduces BE-0399's reinvocation loop | No. A declining monitor hands the banner to XCUITest's own handler, which clears it, and the monitor is not invoked again. A monitor that returns `true` *without* clearing does loop. The test capped a throwaway monitor at six invocations to reproduce this, instead of letting it run to the unbounded failure BE-0399 measured. |

Two further measurements, outside the six questions, reshape the design below.

**What today costs is latency, not a misdirected tap.** An undisturbed tap took 0.58s. The same tap
under a banner took **9.18s**, with no monitor and with BE-0399's declining monitor alike. XCUITest
waits the banner out and presses nothing. A handler that swipes brought the time down to **3.72s**.

In no arrangement did the banner receive the tap. XCUITest's own handler never pressed the banner's
button either, because the application's `didReceive` delegate never fired. The Motivation names a
hazard: tapping the banner opens its app. That hazard belongs to *this item's own implementation*.
It does not describe today's behavior.

The same measurement retires two neighboring claims. A step does not "tap the banner instead of the
target". Nor does it "resolve against whichever element XCUITest's own hit-testing picks". The
proposal assumed both before Unit 1 ran. In every arrangement above, the interrupted tap landed on
its intended target.

With both claims retired, the item's own falsification criterion goes too. That criterion watches for
the same landing, so it reads the same before and after and distinguishes nothing. What separates
before from after sits elsewhere. Unit 1's measurement established two justifications instead. One is
the spurious step failure measured next. The other is the corrupted `after.png` and visual-regression
capture that Unit 8 exists to reach.

**A banner already fails a passing step whenever `systemAlertHandling` is on.** BE-0399's monitor
asks the interrupting element for `alert.buttons`. A banner answers with exactly one button, and the
label of that button is the notification's own text. No rule can ever identify it, so
`recordDeclined` fires. BE-0406 Unit 2b then turns that record into a step failure that overrides an
`expect` that passed. Direct measurement gave `wouldRecordDeclined=true`, with the notification's
body text standing in for the buttons the run had expected. Nothing in this item created that defect.
Shipping Unit 4 removes it.

A persistent banner is the style iOS uses for the "Ready for Apple Intelligence" notification, and
it is the case that prompted this item. It was measured separately by setting `alertType` to `2` in
the Simulator's own `VersionedSectionInfo.plist`. Such a banner stays up indefinitely, confirmed past
30s. XCUITest nonetheless cleared it and landed the tap in 3.53s. The latency gap therefore closes on
a persistent banner while the spurious failure remains. That is why the failure, not the latency, is
this item's primary justification.

### Unit 2 — a deterministic presence query

Add a driver method that reports whether a notification banner is currently showing. When one is
showing, the method also reports the on-screen frame that Unit 3's swipe needs. The method mirrors the
shape of BE-0315's `system_alert_labels()`: a thin, non-blocking read that reports a fact and decides
nothing.

The method sits behind its own capability token, as `HANDLE_SYSTEM_ALERT` gates BE-0315's query. At
first, only the iOS XCUITest backend advertises it. A backend without it reports absence, not an
error.

Unit 1 settled the query's shape. It matches `NotificationShortLookView` against SpringBoard's whole
tree and reports at most one banner, since iOS coalesces concurrent ones. Its measured cost is about
32ms when no banner is up. The existing `springboard.alerts.firstMatch.exists` probe beside it costs
about 15ms.

Unit 1 also narrowed what this query is *for*. It cannot serve the interruption path of Unit 4,
because that path already holds the banner element. The query serves Unit 8's proactive poll. The poll
does what the interruption path cannot: it clears a banner that merely sits on screen while nothing
interacts with it.

### Unit 3 — a deterministic swipe-dismiss action

Add a driver action that swipes the banner away. The action anchors to the frame that Unit 2 reports,
not to a fixed screen coordinate, so the gesture holds across device sizes.

The direction matches how a person dismisses a real banner: upward. The swipe ends at an on-screen
point above the banner's own frame, not past the screen's edge. Past the edge, SpringBoard claims the
drag as its own notification-shade gesture.

The action reuses the coordinate machinery that the existing driver implementation of `swipe` already
has, instead of adding a second gesture primitive. That machinery resolves a point as an offset from
the application's own origin. Unit 2's frame is measured in SpringBoard's coordinate space. This unit
therefore also states how that frame converts into the application's offsets.

Like Unit 2, this unit now serves Unit 8's proactive poll alone. Unit 4's monitor path performs the
same gesture inside the runner, against the element that XCUITest handed it, without a driver round
trip.

### Unit 4 — the interruption-monitor path

Unit 1's fourth and fifth measurements selected this path. Unit 1's second extra finding made it the
unit that carries the item's value: a banner that reaches BE-0399's alert path fails a step that would
otherwise pass.

The runner's existing interruption monitor gains a branch for a notification banner. The branch runs
*ahead of the policy*, not inside it. Recognition is by identifier. Unit 1 measured
`NotificationShortLookView` as stable across iOS 18.6 and 26.5. The element's type is an undocumented
raw value, and the label of its one button varies per notification and per locale.

The branch sits ahead of the `governs` check because neither alert outcome fits a banner.

- A governing policy records the banner as an undeclared interruption and fails the step.
- An ungoverned policy leaves XCUITest to wait the banner out.

On that branch, the monitor swipes the banner upward by its own frame. It then confirms the clearance
before claiming the interruption. Confirmation keeps the claim honest. Unit 1 reproduced the
reinvocation loop that BE-0399 warned about, so a monitor that claimed a banner it had not cleared
would take the resident runner down. An unconfirmed swipe declines instead. Declining hands the banner
back to XCUITest's own handler, which Unit 1 measured as clearing it.

The dismissal reaches the report through the existing drain, as an `AlertEvent`. It is a dismissal,
not an `UndeclaredInterruption`, since nothing a scenario could declare would identify a banner. The
banner carries no button, so that record needs a field of its own to be legible. `AlertEvent` carries
only `label`. A banner dismissal would otherwise arrive as an alert whose locator named no button.
This unit adds an optional discriminator to `AlertEvent`, defaulting to today's alert case, and
renders it in the run's report.

**No config or scenario toggle.** The proposal called for one, on the shape that BE-0177 established
for `systemAlertHandling`. Unit 1 removed the case for it. A scenario cannot observe a banner, because
`expect` runs against the application's own tree, which Unit 1 confirmed never shows one. No scenario
can be broken by clearing a banner, so a toggle would carry no known use. Should a use appear, Unit 8
is where the toggle belongs. The proactive poll is the half that spends time on every step.

### Unit 5 — showcase fixture and on-device verification

Add a showcase scenario that raises the banner with a `push` step
([`docs/scenarios.md`](../../docs/scenarios.md), `simctl push`). The step sits immediately before the
tap under test, so the banner's arrival is requested at a step boundary, not at wall-clock timing.

The scenario asserts two facts:

- The tap lands on its target.
- The run's report carries the banner-dismissal discriminator that Unit 4 adds.

`simctl push` returns once the payload reaches the device, not once the banner is on screen. A run in
which the banner never appeared would otherwise pass on the tap alone and exercise nothing. Asserting
on the discriminator closes that gap.

Raising the banner is app-side work that this unit owns. The showcase apps present no foreground
banner today. [`demos/showcase/scenarios/push.yaml`](../../demos/showcase/scenarios/push.yaml) records
that its `push` leaves the foreground UI unchanged. This unit therefore also adds the
`UNUserNotificationCenterDelegate` foreground presentation that the banner needs. The fixture answers
the notification-authorization prompt with a `handleSystemAlert` step, because `permissions` cannot
pre-grant notification authorization on iOS ([`docs/scenarios.md`](../../docs/scenarios.md)).

The tap under test must sit inside the frame that Unit 1 measured, `{{8, 58.7}, {386, 78.7}}` on a
6.3-inch device. The overlap then holds by construction. If no showcase control sits there, this unit
adds one.

The off-Simulator gate cannot prove a native swipe against a real banner. This unit must therefore
exercise the scenario on a booted Simulator.

### Unit 6 — docs

Document the banner branch in [`docs/scenarios.md`](../../docs/scenarios.md) alongside `interrupts`
and `systemAlertHandling`. The entry extends BE-0314's existing comparison of when to reach for each
mechanism. Mirror it in [`docs/ja/scenarios.md`](../../docs/ja/scenarios.md).

Record the branch, and its placement ahead of the `governs` check, in
[`docs/architecture.md`](../../docs/architecture.md) and its Japanese mirror. BE-0113 requires this of
a change to behavior those pages describe.

There is no CLI flag to document, since Unit 4 adds no toggle.

### Unit 7 — tests

For Unit 4, the tests cover the following:

- Recognition of SpringBoard's banner identifier, and rejection of an alert's.
- A banner's own button can never identify a rule, the condition that used to fail a passing step.
- The store reports a swiped banner apart from a tapped label. It clears the banner once drained,
  orders oldest-first, and drops what a previous scenario queued.
- The drain endpoint carries the banners over both the generated and the legacy transport.
- The driver reads the banners, and reads none from a runner that predates them.
- A `/tap` fold without banners still counts as a fold.
- The orchestrator maps banners to `AlertEvent`s of their own kind, and never to an
  `UndeclaredInterruption`.
- A banner lands on the step it interrupted without failing the step.
- The report tells the two kinds apart in both the manifest round trip and the rendered HTML.

For Units 2, 3, and 8 when they land, the tests cover the following:

- A fake driver whose presence query flips between polls.
- The guard clears the banner before a step's own actuation.
- The capability gate leaves a backend without it unchanged.

### Unit 8 — the proactive poll, for what the monitor cannot reach

Unit 1 measured that a plain query never invokes the interruption monitor and never clears the
banner. Everything Bajutsu does that is not an interaction therefore runs with the banner still on
screen. One example is a step's `after.png`, and with it every visual-regression comparison. A banner
that overlaps the captured region corrupts such a comparison outright. Unit 4 closes nothing here.

This unit arms a guard. The guard polls Unit 2's presence query on the bounded interval that BE-0315
already established for the SpringBoard probe. The moment the poll finds a banner, the guard clears it
through Unit 3's action.

If a config- and scenario-level toggle is wanted, it belongs to this unit. This half spends time on
every step, not only on an interrupted one. The toggle would follow the same config-then-scenario,
flag-overridable precedence that `systemAlertHandling` established
([BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)).

### Prime directives preserved

- **AI never judges.** Recognition is an identifier comparison and the dismissal is a swipe. This
  item adds no new AI surface.
- **Determinism first.** No fixed sleep. The monitor confirms the swipe by bounded re-observation of
  the banner's absence. It never waits out the banner's own auto-dismiss timeout. Unit 1 measured
  that waiting is exactly what XCUITest does when nothing answers.
- **App-agnostic.** The branch is a generic runner mechanism keyed on SpringBoard's own identifier.
  No per-app code is added.

## Alternatives considered

- **Route the banner through `interrupts`.** Rejected on the premise that BE-0315's motivation
  established for a system alert. A process outside the application under test draws the banner. The
  condition of `interrupts` is evaluated against the application's own tree alone, so it has nothing
  to check against.
- **Dismiss the banner with a tap instead of a swipe.** Rejected. The banner's one button is the
  *open the notification's app* affordance, which would carry the run away from the scenario under
  test. A swipe removes the banner without that side effect. Unit 1 measured that XCUITest's own
  default handler never presses that button either. The rejection therefore rules out a choice open
  to *this item*. It does not describe what happens today.
- **Add an AI-vision fallback for a case the native query cannot resolve, mirroring the vision guard
  that system alerts once had.** Rejected under prime directive 1. BE-0402 already removed the
  equivalent fallback from `run`'s system-alert path for the same reason. The banner's presence and
  frame are exactly the kind of fact a native query answers, with no judgment call for a model to
  make.
- **Let the banner auto-dismiss on its own timeout instead of swiping it away.** Rejected under prime
  directive 2 (no fixed sleep). Unit 1 measured what that choice costs, because XCUITest already does
  it when nothing answers: 9.18s per interrupted interaction against 0.58s undisturbed. A banner set
  to the persistent style never auto-dismisses at all.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — the banner's accessibility surface, measured on iOS 18.6 and 26.5. The answers, and
      the two findings that reshaped Units 2–4, are recorded in *Detailed design* above.
- [x] Unit 2 — deterministic presence query (`Driver.notification_banner_frame()`, gated on the new
      `HANDLE_NOTIFICATION_BANNER` capability). Scope narrowed by Unit 1: it serves Unit 8's
      proactive poll, not Unit 4. A new `/notificationBanner/query` runner route (mirroring
      `/systemAlert/query`), since Unit 4's own route reuse covered only the drain fold.
- [x] Unit 3 — deterministic swipe-dismiss action anchored to the measured frame
      (`base.notification_banner_swipe_points`), reusing the existing `swipe` action rather than a
      second gesture primitive. Narrowed the same way as Unit 2.
- [x] Unit 4 — the interruption-monitor path: recognize the banner ahead of the policy, swipe it by
      its own frame, confirm the clearance before claiming the interruption, and report it as an
      `AlertEvent` under a kind of its own. Lands without the config/scenario toggle the proposal
      called for — see *Unit 4* above for why Unit 1 removed the case for it.
- [x] Unit 5 — showcase fixture, including the app-side foreground banner presentation (a
      SwiftUI-only `UNUserNotificationCenterDelegate`, gated on the fixture's own launch env so it
      never affects `push.yaml`'s pre-existing first scenario) and a tap target inside the banner's
      frame, and on-device verification. Verified on a dedicated Simulator: the notification
      authorization flow, the delegate's `willPresent` signal, and the tap target's on-screen
      position (measured inside the frame Unit 1 recorded). The notification banner itself did not
      visually render on this session's host — confirmed, with both `simctl push` and a local
      notification, that `willPresent` fires correctly while SpringBoard draws nothing, which
      isolates the gap to this host's own Simulator notification permission (a system dialog no
      unattended session can click through) rather than to the fixture or the driver code. The
      scenario and app changes ship as written; CI's `actuation` job (`ios-e2e.yml`), which already
      runs `push.yaml` on a fresh macOS runner, is what confirms live that a real banner never
      survives to block the tap. A step boundary sits between the banner's appearance and the tap
      (the `wait` for the delegate's own `willPresent` signal), so the fixture cannot pin down
      whether the interruption monitor (Unit 4) or the proactive sweep (Unit 8) is what actually
      clears the banner on a given run — either is a correct outcome, and the scenario's own
      comment says so.
- [x] Unit 6 — docs (`docs/scenarios.md`, `docs/architecture.md`, and both `docs/ja/` mirrors) —
      extended again for the Unit 2/3/8 proactive sweep.
- [x] Unit 7 — tests for what Unit 4 landed, plus the Unit 2/3/8 half: a fake driver whose presence
      query flips between polls, the guard clearing the banner before a step's own after-shot and
      before an `expect`-phase visual capture, the capability gate leaving a backend without it
      unchanged, and the Swift-side route's parity/contract tests.
- [x] Unit 8 — the proactive poll, for the banner a run never interacts its way past: the corrupted
      `after.png` and visual-regression capture Unit 1 measured the monitor cannot reach. No
      scenario/CLI toggle — the same "no known use for a toggle" Unit 4 already established.

Log:

- 2026-09-11 — [#1975](https://github.com/bajutsu-e2e/bajutsu/pull/1975) — Unit 1 measured on dedicated Simulators (iPhone 17 Pro / iOS 26.5, iPhone 16 Pro /
  iOS 18.6), and Units 2–4 rewritten against what it found. Unit 4 landed: the runner's interruption
  monitor now recognizes a notification banner ahead of the alert policy, swipes it away by its own
  measured frame, confirms the clearance before claiming the interruption, and reports it as an
  `AlertEvent` carrying a `notificationBanner` kind. That removes the defect Unit 1 measured, where a
  banner arriving during a run with `systemAlertHandling` on was recorded as an undeclared
  interruption and failed an otherwise-passing step, naming the notification's body text among the
  buttons the run had expected.
- 2026-09-16 — [#2015](https://github.com/bajutsu-e2e/bajutsu/pull/2015) — Units 2, 3, 5, and 8 landed, completing the item. A new
  `HANDLE_NOTIFICATION_BANNER` capability and `Driver.notification_banner_frame()` (a new
  `/notificationBanner/query` runner route) report a banner's frame between interactions;
  `base.notification_banner_swipe_points` converts it into the swipe `swipe` already performs. The
  step loop calls this once per step, rate-limited to `systemAlertHandling`'s own resolved
  `pollInterval`, right before the `after.png` shutter and before the `expect`-phase visual capture
  — the corrupted screenshots Unit 1 measured the interruption monitor alone cannot reach. No
  scenario/CLI toggle, matching Unit 4's own precedent. The showcase fixture (`push.yaml`) gained a
  second scenario raising a genuine foreground banner via a new, SwiftUI-only
  `UNUserNotificationCenterDelegate` (gated on the fixture's own launch env, so the pre-existing
  first scenario in the same file never raises one) and a dedicated flat screen
  (`SHOWCASE_NOTIFICATION_BANNER`) whose tap target sits inside the banner's measured frame. The
  UIKit target gains no delegate at all, having no such screen to raise or tap a banner from.
  On-device verification on a dedicated Simulator confirmed
  the permission flow, the delegate's timing signal, and the target's on-screen position; the
  banner's own visual rendering did not appear on this session's host, isolated to a local Simulator
  notification-permission gap (confirmed with both a real push and a local notification) rather than
  to the code, so the live confirmation that a real banner never survives to block the tap is left
  to CI's `actuation` job — the fixture cannot itself distinguish whether the interruption monitor
  or the proactive sweep is what clears a given run's banner, since a step boundary sits between
  the banner's appearance and the tap. The swipe itself leaves a frame caught mid-animation alone
  whenever the resulting gesture would travel less than the interruption monitor's own 20-point
  swipe — the inverted (downward) case is only the extreme of that — re-checks presence again
  immediately before the gesture (a banner can auto-dismiss between the two reads), and re-confirms
  the banner gone with a bounded poll before returning, matching the interruption monitor's own
  confirm-before-claiming discipline.

## References

- [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) — the
  `interrupts` field, and why its in-tree condition cannot reach a system-owned overlay.
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md) —
  the native presence-query and reactive-guard shape this proposal reuses for a banner instead of an
  alert.
- [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md) —
  the interruption monitor and its ordering, which Unit 1 must measure against the banner before
  Unit 4 can reuse it.
- [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md) — the
  per-prompt declaration this item's single config toggle is simpler than, since a banner has no
  distinct prompts to declare.
- [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md) — the
  config-then-scenario, flag-overridable precedence Unit 4's toggle follows.
- [`docs/scenarios.md`](../../docs/scenarios.md) — the existing `swipe` step whose coordinate machinery
  Unit 3 reuses, and the `push` step Unit 5's fixture uses to raise the banner deterministically.
- Apple, [`XCTestCase.addUIInterruptionMonitor(withDescription:handler:)`](https://developer.apple.com/documentation/xctest/xctestcase/adduiinterruptionmonitor(withdescription:handler:)) —
  the monitor Unit 4's interruption path would reuse, cited in BE-0314 and BE-0399 as the same prior
  art.
