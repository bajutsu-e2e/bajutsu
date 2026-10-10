**English** · [日本語](BE-XXXX-runner-resident-alert-watcher-ja.md)

# BE-XXXX — Move system-alert detection into a runner-resident watcher

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-runner-resident-alert-watcher.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's reactive system-alert guard clears prompts that block a scenario, such as the notification
permission request or iOS's "Save Password" sheet. Today the Python orchestrator watches for those
prompts. During every wait it queries SpringBoard (the iOS system shell) on an interval, samples the
accessibility tree every 50 ms, and runs a state machine of latches and notes around both. This
proposal moves the watching into the XCUITest runner, the resident Swift process that already holds
the Simulator connection. A watcher thread in the runner detects an alert, presses the button the
scenario's own policy names, and records what it did. The orchestrator then reads those records
instead of watching: its alert gate shrinks from a detector into a reader, and the XCUITest-only
detection code leaves the Python side.

## Motivation

The guard's detection logic has grown into the largest single piece of the wait loop.
`_AlertGuardGate` (`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`) is 801 lines. It
interleaves three detectors that answer overlapping questions. The first is the native SpringBoard
probe, rate-limited to `poll_interval` (one second by default). The second is the in-tree dismissal
for alerts the application itself raises, which runs on every 50 ms poll but only on a poll whose
probe just reported no SpringBoard alert (BE-0399). The third is the collapsed-tree proxy, which samples every poll and explains a blocked screen after the fact. Each detector keeps its own latches
and notes, and each was tuned against a race the others create. A change to one, such as BE-0418's
retry, has to be reasoned about against all three.

The structure follows from where detection runs. XCUITest has no callback for "an alert appeared".
Its only hook is the interruption monitor that BE-0399 installed in the runner, and that monitor
fires when XCUITest evaluates an interaction against the application. Between interactions the
runner cannot see an alert. An alert the application raises in its own process never interrupts an
interaction at all, which is why BE-0399 dropped those rules from the monitor's policy. The
orchestrator therefore had to poll from outside, across a Hypertext Transfer Protocol (HTTP) round trip, and the runner's single
main thread forced the polling to stay sparse (BE-0315). The same process boundary also splits one
decision across two languages: the runner presses a SpringBoard button by pushed policy, while
Python taps an application-owned button by the same policy resolved a second time.

Moving the watcher into the runner removes that split. The runner owns the clock, the serial operation
queue, and both kinds of alert surface, so one sweep can answer every kind of prompt under one policy.
The orchestrator stops polling for alerts and reads the runner's records at points it already reads
them. No scenario changes, and no new language model call enters a run (prime directive 1).

A later reader can tell the change arrived by three observations. First, a wait under a governing
guard, outside a `handleSystemAlert` step, sends neither `/systemAlert/query` nor `/interruptionPolicy/drain`, which the runner's request log shows. Second, the gate
file no longer contains the native probe or the in-tree dismissal (the native probe alone in the narrowed
form). Third, the showcase scenario that
answers the notification request and the "Save Password" sheet together stays green on the four iOS
versions BE-0399 measured (18.6, 26.3, 26.4, and 26.5).

## Detailed design

The work moves in order: measure the watcher's cost, build the SpringBoard watcher, build the orchestrator's reader, handle a step that meets an answered alert, port the application-owned alert, delete the old path, then verify and
document. If the application sweep fails Unit 1's gate, Unit 5 is dropped and the remaining units ship the SpringBoard half alone, with Unit 6 deleting
the native probe alone. Every unit
is XCUITest-specific behind existing seams, so the other backends keep today's behavior and prime
directive 3 holds.

### Unit 1 — Measure the sweep before building it

