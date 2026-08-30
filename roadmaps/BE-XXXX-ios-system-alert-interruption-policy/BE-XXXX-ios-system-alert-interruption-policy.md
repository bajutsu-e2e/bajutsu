**English** · [日本語](BE-XXXX-ios-system-alert-interruption-policy-ja.md)

# BE-XXXX — Answer an interrupting system alert only by the scenario's own policy

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ios-system-alert-interruption-policy.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1808](https://github.com/bajutsu-e2e/bajutsu/pull/1808) |
| Topic | Platform support |
| Related | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md), [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's reactive system-alert guard clears an operating-system prompt that the application's own
accessibility tree cannot see, tapping one of the prompt's buttons under a policy the
[scenario](../../docs/glossary.md#scenario-authoring) sets. On iOS, two such prompts can be on
screen at once: the "Save Password" alert iOS raises after a sign-in, and the notification
authorization request. Measured on iOS 18.6, 26.3, 26.4, and 26.5 Simulators, the guard stops being
deterministic when both appear together, and the reason is not in Bajutsu's own policy at all.
XCUITest resolves an alert that interrupts one of its interactions *before* it synthesizes that
interaction, and with nothing installed it answers using the alert's own default button.

This proposal takes that decision back. The runner installs an interruption monitor that presses the
button the scenario's own `systemAlertHandling` names — the labels are resolved in the orchestrator
and pushed to the runner, so the policy stays in one place and the runner only applies it — and
reports what it pressed, so such a dismissal reaches the run's report instead of happening in
silence. Alongside it, the mid-wait gate stops issuing its in-tree dismissal tap on any poll that has
not just confirmed no system alert is showing, which fixes the order the two prompts are answered in:
the system-owned alert first, by the scenario's policy, and only then the application-owned
save-password alert.

## Motivation

The two prompts do not live on the same surface, and only one of them is a system alert. Measured
against an application signing in through its in-app browser, the save-password alert is raised by
iOS into the *application's own* process and the notification request is a SpringBoard alert in
another process:

```
springboard buttons = ["Don't Allow", "Allow"]
app.alerts          = "Save Password" / "Never for This Website" / "Not Now"
snapshot button id='' label='Not Now' frame=(61.7, 517.0, 270.0, 44.0)
```

That split decides which code path handles each prompt.
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)'s
native path reads `springboard.alerts` through `system_alert_labels()`, so the save-password alert
reads as `"absent"` to it forever, and the one path that clears it is `_dismiss_from_tree` in
`bajutsu/orchestrator/waits.py`, which matches an identifier-less labeled button in the poll's own
tree and taps it through the ordinary `Driver.tap`.

That tap is where XCUITest intervenes. The runner's activity log records the whole sequence:

```
t = 8.11s Tap "Not Now" Button
t = 8.14s     Check for interrupting elements affecting "Not Now" Button
t = 8.17s     Found 1 interrupting element:
t = 8.19s     Invoking UI interruption monitors for "…Would Like to Send You Notifications" Alert
t = 8.29s Default interruption handler attempting to dismiss alert by tapping "Allow" Button.
t = 9.92s Confirmed successful handling of interrupting element
t = 9.92s     Synthesize event
```

The answer that built-in handler gives is the opposite of the policy the guard exists to apply.
`DEFAULT_DISMISSIVE_LABELS` in `bajutsu/orchestrator/types.py` lists "Don't Allow" first, precisely
so a permission request the scenario never spoke about is refused rather than granted, and
[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md) lets a
scenario name `choice: deny` for the notification request. A scenario that says `deny` still ends
with the permission granted, because XCUITest answered first. No step fails, no warning is printed,
and no `AlertEvent` reaches the report, so nothing in the run's output records that the permission
was granted at all.

The grant is intermittent rather than constant, which is what makes the failure read as flakiness.
`_observe_native` probes SpringBoard at most once per `guard.poll_interval` (one second by default)
but called `_dismiss_from_tree` on every `_POLL` (0.05 seconds). Nineteen of every twenty polls
therefore issued an application tap without knowing whether a system alert was up.

Declining the interruption is not an available answer, which is the finding that shaped the design.
A monitor that claims the alert without pressing anything does suppress the built-in handler, but
XCUITest verifies the interruption cleared, finds the alert still up, and re-invokes the monitor on
the next interaction. In a resident runner that serves queries continuously — the interruption check
fires on application-level queries, not only on taps — that is an unbounded loop, and it took the
runner down mid-run in every measured attempt, against a control run that survived with the monitor
removed. So the monitor has to actually answer, and the only question is with which button.

A later reader can tell whether the change arrived by running a scenario that answers the
notification request with `deny` while iOS's save-password alert is on screen, then asserting the
application's own mirrored authorization status. Before the change the status settles on
`authorized`; after it, on `denied`. The showcase scenario this proposal adds makes exactly that
assertion, and it was measured green on four iOS versions.

## Detailed design

### Unit 1 — Answer an interrupting alert by the scenario's own policy

The runner installs one user-interface interruption monitor. It reads the alert's button labels,
picks one by `InterruptionPolicy` — a rule whose identifying labels are each present exactly once,
else the first candidate present exactly once — presses it, records the label, and returns `true`.
The matching discipline is a small port of `match_alert_rule` and `pick_alert_label`; every label in
it is resolved in the orchestrator from the scenario's `systemAlertHandling` and pushed over a new
`POST /interruptionPolicy`, so the decision has one home and the runner only applies it.

The policy is pushed once per scenario rather than per lease, because the resident runner outlives a
scenario: a scenario that disables the guard pushes an *empty* policy rather than skipping the call,
so it never inherits the previous scenario's answer. A `POST /interruptionPolicy/drain` hands back
the labels pressed since the last drain, which the run loop folds into the step's `AlertEvent`s
beside its actuations — the report is where a dismissal answered this way stops being silent.

Declining stays the fallback for an alert the policy names no button on: the monitor returns `false`
and XCUITest's own handler takes it, exactly as before this monitor existed. That is the one
outcome that cannot loop, because that handler does clear the alert.

### Unit 2 — Run the in-tree dismissal only on a poll that just confirmed no alert

`_observe_native` gains one local flag recording whether the native probe ran on this poll *and*
returned `"absent"`, and `_dismiss_from_tree` is called only when the flag is set. The in-tree
dismissal therefore fires at the `poll_interval` cadence instead of the `_POLL` cadence, and every
one of its taps is preceded, in the same poll, by a fresh SpringBoard query that found nothing. That
is what makes the order deterministic when both prompts are up.

### Unit 3 — Re-derive the not-tappable give-up bound in seconds

`_TREE_DISMISS_MAX_DECLINES` bounded the consecutive `ElementNotTappable` declines
`_dismiss_from_tree` tolerates, and its value of 20 was derived as "~1s at `_POLL`". Unit 2 changes
the cadence it is counted in, and `poll_interval` is configurable per scenario, target, and flag
([BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)), so no
replacement count maps to a fixed duration. The bound becomes clock-based, like the neighbouring
`_TREE_RETAP_DELAY` which already made that choice for the same reason.

### Unit 4 — Guard the `gone` wait

A `gone` wait went unguarded, on the reasoning that a blocking prompt collapses the tree and a
collapsed tree already satisfies "gone". That holds for a SpringBoard prompt and only for those. A
prompt drawn inside the application's own process collapses nothing and instead *adds* its buttons to
the tree, so a `gone` wait on one of them sits unsatisfied for its whole timeout with nothing to
clear it — measured on-device, where the save-password alert survived a full sixty-second wait. The
branch now feeds the gate like `for` does, after its own condition check so a wait already satisfied
never actuates.

### Unit 5 — Report an application-owned alert once in the browser-merged tree

While `SFSafariViewController` is up, `queryElements` concatenates the application's walk with the
browser service's
([BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)). An
alert the application raises *over* the browser is drawn by the application and mirrored into the
service's tree as well, so each of its buttons was reported twice, and the identifier-prefix prune
cannot reach it because the alert is not browser chrome. A duplicate like that is one control seen
twice rather than an ambiguity, but the tree has to say so: left in, the pair fails `resolve_unique`,
the in-tree dismissal correctly declines to guess, and the alert is never cleared. The merge now
drops a service-tree node whose whole reported identity, frame included, matches one the application
already reported.

### Unit 6 — Raise the real prompt in the showcase

The showcase's in-app browser loads a sign-in page served beside the existing browser fixture, and
submitting it makes iOS raise its real "Save Password" alert. The browser route needs no associated
domain, entitlement, or Hypertext Transfer Protocol Secure (HTTPS) server: iOS offers to save a
credential typed into a web form for the page's own origin, exactly as Safari does. Measured, the
application's own login screen — the other route iOS raises this alert from — does need all three,
so it is left to a follow-up rather than made a standing prerequisite of this lane.

The notification request is armed by the application on a delay once the browser opens
(`SHOWCASE_NOTIF_AFTER_BROWSER`), because a scenario cannot tap its way into the stacked state: an
element tap made while a system alert is showing is answered by the monitor first. The scenario then
waits on outcomes rather than on an order, because the order moves between versions — measured, the
notification request arrives while the sign-in is still being typed on iOS 26.4 and 26.5 but after it
on 18.6 and 26.3, and on 26.4 the save alert itself arrives after the page has navigated.

### Unit 7 — Cover the orchestrator's half deterministically

The fake actuator drives tests for the policy the orchestrator pushes (the guard's own rules and
candidates, the dismissive default when a scenario names none, an empty policy when it disables the
guard, and nothing at all on a backend without the opt-in), for the drain becoming `AlertEvent`s, for
the in-tree dismissal's gating in all three of its cases, for the clock-based give-up, and for the
`gone` wait now clearing an application-owned prompt. The runner's own half is Swift and lands
outside that gate, so Unit 6's scenario is what verifies it.

## Alternatives considered

* **Let the monitor decline and leave the alert to the guard's native path.** The first
  implementation, and it is wrong: XCUITest verifies that a claimed interruption cleared, and
  re-invokes the monitor on every following interaction while the alert is still up. Measured, that
  loops until the runner dies, against a control run that survived once the monitor was removed. The
  monitor must answer.
* **Change only the Python gate and leave XCUITest's default handler in place.** Rejected because
  the gate covers one caller. Every other element tap a scenario performs — an ordinary `tap` step
  while a permission request happens to be up — would still let XCUITest grant the permission
  silently, and measured on iOS 26.4 and 26.5 the request does interrupt an ordinary typing step.
* **Resolve the button in Swift from the alert's text.** Rejected because it would split the button
  policy across two languages: the ordered `instruction` candidates, BE-0382's per-prompt `rules`,
  and BE-0320's locale-keyed label table all live in Python. Pushing resolved labels keeps one home
  for the decision and leaves the runner a mechanism.
* **Gate the in-tree dismissal on the last known native state rather than a fresh probe.** Rejected
  because a cached "absent" can be a full `poll_interval` old, and that interval is exactly the
  window the defect lives in.
* **Probe SpringBoard on every `_POLL` and leave the in-tree cadence alone.** Rejected on the
  measurement BE-0315 already recorded: a per-poll SpringBoard query roughly doubles the load on the
  runner's single main thread.
* **Reach the save-password alert through the native `springboard.alerts` query.** Rejected on the
  measurement: the alert is raised into the application's process and never appears there.
* **Raise the alert from the application's own login screen instead of the browser.** Deferred, not
  rejected — it is a genuinely different route and worth covering. Measured, it needs a
  `webcredentials:` associated domain, and declaring the entitlement is not enough: with no HTTPS
  server publishing `apple-app-site-association` and no certificate authority in the Simulator's
  keychain, no prompt appears at all. That apparatus is a standing prerequisite for a lane that today
  needs only a built application, so it belongs in its own change.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — answer an interrupting alert by the policy the orchestrator pushes, and report it
- [x] Unit 2 — gate `_dismiss_from_tree` on a native probe that ran this poll and returned `"absent"`
- [x] Unit 3 — re-derive the not-tappable give-up bound in seconds instead of polls
- [x] Unit 4 — guard the `gone` wait so an application-owned prompt can be cleared
- [x] Unit 5 — report an application-owned alert once in the browser-merged tree
- [x] Unit 6 — raise iOS's real save-password alert in the showcase and answer both prompts
- [x] Unit 7 — cover the orchestrator's half deterministically
- [ ] Follow-up — the native-login route, with the associated-domain apparatus it needs

## References

- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`, whose
  `_observe_native` sequences the native probe, the in-tree dismissal, and the collapsed-tree proxy.
- [`bajutsu/orchestrator/types.py`](../../bajutsu/orchestrator/types.py) — `AlertGuardConfig`,
  `push_interruption_policy`, `drain_interruptions`, and the `DEFAULT_DISMISSIVE_LABELS` policy.
- [`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift`](../../BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift)
  — the pushed policy and the matching discipline the monitor applies.
- [`BajutsuKit/Runner/Sources/RunnerUITest.swift`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift)
  — where the monitor is installed.
- [BE-0269 — Intervene early in a wait](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)
  — why the guard runs mid-wait rather than only after a step fails.
- [BE-0315 — Native system-alert handling](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  — the native path, its `poll_interval`, and the per-poll-query cost this proposal does not re-incur.
- [BE-0382 — Per-prompt rules](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md)
  — the per-prompt `choice` a silent grant overrides.
- [Apple — About the Password AutoFill workflow](https://developer.apple.com/documentation/security/about-the-password-autofill-workflow)
  — the associated-domain requirement behind the deferred native-login route.
