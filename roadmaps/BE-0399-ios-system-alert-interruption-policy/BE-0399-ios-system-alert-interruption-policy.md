**English** · [日本語](BE-0399-ios-system-alert-interruption-policy-ja.md)

# BE-0399 — Answer an interrupting system alert only by the scenario's own policy

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0399](BE-0399-ios-system-alert-interruption-policy.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0399") |
| Implementing PR | [#1808](https://github.com/bajutsu-e2e/bajutsu/pull/1808) |
| Topic | Platform support |
| Related | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md), [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's reactive system-alert guard clears an operating-system prompt that the application's own accessibility tree cannot see. It taps one of the prompt's buttons under a policy that the [scenario](../../docs/glossary.md#scenario-authoring) sets.

On iOS, two such prompts can be on screen at once: the "Save Password" alert iOS raises after a sign-in, and the notification authorization request. We measured on iOS 18.6, 26.3, 26.4, and 26.5 Simulators. When both prompts appear together, the guard stops being deterministic, and the cause is not in Bajutsu's own policy at all. XCUITest resolves an alert that interrupts one of its interactions *before* it synthesizes that interaction. With nothing installed, it answers using the alert's own default button.

This proposal takes that decision back. The runner installs an interruption monitor that presses the button the scenario's own `systemAlertHandling` names. The orchestrator resolves the labels and pushes them to the runner, so the policy stays in one place and the runner applies it. The runner also reports what it pressed, so such a dismissal reaches the run's report instead of happening in silence.

Alongside the monitor, the mid-wait gate stops issuing its in-tree dismissal tap on any poll that has not just confirmed no system alert is showing. That fixes the order in which the two prompts are answered: the system-owned alert first, by the scenario's policy, and only then the application-owned save-password alert.

## Motivation

The two prompts do not live on the same surface, and one of them alone is a system alert. We measured an application that signs in through its in-app browser. iOS raises the save-password alert into the *application's own* process. The notification request is a SpringBoard alert in another process:

```
springboard buttons = ["Don't Allow", "Allow"]
app.alerts          = "Save Password" / "Never for This Website" / "Not Now"
snapshot button id='' label='Not Now' frame=(61.7, 517.0, 270.0, 44.0)
```

That split decides which code path handles each prompt. The native path of [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md) reads `springboard.alerts` through `system_alert_labels()`, so the save-password alert reads as `"absent"` to it forever. The one path that clears the alert is `_dismiss_from_tree` in `bajutsu/orchestrator/waits.py`. It matches an identifier-less labeled button in the poll's own tree and taps it through the ordinary `Driver.tap`.

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

The answer that the built-in handler gives is the opposite of the policy the guard exists to apply. `DEFAULT_DISMISSIVE_LABELS` in `bajutsu/orchestrator/types.py` lists "Don't Allow" first, so a permission request the scenario never spoke about is refused, not granted. [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md) lets a scenario name `choice: deny` for the notification request.

A scenario that says `deny` still ends with the permission granted, because XCUITest answered first. No step fails, no warning is printed, and no `AlertEvent` reaches the report. Nothing in the run's output records that the permission was granted at all.

The grant is intermittent, not constant, which makes the failure read as flakiness. `_observe_native` probes SpringBoard at most once per `guard.poll_interval` (one second by default) but called `_dismiss_from_tree` on every `_POLL` (0.05 seconds). Nineteen of every twenty polls therefore issued an application tap without knowing whether a system alert was up.

Declining the interruption is not an available answer, and that finding shaped the design. A monitor that claims the alert without pressing anything does suppress the built-in handler. XCUITest then verifies that the interruption cleared, finds the alert still up, and re-invokes the monitor on the next interaction.

The resident runner serves queries continuously, and the interruption check fires on application-level queries, not on taps alone. The re-invocation is therefore an unbounded loop. It took the runner down mid-run in every measured attempt, against a control run that survived with the monitor removed. The monitor has to answer, and the only question is with which button.

A later reader can tell whether the change arrived by running a scenario that answers the notification request with `deny` while iOS's save-password alert is on screen, then asserting the application's own mirrored authorization status. Before the change, the status settles on `authorized`. After it, the status settles on `denied`. The showcase scenario this proposal adds makes exactly that assertion, and it was measured green on four iOS versions.

## Detailed design

### Unit 1 — Answer an interrupting alert by the scenario's own policy

The runner installs one user-interface interruption monitor. The monitor reads the alert's button labels and picks one by `InterruptionPolicy`: a rule whose identifying labels are each present exactly once, else the first candidate present exactly once. It presses the picked button, records the label, and returns `true`.

The matching discipline is a small port of `match_alert_rule` and `pick_alert_label`. The orchestrator resolves every label in it from the scenario's `systemAlertHandling` and pushes the labels over a new `POST /interruptionPolicy`. The decision therefore has one home, and the runner applies it.

The orchestrator pushes the policy once per scenario, not once per lease, because the resident runner outlives a scenario. A scenario that disables the guard pushes an *empty* policy instead of skipping the call, so it never inherits the previous scenario's answer.

A `POST /interruptionPolicy/drain` hands back the labels pressed since the last drain. The run loop folds them into the step's `AlertEvent`s beside its actuations. The report is where a dismissal answered this way stops being silent.

Declining stays the fallback for an alert on which the policy names no button. The monitor returns `false`, and XCUITest's own handler takes the alert, exactly as before this monitor existed. That is the one outcome that cannot loop, because that handler does clear the alert.

### Unit 2 — Run the in-tree dismissal only on a poll that just confirmed no alert

`_observe_native` gains one local flag. The flag records whether the native probe ran on this poll *and* returned `"absent"`. `_dismiss_from_tree` is called when the flag is set and not otherwise.

The in-tree dismissal therefore fires at the `poll_interval` cadence instead of the `_POLL` cadence. Every one of its taps is preceded, in the same poll, by a fresh SpringBoard query that found nothing. That makes the order deterministic when both prompts are up.

### Unit 3 — Re-derive the not-tappable give-up bound in seconds

`_TREE_DISMISS_MAX_DECLINES` bounded the consecutive `ElementNotTappable` declines that `_dismiss_from_tree` tolerates. Its value of 20 was derived as "~1s at `_POLL`". Unit 2 changes the cadence the bound is counted in.

`poll_interval` is configurable per scenario, target, and flag ([BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)), so no replacement count maps to a fixed duration. The bound becomes clock-based, like the neighbouring `_TREE_RETAP_DELAY`, which already made that choice for the same reason.

### Unit 4 — Guard the `gone` wait

A `gone` wait went unguarded, on the reasoning that a blocking prompt collapses the tree and a collapsed tree already satisfies "gone". That reasoning holds for a SpringBoard prompt and for no other.

A prompt drawn inside the application's own process collapses nothing. It *adds* its buttons to the tree. A `gone` wait on one of those buttons sits unsatisfied for its whole timeout with nothing to clear it. We measured this on-device, where the save-password alert survived a full sixty-second wait.

The branch now feeds the gate as `for` does, after its own condition check, so a wait already satisfied never actuates.

### Unit 5 — Report an application-owned alert once in the browser-merged tree

While `SFSafariViewController` is up, `queryElements` concatenates the application's walk with the browser service's walk ([BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)). An alert that the application raises *over* the browser is drawn by the application and mirrored into the service's tree as well. Each of its buttons was therefore reported twice, and the identifier-prefix prune cannot reach it, because the alert is not browser chrome.

A duplicate like that is one control seen twice, not an ambiguity, but the tree has to say so. Left in, the pair fails `resolve_unique`, the in-tree dismissal correctly declines to guess, and the alert is never cleared. The merge now drops a service-tree node whose whole reported identity, frame included, matches one that the application already reported.

### Unit 6 — Raise the real prompt in the showcase

The showcase's in-app browser loads a sign-in page served beside the existing browser fixture. Submitting the page makes iOS raise its real "Save Password" alert. The browser route needs no associated domain, no entitlement, and no Hypertext Transfer Protocol Secure (HTTPS) server: iOS offers to save a credential typed into a web form for the page's own origin, exactly as Safari does.

We measured the application's own login screen, the other route from which iOS raises this alert. It needs all three. That route is left to a follow-up and is not made a standing prerequisite of this lane.

The application arms the notification request on a delay once the browser opens (`SHOWCASE_NOTIF_AFTER_BROWSER`). A scenario cannot tap its way into the stacked state, because the monitor answers first an element tap made while a system alert is showing.

The scenario then waits on outcomes, not on an order, because the order moves between versions. We measured the following:

- On iOS 26.4 and 26.5, the notification request arrives while the sign-in is still being typed.
- On iOS 18.6 and 26.3, the notification request arrives after the sign-in.
- On iOS 26.4, the save alert itself arrives after the page has navigated.

### Unit 7 — Cover the orchestrator's half deterministically

The fake actuator drives tests for each of these:

- The policy the orchestrator pushes: the guard's own rules and candidates, the dismissive default when a scenario names none, an empty policy when it disables the guard, and nothing at all on a backend without the opt-in.
- The drain becoming `AlertEvent`s.
- The in-tree dismissal's gating in all three of its cases.
- The clock-based give-up.
- The `gone` wait now clearing an application-owned prompt.

The runner's own half is Swift and lands outside that gate, so Unit 6's scenario verifies it.

## Alternatives considered

* **Let the monitor decline and leave the alert to the guard's native path.** This was the first implementation, and it is wrong. XCUITest verifies that a claimed interruption cleared, and re-invokes the monitor on every following interaction while the alert is still up. We measured that loop running until the runner dies, against a control run that survived once the monitor was removed. The monitor must answer.
* **Change the Python gate alone and leave XCUITest's default handler in place.** Rejected, because the gate covers one caller. Every other element tap a scenario performs would still let XCUITest grant the permission silently. An ordinary `tap` step while a permission request happens to be up is one example, and we measured on iOS 26.4 and 26.5 that the request does interrupt an ordinary typing step.
* **Resolve the button in Swift from the alert's text.** Rejected, because it would split the button policy across two languages. The ordered `instruction` candidates, BE-0382's per-prompt `rules`, and BE-0320's locale-keyed label table all live in Python. Pushing resolved labels keeps one home for the decision and leaves the runner a mechanism.
* **Gate the in-tree dismissal on the last known native state instead of a fresh probe.** Rejected, because a cached "absent" can be a full `poll_interval` old, and that interval is exactly the window the defect lives in.
* **Probe SpringBoard on every `_POLL` and leave the in-tree cadence alone.** Rejected on the measurement BE-0315 already recorded: a per-poll SpringBoard query roughly doubles the load on the runner's single main thread.
* **Reach the save-password alert through the native `springboard.alerts` query.** Rejected on the measurement: the alert is raised into the application's process and never appears there.
* **Commit a fixed test certificate authority for the native route instead of generating one.** It would make that lane reproducible with fewer moving parts. Rejected, because a private key in the repository is a secret in the history whatever its purpose, and `make lint-secrets` would rightly object. Generating one per run into gitignored `tmp/` and deleting it afterwards costs a few seconds of `openssl` and leaves nothing behind.
* **Give the native route its own showcase application instead of a launch-env screen.** Rejected as disproportionate. `SHOWCASE_SIGNIN` swaps the whole interface the way `SHOWCASE_GESTURES` already does, so no other scenario's tab bar moves and no second product needs building.

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