The cost of an in-runner sweep is unknown. A SpringBoard query (`springboard.alerts.firstMatch.exists`)
is cheap in isolation. An application-side sweep for an alert that is not in `app.alerts` needs a
snapshot of the application tree, which is far heavier. A throwaway runner build sweeps both at the
default one-second interval while the showcase suite runs on the four iOS versions above. It
measures step duration against the baseline and counts runner crashes. The gate for Units 2 to 8 is
no step-duration change beyond run-to-run noise and zero crashes. If SpringBoard passes and the
application sweep fails, Unit 5 is dropped and this item narrows to the SpringBoard half; if the
SpringBoard sweep fails, the item is Rejected. In that narrowed form the thin gate keeps calling
`_dismiss_from_tree` for application-owned alerts, so the "Save Password" sheet stays handled. Unit 3 states the licence for that call. The spike also records whether the monitor already
answers an alert during the wait's own `/elements` traffic, because the answer decides whether a sweep is needed while `/elements` traffic is in flight. It adds no product code.

### Unit 2 — A runner-resident watcher for SpringBoard alerts

The watcher is a new type in `BajutsuKit/Sources/BajutsuRunner/`, next to `InterruptionPolicy.swift`.
It starts when `POST /interruptionPolicy` arrives with `governs` true and stops when a later push
says otherwise, so a scenario that disables the guard never inherits a running watcher.

- **Interval.** The push carries `pollIntervalSeconds`, the value of `systemAlertHandling.pollInterval`
  (default one second, `AlertGuardConfig.poll_interval`). The scenario key keeps its meaning and
  gains no sibling.
- **Serialization.** Each sweep runs on `APIHandler`'s serial `operations` queue, so it never overlaps
  another operation. The handler sets an in-flight flag around each operation, and a sweep that
  finds the flag set skips to the next interval. The design holds on the planned Hummingbird
  listener too, since that listener keeps the same queue.
- **Answer.** On an alert the watcher reads the button labels, picks one with
  `InterruptionPolicy.label(for:)` (every identifying label present exactly once), and presses it.
  Python resolves the labels and pushes them. Swift keeps the matching discipline in
  `InterruptionPolicy.label(for:)`, which mirrors Python's `matching_alert_rule`, so the policy keeps
  one home.
- **Report.** Answered, declined, and swiped-away records enter the store that
  `POST /interruptionPolicy/drain` already returns. A new record kind, *unidentified*, covers an
  alert no rule names that the watcher leaves on screen. It stays apart from *declined*, which the
  monitor records after XCUITest's default handler has cleared the alert. The watcher records one
  unidentified alert once per showing, keyed on its button set and re-armed when a sweep no longer
  finds it. A prompt left up therefore adds one record, not one per interval. The orchestrator fails
  the step by name, as BE-0406 does for a decline.
- **Reservation.** A `handleSystemAlert` step taps its own alert, so the watcher must leave that one
  alert alone and keep answering every other alert, as today's gate does with `reserved=sel`
  (`waits/_functions.py`). The orchestrator pushes the step's selector before the step through a
  separate endpoint, `POST /alertWatcher/reservation`. It clears the reservation after the step with
  an empty body. The endpoint leaves the record store and the pushed policy untouched, because
  `POST /interruptionPolicy` clears pending records in `setPolicy`. A `POST /interruptionPolicy`
  push, such as the one `_reserve_declared_alert` makes at the start of a prompt-form step, leaves
  the reservation alone. The watcher skips an alert one of whose buttons the selector names. Swift
  ports the subset of `base.matches` that `selector_names_button` uses today: `label`,
  `labelMatches`, `value`, and `traits`. An `id` reserves nothing, as today. This carries the
  `reserved` argument of `probe_native` over to the watcher.

The port splits one matching rule across two languages, which BE-0399 avoided for the button policy.
The reservation accepts that cost because suspending the watcher would leave a step's other alerts
unanswered. Two guards hold the port to Python:

- a shared fixture of selectors and button lists that both test suites run;
- a check at scenario load that rejects a `labelMatches` pattern Python's `re` and Foundation's
  `NSRegularExpression` read differently. When the check cannot decide, it rejects.

The monitor from BE-0399 stays. It still answers an alert that interrupts an interaction between two
sweeps, under the same policy, and the watcher and the monitor share one record store.

### Unit 3 — The orchestrator reads records instead of probing

A new capability token, `ALERT_WATCHER`, marks a backend that runs Unit 2's watcher. When the driver
advertises it, `bajutsu/common/orchestrator/waits/` builds a thin gate beside `_AlertGuardGate`
instead of the full one. The runner folds the pending records into each `/elements` reply, draining them
atomically with the snapshot, following the precedent of `/tap`, whose reply already carries the
drained labels (BE-0407 Unit 6). The XCUITest driver adds the folded records to the carry that already holds `/tap`'s drained
labels (`_drain_carry`). `drain_interruptions()` keeps today's rule: it returns the carry alone
when the carry is current, and merges it with `POST /interruptionPolicy/drain` otherwise. `POST /interruptionPolicy/drain` remains for
the one-shot points (end of step, end of scenario) and Unit 4's failure-time drain. A
`POST /interruptionPolicy` push also drains the pending records and folds them into its reply, and
the driver adds them to `_drain_carry` like an `/elements` fold. Today `_reserve_declared_alert`
drains and then pushes in two requests, both before and after a prompt-form step. A watcher sweep
that lands between them would record an answer, or an unidentified alert, that `setPolicy` then
wipes unread. Draining inside the push closes that window.

On each poll the thin gate reads the records folded into that poll's `/elements` reply, so reading
costs no extra round trip. It reads them without consuming them and emits no report events. It uses
them for two things. One is the blocked-screen note, built from unidentified and declined records.
The other is Unit 4's step-scoped list of answered alerts. It never calls `probe_native`, never taps, and keeps no latch about the screen. The one exception is the narrowed form from Unit 1, where it keeps
calling `_dismiss_from_tree`. There the runner folds one more fact into the `/elements` reply:
whether its last sweep found a SpringBoard alert or a reserved alert. The thin gate calls
`_dismiss_from_tree` only when that bit is clear, and at most once per `poll_interval`. This
replaces today's `probed_absent` licence.

Each record has one owner. The end-of-step drain (`_drain_step_interruptions` in
`bajutsu/common/orchestrator/loop/_step_runner.py`) stays the one place that turns records into the
step's `AlertEvent`s. It also fails the step on an unidentified or declined record, as it does today.
Without this rule, each watcher answer would appear twice in the report, once from the gate and once
from the drain.

Under the capability, the step runner does not call `AlertGuardConfig.__call__` at the end of a
step or scenario. Unit 4's failure-time drain and once-per-step retry replace the drain and the
whole-step retry that `__call__` performs today, and its `cleared` return no longer gates a retry.
The end-of-step drain stays the sole reporter and fails the step on unidentified or declined
records. In the narrowed form, `dismiss_from_tree_once` keeps running at the end of a step under the
same SpringBoard-present bit. The remaining one-shot reads use the drain endpoint. A backend without the
capability (adb, Playwright, or the fake driver when a test leaves the capability off) keeps
`_AlertGuardGate` unchanged until Unit 6 removes its XCUITest-only branches.

### Unit 4 — A step that meets an answered alert

A request that arrives while a sweep runs on `operations` waits behind it. The sweep covers one
press, which BE-0399's activity log shows at about 1.6 seconds. The orchestrator's socket
windows are 15 seconds for a read and 30 seconds for a write (`bajutsu/common/drivers/xcuitest/_functions.py`).
The watcher presses one button per sweep, so a waiting request never absorbs more than one press.
Unit 1 records the longest wait it observes.

A step can still meet an alert in two ways, and each has its own answer.

- **The alert appears while the step's actuation is in flight.** XCUITest hands the interruption to
  the monitor and then synthesizes the original event, so the actuation completes once. No retry is
  needed.
- **The alert covers the target before the actuation can land.** The step's selector finds nothing,
  or the element is not tappable, and the step fails today. The thin gate keeps the answered
  alerts it read during the current step in a step-scoped list in the step's own context. On such a
  failure the orchestrator drains once more, to pick up an answer recorded after the last poll. The
  records it drains are folded into the step's outcome, the same way `_reserve_declared_alert`'s
  pre-push drain does today, so nothing it picks up is lost from the report. It then checks the
  step-scoped list together with the answered records this drain returned. In the narrowed form, the
  step-scoped list also holds the `AlertEvent`s that the thin gate's `_dismiss_from_tree` produced
  during the step. The failure path also runs `dismiss_from_tree_once`, under the
  SpringBoard-present bit, before this check and adds its event. A step whose target the "Save
  Password" sheet covered therefore keeps the retry that `__call__` gives it today. If either holds an answered event, it resolves the selector again and
  issues the actuation once more. Runner records stay timestamp-free, so the step boundary, not a
  clock, defines the window.


The retry never follows a write the runner may have carried out. BE-0207 forbids re-issuing a write
whose outcome is unknown after delivery, because a second delivery could double-actuate, and this
item keeps that rule. A definite `not-found` or `not-hittable` refusal is not such a write, since
the runner refused it without acting. The retry runs once per step
and only with an answered record in hand, so an unrelated failure is never masked. The report lists
both the answered alert and the retry. Under the capability this retry is the step's one retry: the
end-of-step retry that `AlertGuardConfig.__call__` gates today does not run, so a step cannot retry
twice and no second path can re-issue a write whose outcome is unknown.

### Unit 5 — Application-owned alerts in the watcher

The sweep also looks for an application-owned alert, such as the "Save Password" sheet. Python's
in-tree rules (`AlertGuardConfig.tree_rules`) are pushed in the same `POST /interruptionPolicy`
body; today `push_interruption_policy` drops them before the push. The wire format gains an optional `exclude` list per rule, and Python pushes
the rules in `AlertGuardConfig.tree_dedup_rules` order, so a nested shape's wider sibling comes
first. The watcher matches a rule when every identifying label is on the application snapshot's
buttons exactly once and no excluded label is present, then taps the named button.
It taps an application-owned alert only on a sweep that found no SpringBoard alert, the reserved one
included. That keeps the order today's `probed_absent` licence enforces: an XCUITest tap made while
a SpringBoard alert is up would let the monitor answer that alert first.
`push_interruption_policy`'s refusal of exclusion sets is lifted for in-tree rules alone.

The pacing that `_dismiss_from_tree` carries today moves with the tap. Its numbers do not move: the
re-tap delay, the per-showing tap ceiling, and the not-tappable give-up bound move from
`waits/_alert_guard_gate.py` and `waits/_functions.py` into `alert_guard_config.py`, and are pushed
from there so they keep one home. The give-up bound is still derived from `poll_interval` on the
Python side. In the narrowed form the constants stay where `_dismiss_from_tree` uses them. The watcher reports a give-up as a *gave up* record
naming the rule's identifying labels. The orchestrator composes the `uncleared_prompt_note` text from
that record. The withdrawal applies to the end-of-step drain's result, as `_withdraw_tree_event` does
today: the end-of-step drain drops the answered event that a later *gave up* record for the same
showing contradicts. Wording stays in Python.

A duplicate pair in the browser-merged tree (BE-0396) already collapses in `queryElements`, so the
watcher sees one button and `resolve_unique` semantics hold. An ambiguous match still declines and
reports, and never taps whichever matched first (prime directive 2).

### Unit 6 — Delete the old XCUITest detection path

After Units 2 to 5 pass on device, the Python side loses everything the watcher replaced:
`probe_native`, `_observe_native`'s probe and licence logic, `_dismiss_from_tree` with its latches,
and the XCUITest-specific branches of the collapsed-tree proxy. No flag restores them, because a
second switch would be a second vocabulary for one behavior. The explicit `handleSystemAlert` step
keeps `/systemAlert/query` and `/systemAlert/tap` (BE-0316). The collapsed-tree proxy stays for
backends without the capability. In the narrowed form Unit 6 deletes the native probe alone and keeps
`_dismiss_from_tree`, which waits for the SpringBoard-present bit that Unit 3 describes.

### Unit 7 — Verification

- **Swift.** `FakeElementProvider` in `BajutsuKit/Tests/BajutsuRunnerTests/` drives the watcher's
  interval, skip-when-busy, and give-up bound. It also drives reservation matching against the
  shared fixture, exclusion and widest-first matching, exactly-once matching, the once-per-showing
  unidentified record, and the records folded into the `/elements` reply, the SpringBoard-present bit in that reply, and
  the records folded into the `/interruptionPolicy` reply. None of these tests
  needs a Simulator.
- **Python.** The fake actuator implements the capability. Tests cover seven behaviors:
  - the single-owner rule: the thin gate reads the folded records without reporting them, and the
    end-of-step drain reports each record once;
  - the failure by name on an unidentified record;
  - the reservation push and clear around `handleSystemAlert`, and the load-time `labelMatches`
    check;
  - the once-per-step retry, fed by the step-scoped event list, after a pre-delivery failure and
    never after a write whose outcome is unknown;
  - the composition of the `uncleared_prompt_note` text from a *gave up* record, and the withdrawal
    of its `AlertEvent` in the end-of-step drain's result;
  - in the narrowed form, the in-tree tap waits for a clear SpringBoard-present bit, and an in-tree
    dismissal arms the once-per-step retry;
  - under the capability, the end-of-step `AlertGuardConfig.__call__` retry does not run.
- **On device.** `demos/showcase/scenarios/permission.yaml` and BE-0399's two-prompt scenario run
  green on the four iOS versions with Unit 6's deletion applied.

### Unit 8 — Documentation

`docs/architecture.md`, `DESIGN.md`, and their `docs/ja/` mirrors describe the guard as a Python
detector. They are updated in the same change that deletes the detector (BE-0113).

## Alternatives considered

| Option | What it does | Why it was not adopted |
|---|---|---|
| Runner raises a flag, Python decides | The runner detects and sets a flag on every response. Python taps. | The tap round trip and the gate's state machine stay. The complexity this item targets would remain. |
| Server push to the orchestrator | The runner streams alert events over Server-Sent Events or a WebSocket. | The runner answers the alert itself, so the orchestrator needs the record only at points it already reads. A second channel adds a transport for no new information. |
| Rely on the interruption monitor alone | Drop the watcher and let the monitor answer. | The monitor runs only when XCUITest evaluates an interaction. An application-owned alert never interrupts one (BE-0406), so it would go unanswered. |
| Keep the Python path behind a flag | Ship the watcher with a switch back to polling. | Two implementations of one behavior would stay in maintenance, and the flag would be a second vocabulary for it. |
| Resolve the button in Swift from the alert's text | The runner decides the button without a pushed policy. | BE-0399 rejected this: the candidates, per-prompt rules, and locale table live in Python. |
| Suspend the watcher around `handleSystemAlert` | The step pauses the watcher through a suspend and resume pair of endpoints. | Nothing would answer the step's other alerts. The thin gate never taps, the monitor never fires on the step's own `/systemAlert/query` polls, and an application-owned alert never reaches the monitor, so a "Save Password" sheet could hold the screen for the step's whole timeout. |
| SpringBoard alerts only | The watcher covers `springboard.alerts` and Python keeps the in-tree path. | It stays available as the fallback if Unit 1's gate fails for the application sweep, but it leaves the largest detector in the gate. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — measure the in-runner sweep on four iOS versions (gate)
- [ ] Unit 2 — runner-resident watcher for SpringBoard alerts
- [ ] Unit 3 — orchestrator reads records through a thin gate and the `ALERT_WATCHER` capability
- [ ] Unit 4 — a step that meets an answered alert: bounded wait behind a sweep and a pre-delivery retry
- [ ] Unit 5 — application-owned alerts in the watcher
- [ ] Unit 6 — delete the old XCUITest detection path
- [ ] Unit 7 — Swift, Python, and on-device verification
- [ ] Unit 8 — documentation in both languages

## References

- [BE-0315 — Native system-alert handling](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  — the native SpringBoard probe and the single-main-thread load argument.
- [BE-0399 — Answer an interrupting alert by the scenario's policy](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md)
  — the monitor, the pushed policy, and the drain this item reuses.
- [BE-0406 — Declare system alerts by prompt alone](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md)
  — why application-owned rules never reach the monitor.
- [BE-0316 — Explicit mid-flow step for permission-prompt alerts](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)
  — the `handleSystemAlert` step that keeps the explicit routes.
- [`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift`](../../BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift)
  — the pushed policy and matching discipline.
- [`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`](../../bajutsu/common/orchestrator/waits/_alert_guard_gate.py)
  — the gate this item thins.
