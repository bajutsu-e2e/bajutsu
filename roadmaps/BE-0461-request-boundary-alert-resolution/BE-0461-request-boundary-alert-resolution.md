**English** · [日本語](BE-0461-request-boundary-alert-resolution-ja.md)

# BE-0461 — Resolve system alerts at runner request boundaries instead of watching from Python

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0461](BE-0461-request-boundary-alert-resolution.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0461") |
| Implementing PR | [#2152](https://github.com/bajutsu-e2e/bajutsu/pull/2152) (unit 1), [#2155](https://github.com/bajutsu-e2e/bajutsu/pull/2155) (unit 2), [#2164](https://github.com/bajutsu-e2e/bajutsu/pull/2164) (units 3–9) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's reactive system-alert guard clears prompts that block a
[scenario](../../docs/glossary.md#scenario-authoring). Two examples are the notification permission
request and iOS's "Save Password" sheet. Today the Python orchestrator watches for those prompts
from outside the device. During every wait it queries SpringBoard (the iOS system shell) on an
interval. It also samples the accessibility tree every 50 ms and runs a state machine of latches
and notes around both.

We propose to resolve an alert when a request needs the screen, inside the XCUITest runner. The
runner is the resident Swift process that already holds the Simulator connection. When the
orchestrator asks for the element tree, the runner first answers the alert the policy names. It then
takes the snapshot. The reply carries the tree and a record of each answer. The Python alert gate
then shrinks from an 801-line detector to a reader of a few dozen lines. The collapsed-tree proxy stays.

## Motivation

The guard's detection logic has grown into the largest single piece of the wait loop.
`_AlertGuardGate` (`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`) is 801 lines long. It
interleaves three detectors that answer overlapping questions:

- **The native SpringBoard probe**, rate-limited to `poll_interval` (one second by default).
- **The in-tree dismissal** for alerts the application raises itself. It runs on the 50 ms poll,
  but only on a poll whose probe has reported no SpringBoard alert (BE-0399).
- **The collapsed-tree proxy**, which samples every poll and explains a blocked screen afterward.

Each detector keeps its own latches and notes. We tuned each one against a race the others create.
A change to one, such as BE-0418's retry, must account for all three.

The structure follows from where detection runs. XCUITest has no callback for "an alert appeared".
Its sole entry point is the interruption monitor that BE-0399 installed in the runner
(`BajutsuKit/Runner/Sources/RunnerUITest.swift`). XCUITest calls that monitor when it evaluates an
interaction, and during some application-level queries. An alert the application raises in its own
process never reaches the monitor. The orchestrator polls from outside instead, across a
Hypertext Transfer Protocol (HTTP) round trip. The runner's single main thread kept that polling
sparse (BE-0315). The process boundary also splits one decision across two languages. The runner
presses a SpringBoard button by pushed policy, while Python taps an application-owned button by the
same policy resolved a second time.

An alert matters to a run at one moment: when the next request looks at the screen. A watcher on a
second, independent clock would answer alerts nobody is looking at yet. We resolve the alert where
the orchestrator reads the screen. The runner does the work inside the request, in the order the
orchestrator already sends requests. Nothing records between two requests: every record arises
inside one, and the end-of-step drain collects whatever a reply did not carry. The design needs no
reservation state, no policy generations, and no way to read records without consuming them. It
needs no cached presence bit that a later `/tap` must re-check. It changes no order of drains.

No scenario changes, and no language model call enters a run (prime directive 1).

A later reader can tell the change arrived by three observations:

- Outside a `handleSystemAlert` step, a wait under a governing guard sends no `/systemAlert/query`,
  and every poll's tree read carries `resolveAlerts=true`. The handle re-read that
  `dismiss_blocking_tip` makes for a showing TipKit tip stays a plain `/elements`. The runner's
  request log shows both.
- The gate file no longer contains the native probe or the in-tree dismissal.
- The showcase scenario that answers the notification request and the "Save Password" sheet together
  (`demos/showcase/scenarios/save_password_browser.yaml`) stays green on iOS 18.6, 26.3, 26.4, and 26.5. BE-0399 measured those four versions.

## Detailed design

The work moves in order. We measure first, then build the runner side, then the Python side. The
old path goes after on-device verification, and the documentation goes with it. Every runner change
sits behind a capability token that the XCUITest
[backend](../../docs/glossary.md#driver-backend-actuator-platform) advertises. The adb and
Playwright backends keep today's gate, so prime directive 3 holds.

### Unit 1 — Measure before building (gate)

Unit 1 adds no product code. A throwaway runner build measures four facts on iOS 18.6, 26.3, 26.4,
and 26.5:

1. **Whether `app.snapshot()` calls the interruption monitor.** Every `GET /elements` calls
   `app.snapshot()`. If the snapshot calls the monitor, the SpringBoard check in Unit 2 drops out.
   The in-tree rule pass alone remains. A plain `/elements` then answers SpringBoard alerts too.
   Three parts of the design must then be revisited before Unit 2 starts:
   - Unit 4's claim that evidence tree dumps show the real screen.
   - Unit 3's reliance on `resolveAlerts=inTree` to leave the step's own alert alone.
   - The two outputs that only step 1 produces: the *unidentified* record behind `alert_block_note`,
     and the `springboardChecked` fact that lets the collapsed-tree proxy stop a wait early.
2. **The cost of a SpringBoard check.** The spike records the 50th-percentile (p50) and
   95th-percentile (p95) time of one `springboard.alerts.firstMatch.exists` call. That number sets
   the check's rate limit.
3. **The time `/elements` takes when it presses a button.** BE-0399's activity log shows one
   press at about 1.6 seconds. The spike compares the longest observed `/elements` against the 30-second actuation window that a resolving read gets. Unit 2 defines the window as `_ACTUATION_TIMEOUT_SECONDS` in `bajutsu/common/drivers/xcuitest/_functions.py`.
4. **The cost per step.** Per version and on one host, the showcase suite runs ten
   times on the baseline runner and ten times on the candidate. The candidate passes when its median
   step duration is within 5 % of the baseline. Its p95 step duration must be within 10 %, and no
   run may crash the runner.

The thresholds in the fourth item are starting values for review. The spike records them with
the measured numbers in this item.

#### Unit 1 results

We measured the four facts on 2026-10-11 and 2026-10-12, on one Apple silicon machine. The runner
build was throwaway and stays out of the repository. Each iOS version ran on its own new Simulator.
Versions 26.3, 26.4, and 26.5 ran on an iPhone 17 Pro. Version 18.6 ran on an iPhone 16 Pro,
because the iPhone 17 Pro does not support it. The build gave `GET /elements` two modes:

- **Probe mode** checks SpringBoard before and after the snapshot and presses nothing. It counts
  the interruption handler's calls in each part of the request. While an alert
  shows, it takes ten more snapshots and twenty timed SpringBoard checks.
- **Candidate mode** runs Unit 2's step 1 before the snapshot. That step checks SpringBoard at most
  once per second. It presses the button that the pushed native policy picks. Candidate mode resolves on
  every `/elements`, evidence reads included. The Python gate also keeps its own probe. The cost
  below is thus an upper bound on what Unit 2 adds.

Both modes ran three scenarios on all four versions:

- `permission.yaml`
- `save_password_browser.yaml`
- `save_password_interrupts_step.yaml`

1. **`app.snapshot()` never reaches the interruption handler, on any of the four versions**. We
   took 11 single reads and 110 repeated snapshots while a SpringBoard alert showed. The handler
   ran zero times, and the alert stayed up after every snapshot. Every version had at least two such
   reads. The SpringBoard check never reached the handler either, in 341 checks made while an alert
   showed.

   For the notification request these scenarios raise, a plain `/elements` stays a pure read.
   Unit 2's step 1 stays necessary. The three parts of the design that hinge on this fact need no
   revision: Unit 4's evidence tree dumps, Unit 3's `resolveAlerts=inTree`, and step 1's outputs.
2. **A SpringBoard check costs more when no alert shows**. To answer "absent",
   `springboard.alerts.firstMatch.exists` searches the whole SpringBoard tree. A showing alert ends
   the search at the first match. The absent figures pool two sources: the probe runs and every
   rate-limited check of the fourth fact's candidate runs. That makes about 3,500 checks per
   version.

   | iOS | Absent p50 | Absent p95 | Showing p50 | Showing p95 |
   |---|---|---|---|---|
   | 18.6 | 68 ms | 156 ms | 40 ms | 68 ms |
   | 26.3 | 41 ms | 120 ms | 30 ms | 48 ms |
   | 26.4 | 40 ms | 109 ms | 31 ms | 57 ms |
   | 26.5 | 36 ms | 95 ms | 38 ms | 66 ms |

   At one check per second, an absent check holds the main thread 4–7 percent of the time at
   p50. In the fourth fact, this share adds little to a typical step. It costs 0–3 ms at the
   matched-step median and 28–40 ms on `scroll`. Unit 2 keeps the one-second
   `pollIntervalSeconds` default.
3. **A resolving `/elements` that presses a button stays well inside the 30-second window**. The
   presses took 0.7–2.1 seconds. The longest pressing `/elements` took 3.1 seconds, on iOS 26.5
   ("Don’t Allow").

   One read that pressed nothing took 24.7 seconds. It ran during `save_password_browser.yaml` on
   iOS 26.5. The SpringBoard check took 154 ms of it. The snapshot took the other 24.6 seconds,
   SafariViewService read included. A plain `/elements` takes the snapshot too. The stall still shows
   a thinner margin in the 30-second actuation window than the press latency suggests. A resolving
   read gets that window in place of BE-0207's retry.
4. **Matched-step cost stays near 1 percent, and one candidate run lost its runner**. Each
   version ran the showcase suite ten times per runner, alternating baseline and candidate.
   The suite used the tag selection of `make run-swiftui`, minus `select_photos.yaml`. On iOS 18.6,
   that file's photo seeding (`simctl addmedia`) timed out here and ended the whole run.
   Scenarios that broke on every baseline run broke the same way on the candidate. One more
   scenario broke more often on the candidate; the paragraphs after the table cover it.

   | iOS | Median, baseline → candidate | p95, baseline → candidate | Matched-step median difference | Runner lost |
   |---|---|---|---|---|
   | 18.6 | 0.346 s → 0.327 s (−5.6 percent) | 2.937 s → 2.894 s (−1.5 percent) | −0.4 ms | 0 / 0 |
   | 26.3 | 0.351 s → 0.352 s (+0.4 percent) | 3.337 s → 3.373 s (+1.1 percent) | +0.9 ms | 0 / 0 |
   | 26.4 | 0.333 s → 0.305 s (−8.4 percent) | 3.220 s → 3.204 s (−0.5 percent) | +1.8 ms | 0 / 0 |
   | 26.5 | 0.243 s → 0.234 s (−3.5 percent) | 3.009 s → 2.966 s (−1.4 percent) | +3.3 ms | 0 / 1 |

   The pooled medians moved by up to 8 percent in the candidate's favor. An added check cannot make
   a step faster. Those swings reflect which steps each run completed, not the candidate's cost.
   At this noise level, the 5 percent pooled-median criterion cannot resolve a real change. The
   verdict rests on the matched-step column instead, and on p95, which stays within 2 percent. The
   matched-step column pairs the same step of the same scenario in the same iteration. By that
   measure, the candidate adds 0–3 ms per step, about 1 percent of the median. `scroll` alone
   shows a steady cost, of 28–40 ms per step. A scroll reads `/elements` more than once.

   The lost runner breaks the criterion that no run may take the runner down. On iOS 26.5, a
   notification banner interrupted a `swipe`. XCUITest's interruption handling then could not
   snapshot the banner ("Automation type mismatch"). The test case ended. That path is BE-0416's
   banner handling, which the baseline runner shares. The candidate's last `/elements` before the
   loss returned as usual. In 40 runs each, the baseline lost none and the candidate lost
   one. Review decides whether that single loss holds Unit 2 back. We read it as an existing banner
   defect the measurement surfaced, not a cost of resolving.

   One scenario broke more often on the candidate on iOS 26: the first scenario of
   `codegen_android.yaml`. Its first step taps `{ id: search }` without a wait. It broke on 1–2 of
   10 baseline runs per version and on 3–7 of 10 candidate runs. On iOS 26, the Search tab
   carries the identifier `Search` in some trees and none in others. The tap passes when its one
   read lands on a tree that still carries it. On iOS 26 the candidate's SpringBoard check delays
   the snapshot by about 40 ms at p50, and the tap then tends to lose that race. The scenario is the UI Automator
   codegen fixture, and its iOS run depends on that race with or without resolving. Unit 2 still
   moves snapshot timing by the cost of a SpringBoard check. A scenario that races the first
   rendered tree will feel that shift.

### Unit 2 — An alert-aware `/elements`

`GET /elements` gains a query parameter, `resolveAlerts`, which takes `false` (the default), `true`,
or `inTree`. A plain read stays a pure read. With `true`, the handler runs four steps on
`APIHandler.operations`, the runner's serial operation queue:

1. **Check SpringBoard.** The handler checks `springboard.alerts` at most once per
   `pollIntervalSeconds`. The orchestrator pushes that interval from
   `systemAlertHandling.pollInterval` (one second by default). A recovery read after a refused
   actuation or a failed check (Unit 5) skips this limit. `resolveAlerts=true` accepts a companion
   flag, `forceCheck`, that every recovery read in Unit 5 sets.
   - When an alert is up and `InterruptionPolicy.label(for:)` picks a button, the handler taps it
     and records *answered*.
   - When no rule picks a button, the handler taps nothing and records *unidentified* on every
     check.
   - A native rule's showing is keyed on the matched rule's identifying labels, so one showing is
     recorded *answered* once. It is pressed again only under the pacing memo described below.
   - The key re-arms when a later check no longer finds the showing.
   - The key also re-arms when an actuation on the application completes after the press. The
     actuation is one that the runner synthesizes: a `/tap`, a gesture, typing, or step 3's in-tree
     tap. XCUITest synthesizes such an interaction only once no alert blocks it.
   - A press on a SpringBoard alert's own button re-arms no key. That covers the presses of step 1,
     the monitor, `/systemAlert/resolve`, and `/systemAlert/tap`. XCUITest synthesizes such a press
     while the alert is still up. The actuation the monitor interrupted still re-arms the key when
     it completes.
   - Without that distinction, a press that did not land would re-arm its own key. The showing would
     be recorded *answered* again and never reach the tap ceiling.
   - A refused actuation (`not-found`, `not-hittable`, or `stale`) synthesized nothing, so it
     re-arms no key.
   - The second trigger never re-arms an in-tree key, because an in-tree alert does not block
     synthesis. An actuation the monitor interrupted completes after the monitor's press, and one it
     did not interrupt shows the alert was already gone.
   - Without the second trigger, a prompt that recurs between two checks would read as the first
     showing still up. The paste prompt on every paste is one example. The monitor would record no
     *answered* for it, and its presses would count toward the first showing's ceiling.
   - A SpringBoard *answered* record is immediate, because the runner pressed the button. The alert
     is usually gone by the end of the press. A press that did not land is pressed again under the
     memo.
2. **Take the application snapshot**, as a plain `/elements` does.
3. **Apply the in-tree rules.** A pushed in-tree rule matches when two conditions hold. Every
   identifying label appears exactly once among the snapshot's labelled buttons that carry no
   identifier (the set `tree_buttons` in `bajutsu/common/drivers/elements.py` builds today). And no
   excluded label appears. The handler tries the rules in the order they arrive (Python sends `tree_dedup_rules` order), so a nested shape's wider sibling comes first.
   - On a match, the handler consults the pacing memo first. It re-checks SpringBoard in the same
     handler only when the memo would let it tap.
   - That re-check counts toward step 1's `pollIntervalSeconds` limit. The exception is a request
     whose own step 1 just queried SpringBoard: there the re-check always runs.
   - A match the memo withholds or declines, or one that falls inside that limit, leaves the in-tree
     alert for a later request without querying SpringBoard.
   - The handler taps only when the re-check finds no SpringBoard alert. Otherwise it leaves the
     in-tree alert for a later request.
   - After the tap, the handler takes a fresh snapshot. The in-tree tap records *answered* at the
     tap, meaning the runner pressed this button.
4. **Reply.** The reply returns the tree with the records this request drained. `/tap` already folds
   its drained records into its reply in the same way (BE-0407 Unit 6, `APIHandler.swift`). The
   reply always carries the records field, empty when nothing happened. It also carries two facts:
   - `springboardChecked` is `true` when this reply's own step 1 queried SpringBoard and found no
     alert, and step 3's re-check, when it ran, found none either.
   - `withheld` is `true` when the pacing memo kept this request from pressing a showing that
     already holds a key, inside the re-tap delay. The earlier press may have come from a resolving
     read, the interruption monitor, or `/systemAlert/resolve`.

   The driver raises `XcuitestChannelError` when a `resolveAlerts` reply lacks the records field, so
   a stale runner build fails loudly instead of returning a plain tree.

The re-check in step 3 keeps an order today's gate enforces. An XCUITest tap made while a
SpringBoard alert is up would let the monitor answer that alert first.

**Pacing.** Native and in-tree showings share one pacing memo, a small per-showing memo in
`InterruptionPolicyStore`. It holds three values:

- the re-tap delay;
- the per-showing tap ceiling;
- the give-up bound: how long the handler keeps retrying an in-tree button that is present but not
  yet hittable (a scrim mid-animation) before it records *gave up* for that showing, as
  `_decline_giveup` bounds it today.

These constants move from `waits/_alert_guard_gate.py` and `waits/_functions.py` into
`bajutsu/common/orchestrator/types/alert_guard_config.py`, and the orchestrator pushes them. Python
still derives the give-up bound from `poll_interval`. The in-tree tap keeps today's pacing.

- A native showing that a later check still finds after the re-tap delay is pressed again. The
  handler stops at the per-showing tap ceiling and records *gave up* for that showing.
- An in-tree showing that reaches the ceiling does the same, as `_TREE_DISMISS_MAX_TAPS` makes it
  give up today.
- The interruption monitor consults and updates the same per-showing key, but only to decide whether to record. It always presses the button its rule picks, inside the re-tap delay and past the tap ceiling alike. A declined interruption would hand the alert to XCUITest's default button (`RunnerUITest.swift`).
- A monitor press on a showing that already has a key records no new *answered*, so the
  at-most-one-*answered* rule holds on every path.

An in-tree showing is keyed on the tapped label, as `_tree_dismiss_pending` is today, not on the
rule. `savePassword`'s nested shapes all tap "Not Now", and a fading sheet read with a subset of its
buttons matches the narrower sibling. A per-rule key would tap that sibling again inside the re-tap
delay and record a second *answered*.

For an in-tree rule, the memo also keeps the tree's signature at the tap (labels and identifiers), as
`tree_signature` does today. A label may still match after the re-tap delay on a changed screen. That
label is an application button the sheet was covering. The handler declines it for the rest of the
showing, and records no *gave up* for it.

**Records.** *Unidentified* and *gave up* are new record kinds. The runner's store, the drain reply,
and `DrainedInterruptions` each gain a field for them. That field stays apart from `unmatched`, which
remains the monitor's decline and the only kind that fails a step.

- An *answered* record carries its rule's surface (`native` or `inTree`). `DrainedInterruptions`
  keeps the surface, so a reader can tell a SpringBoard press from an in-tree one.
- A re-press of the same showing, native or in-tree, records no new *answered*.
- A give-up records *gave up* beside the *answered* and withdraws nothing. The record names the
  rule's identifying labels, its surface, and the button it chose. `uncleared_prompt_note` names
  that button and tells the author the press did not clear the alert.
- One showing therefore yields at most one *answered* and at most one *gave up*.

**Policy pushes.** `InterruptionPolicyStore.setPolicy` clears the pending records on every push.
Inside a scenario, Python drains before it pushes. The push at scenario start
(`bajutsu/common/runner/pipeline.py`) discards the previous scenario's records on purpose.

- The scenario-start push carries a `scenarioStart` mark. It also clears the per-showing keys and
  the pacing memo, so nothing a previous scenario answered or declined suppresses the same prompt
  after a relaunch.
- The pushes inside a scenario (`_reserve_declared_alert` and its restore) keep the keys and the memo.
  A showing that spans them is neither re-pressed early nor recorded twice.
- A key re-arms when its alert is gone. A native key also re-arms when a synthesized actuation
  completes after the press (Unit 2, step 1).

**Wire format.** The wire format gains two optional fields per rule: an `exclude` list, and the
rule's surface (`native` or `inTree`, from `ResolvedAlertRule`). At the policy level, it also gains
`pollIntervalSeconds`, the three pacing values above, and the `scenarioStart` mark.

- `ResolvedAlertRule` carries `native` and `in_tree` as two independent flags. No declared prompt
  sets both today. `push_interruption_policy` raises `ValueError` on a rule that does, rather than
  pushing it on one surface only. It already raises on a native-reachable rule with an exclusion
  set.
- `push_interruption_policy` (`bajutsu/common/orchestrator/types/_functions.py`) stops dropping
  in-tree rules before the push. It sends them in `AlertGuardConfig.tree_dedup_rules` order. The
  runner keeps the wire order, so `_widest_first` stays in Python alone.
- The interruption monitor and the SpringBoard check in step 1 match `native` rules alone, through
  `InterruptionPolicy.label(for:)`. The in-tree pass in step 3 matches `inTree` rules alone and
  honors `exclude`.
- The refusal of an exclusion set stays for a native-reachable rule. Such a rule reaches the
  monitor's subset match, which would discard the exclusion.
- Foreground notification banners keep BE-0416's swipe path.

**Retry eligibility.** A `resolveAlerts` read is not idempotent, because it may press a button.
BE-0207 lets the driver re-send a read whose response timed out after delivery. The driver treats a
`resolveAlerts` read like a write instead: it retries a request that was never delivered and fails
loudly after delivery.

- The change goes in `_is_retry_eligible` (`bajutsu/common/drivers/xcuitest/_functions.py`), which
  today treats every `GET` as idempotent. BE-0287's crash-recovery re-issue uses the same predicate,
  so one change covers both.
- For the same reason, `_timeout_for` gives a `resolveAlerts` read the 30-second actuation window
  (`_ACTUATION_TIMEOUT_SECONDS`) that a write gets. Today it gives every `GET` the 15-second read
  window, because a read can lean on that retry.

### Unit 3 — A `POST /systemAlert/resolve` endpoint

`POST /systemAlert/resolve` applies the pushed policy to whatever SpringBoard alert is showing. A
`handleSystemAlert` step uses it to answer an alert other than its own.

The endpoint drains the runner's store into its reply, as `/tap` does. The driver folds that reply
into `_drain_carry`, so the drain that consumes it reports the answer once. That drain is
`_policy_answered_alert` during the step's wait. The reply's records also reach the gate, as
`query_resolving`'s do (Unit 4).

`_policy_answered_alert` stays in that branch and runs after `resolve_system_alert`. An alert of the step's own that the monitor answered between polls therefore still lets the step proceed (BE-0406 Unit 2b).

Its selector match counts native answers alone. An in-tree *answered* record carries its surface through the drain. An application-owned button that shares the step's label is therefore reported as an unrelated alert, and it never passes the step.

The step keeps its own `/systemAlert/query` polls, which never resolve anything. Under a governing
guard, a step that names its button by label also makes its tree reads through
`query_resolving(inTree)`. Only that kind of step calls `resolve_system_alert` below. A step
answering by position (BE-0445) and a step with the guard off make no tree reads and send no
`/systemAlert/resolve`, because their waits run without the gate today.

The `inTree` mode runs Unit 2's steps 2 to 4 and skips the SpringBoard check in step 1. An application-owned alert such as the Save Password sheet is therefore still cleared while the step waits (BE-0406).

Step 3's SpringBoard re-check finds the step's own alert and withholds the in-tree tap. This matches `probe_native`'s `"reserved"` answer today.

When the alert the step sees does not match the step's selector, Python decides so with the existing
`selector_names_button` (`bajutsu/common/orchestrator/types/_functions.py`). Python then calls
`resolve_system_alert`, which sends `/systemAlert/resolve`.

- Python makes that call at most once per `systemAlertHandling.pollInterval`, the cadence
  `probe_native` keeps today. Each call makes its own SpringBoard query. Calling it on every
  0.2-second `/systemAlert/query` poll would double the step's query rate for as long as an alert
  the step is not waiting for holds the screen (BE-0315).
- On a poll that skips the call, `_policy_answered_alert` still runs, as today.
- The step's claim on its own alert becomes a choice made per request, with no runner state beyond
  today's policy push.
- The policy push that a `handleSystemAlert` step makes through `_reserve_declared_alert`
  (`loop/_step_runner.py`, BE-0406 Unit 2b), and the restore after the step, stay unchanged under
  `RESOLVE_ALERTS`.
- Selector matching stays in Python, so no Swift port of it is needed.

The request carries the button labels the step's last `/systemAlert/query` saw. The runner answers
only when the alert showing now offers exactly that label set. Otherwise it returns `changed` and taps
nothing, so the step's own alert, arriving in place of the one Python judged, is never answered by
policy.

The endpoint shares Unit 2's per-showing key and pacing memo.

- A showing it already answered is pressed again only after the re-tap delay, up to the per-showing
  ceiling, and is then recorded *gave up*. A re-press records no new *answered*.
- A call the memo withholds taps nothing and returns a fifth reply, `pending`, so the step never
  reads a withheld call as a press.
- When no alert is showing, the endpoint returns `absent`.
- When it finds an alert that no rule names, it records *unidentified*, returns `unidentified`, and
  taps nothing. The `handleSystemAlert` step keeps waiting, as it does today.

### Unit 4 — The thin Python path

The wait loop reaches the new behavior through a narrow driver protocol, `AlertResolvingTarget` in
`bajutsu/common/drivers/base/`, beside `InterruptionPolicyTarget`. It has two methods:

- `query_resolving(mode, force_check=False)` takes `true` or `inTree`. It returns the elements
  together with that reply's records.
- `resolve_system_alert(labels)` sends Unit 3's `POST /systemAlert/resolve` with the label set the
  step last saw. It returns the status (`answered`, `changed`, `absent`, `unidentified`, or `pending`) together with that reply's records.

The `RESOLVE_ALERTS` token marks a backend that implements the protocol. `Driver.query` and every
other backend stay unchanged, and evidence capture keeps calling `Driver.query`.

The gate reads the records of `resolve_system_alert` the same way it reads `query_resolving`'s. A
*gave up* or *unidentified* record from `/systemAlert/resolve` during a `handleSystemAlert` wait
therefore reaches the timeout as a note (`uncleared_prompt_note` or `alert_block_note`). The
`_with_block_note` helper appends that note to the step's own timeout reason, as it appends the
gate's note today, so the reason still names the buttons the step's selector missed.

The driver also folds both replies' records into `_drain_carry`. The gate reads the returned records
without consuming anything, and the drain that consumes the records reports each one once.

On a backend that advertises `RESOLVE_ALERTS`, the wait loop and selector resolution under a
governing guard call `query_resolving(true)`, which sends `GET /elements?resolveAlerts=true`. The
exception is a `handleSystemAlert` step.

- Selector resolution here means the reads an action handler makes through `Driver.query()` to
  resolve its target before acting. They are `scroll.py`'s reads and, in `gestures.py`, the value
  read `_do_clear` makes before its focusing tap and `_directional_endpoints`.
- A handle-based actuation resolves its target inside the plain `/elements` that mints the handle.
  Handle-based actuations are `tap`, `double_tap`, `long_press`, `pinch`, `rotate`, and
  `set_picker_value`. Such an actuation
  sends no resolving read of its own.
- A governing guard is the step's configured `systemAlertHandling` guard (`_LoopConfig.alert_guard`),
  whichever body run makes the read.
- The TipKit retry and the alert retry re-run the step body without the mid-wait gate today. Under
  the capability they still send resolving reads, because a resolving read keeps no state that a
  re-run could re-arm.
- The rule covers every guarded wait, `screenChanged` included, so its frozen-screen stop keeps
  working.

[Evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage) screenshots and tree dumps
never set the flag, so evidence shows the real screen. A scenario that turns the guard off keeps
calling `Driver.query`, so it sends no resolving read.

The gate shrinks to a few dozen lines plus the kept collapsed-tree proxy. It reads the records that
`query_resolving` returns and keeps them in the step's state. It also builds the blocked-screen note
from two kinds of record:

- *Unidentified* records give `alert_block_note`, which names the buttons no rule identifies.
- *Gave up* records give `uncleared_prompt_note`, which names the button a rule chose but could not
  clear.

A note built from either record outranks the collapsed-tree proxy's label-less note. The proxy never
clears it, as the `_native_unhandled` and `_tree_gave_up` latches ensure today (BE-0402).

- An *unidentified* note holds until a reply's `springboardChecked` is true. During a
  `handleSystemAlert` wait, it also ends when the step's own `/systemAlert/query` no longer reads the
  buttons the note names.
- A *gave up* note holds until the alert it names is shown gone, as the `_tree_gave_up` latch lifts
  today.
  - For an in-tree record, the alert is gone when a reply's tree shows the application and no
    declared in-tree shape nests with the record's identifying labels (contains them or is contained
    by them). The shape counts only when all its identifying labels appear among `tree_buttons`. The
    helper `_tree_gave_up_shape_matches` decides this today. A sheet that re-presents with a
    narrower sibling shape alone therefore keeps the note.
  - For a native record, the alert is gone on the same conditions that lift an *unidentified* note.

The gate keeps today's collapsed-tree proxy on top of `springboardChecked`. That flag is a fact of
the same request, not a cached licence. Once the tree has stayed collapsed for `frozenScreenTimeout`
under a scenario that declares an in-tree rule, a collapsed reply whose `springboardChecked` is true
sets the stuck note through `collapsed_tree_note` and stops the wait early. A reply whose flag is
false never sets it. The native probe's "absent" does the same today, for the iOS 26.5 Save Password
sheet left mid-presentation.

During a `handleSystemAlert` wait, the proxy also stays idle while the step's own `/systemAlert/query`
reads an alert its selector names, as `probe_native`'s `"reserved"` answer keeps it idle today. A
step that times out with its own alert still up therefore gains no label-less note.

Each record is reported once, by the drain that consumes it. The end-of-step drain
(`_drain_step_interruptions` in `bajutsu/common/orchestrator/loop/_step_runner.py`) is the sole
reporter, except for four drains that keep their role today:

- `_policy_answered_alert` in the `handleSystemAlert` wait (`waits/_functions.py`)
- the `expect` phase's drain (`loop/_functions.py`)
- `_reserve_declared_alert`'s drain before its push
- the drain before the restore push in the same step's `finally` (both in `loop/_step_runner.py`)

The end-of-step drain fails the step by name on a declined record (BE-0406), as today. An
*unidentified* record feeds the blocked-screen note and leaves the step running, so a scenario that
answers the prompt in a later `handleSystemAlert` step keeps working.

The driver puts each reply's records into its existing `_drain_carry`. A resolving `/elements` drains
the runner's whole store, so its reply marks the carry current, as a `/tap` reply does. Every other
call still clears `is_current`, because the monitor records during any XCUITest interaction and those
replies carry nothing. Such calls include a plain `/elements` (which fires the monitor too if Unit 1
finds the snapshot does), a gesture, and typing. Under that rule, the carry-only fast path in
`drain_interruptions()` stays valid.

Python composes both note texts through the existing `alert_block_note` and `uncleared_prompt_note`
(`bajutsu/common/orchestrator/types/_functions.py`), so the wording keeps one home.

### Unit 5 — A narrower retry

After an actuation, the once-per-step alert retry now covers an application-owned alert that no
resolving read cleared before the actuation. Two cases fit that description:

- The alert appeared after the last resolving read. That read may be a guarded wait's poll several
  steps back, because a handle-based actuation sends no resolving read of its own (Unit 4).
- A resolving read left the alert for a later request because a SpringBoard alert was up (Unit 2,
  step 3).

A SpringBoard alert in that gap usually needs no retry, because the monitor answers it when the tap
is synthesized. A `not-hittable` refusal returns before `tap()` runs
(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`), so the recovery read below forces the
SpringBoard check.

**Recovery after an actuation.** The recovery runs under a governing guard on any of three triggers:

- a definite `not-found` or `not-hittable` refusal from the runner
- an `ElementNotFound` that a handle-based actuation (`tap`, `double_tap`, `long_press`, `pinch`, `rotate`, `set_picker_value`) raises from its own plain handle read, before it sends its request or
  while it re-resolves a stale handle
- an `ElementNotFound` that an action handler raises from its selector-resolution read (Unit 4)

On a trigger, the step resolves the selector again through `query_resolving(true, force_check=True)`.
That resolving `/elements` skips the SpringBoard rate limit. The step then waits for the screen to
settle through the existing `settle_after_alert_dismiss` (`waits/_functions.py`), as
`AlertGuardConfig.__call__`'s rounds do today, in any of three cases:

- the reply's records show an answer
- the reply sets `withheld`
- a resolving read that the failed body run made (a guarded wait's poll or a selector-resolution
  read) returned an *answered* record

The last case covers an alert that the body's own read answered and that is gone by the time of the
recovery read. The recovery read alone then shows neither an answer nor `withheld`.

After the settle, the step runs the body once more, at the end-of-step site described below. Whether
or not the body runs again, the reply's records supply the blocked-screen note. A refused tap under
an unnamed alert therefore still fails with that note rather than a bare `not-hittable`. The
recovery never follows a write whose outcome is unknown, because a second delivery could double-actuate
(BE-0207).

**Recovery after a failed check.** A failed check keeps a one-time recovery of the same kind. The
`assert` step, the `expect` phase, `extract`, and `if` keep reading through `Driver.query`, so a read
never changes the screen in the middle of a check.

- When an `assert` step or the `expect` phase fails under a governing guard, the step sends one
  `query_resolving(true, force_check=True)`, which skips the SpringBoard rate limit.
- When that reply shows an answer or sets `withheld`, the step waits through
  `settle_after_alert_dismiss` and the check runs once more. A failed `assert` re-runs the step body
  at the end-of-step site below. A failed `expect` is evaluated again in its own phase.
- Whether or not the check runs again, the reply's records supply the blocked-screen note that
  `__call__` supplies today.
- An `extract` takes no recovery, as today. It runs after the end-of-step site and its drain
  (`loop/_step_runner.py`), so a recovery there would leave its records for the next step's drain.
  Re-running a mutating step's body to repeat the extract would actuate twice.
- An `if` condition never fails, so it takes no recovery. As today, a condition that an alert makes
  false takes the `else` branch.

The `expect` phase runs after the last end-of-step drain, so it drains once more after its recovery
read and re-run, as its second drain does today. The recovery's records are then reported in this
scenario. They never wait in `_drain_carry`, which no policy push clears, for the next scenario's
first drain.

**Paths that no longer run.** Under the capability, two other paths stay off: the end-of-step retry
in `AlertGuardConfig.__call__` (BE-0418), and the `expect` path's own call to `__call__`
(`bajutsu/common/orchestrator/loop/_functions.py`).

- The recovery for a refused actuation and for a failed `assert` runs at the end-of-step site that
  `__call__` occupies today, after the TipKit retry. It re-runs the step body once.
- The recovery never runs inside the step body, so the TipKit retry's second body run cannot spend a
  second one. A step therefore makes at most one alert retry, whether it follows a refused actuation
  or a failed check.
- The TipKit retry after `_dismiss_blocking_tip` (`loop/_step_runner.py`) is unchanged.
- A `handleSystemAlert` step that answers by position (BE-0445) waits without the gate. Today
  `__call__` at the end-of-step site is the only place where such a step that failed learns what
  blocked the screen. Under a governing guard, the step takes the same recovery at that site. It
  sends one `query_resolving(true, force_check=True)`, whose records supply the blocked-screen note.
  On an answer or `withheld`, it settles through `settle_after_alert_dismiss` and re-runs the step
  body once.
- A failed `handleSystemAlert` step that names its button by label still takes no end-of-step
  recovery, as today, because its own wait already drove the guard.

### Unit 6 — Prevention

Prevention makes the reactive path rarer, but it cannot remove that path. Unit 6 adds three pieces:

- **Launch arguments.** The documentation names the `targets.<name>` launch arguments (`launchArgs`
  and `launchEnv`) as the supported way to skip an application's own prompts under test.
- **AutoFill Passwords.** A spike tries an opt-in `simulator.autofillPasswords: off`, which turns
  off the Simulator's AutoFill Passwords setting. It ships if the setting holds on all four iOS
  versions.
- **Notifications and App Tracking Transparency (ATT) stay reactive.** BE-0276 records that
  notification authorization is not a Transparency, Consent, and Control (TCC) service. We believe
  `simctl privacy` has no ATT service either. The spike confirms that belief.

### Unit 7 — Delete the old Python detection path

After on-device verification, the Python side loses what Units 2 to 5 replace. The collapsed-tree
proxy stays, so the removal covers the native probe and the in-tree dismissal alone.

- It covers `probe_native` and `_observe_native`, together with both halves of each
  `AlertGuardConfig.__call__` round.
- On a backend without `HANDLE_SYSTEM_ALERT`, `probe_native` returns `"incapable"`, and the round
  ends before `dismiss_from_tree_once`. Only a genuinely empty `"absent"` read licenses that tap, so
  no such backend taps the tree today. Once XCUITest moves to `RESOLVE_ALERTS`, the tree half has no
  backend left to run on, and it goes too.
- Unit 7 then checks what remains of `__call__`, such as a note another backend still needs. It
  deletes `__call__` itself when nothing does.
- It also covers `_dismiss_from_tree` with its latches and its licence to tap, and the alert
  threading in `waits/_functions.py`.

No flag restores the deleted path, because a second switch would be a second vocabulary for one
behavior.

A backend without the capability keeps today's gate. The explicit `handleSystemAlert` step keeps
`/systemAlert/query` and `/systemAlert/tap` (BE-0316).

### Unit 8 — Verification

- **Swift.** `FakeElementProvider` in `BajutsuKit/Tests/BajutsuRunnerTests/` drives the
  `resolveAlerts` handler without a Simulator. The tests cover the following:
  - The handler's step order and the SpringBoard rate limit.
  - `forceCheck` skipping the rate limit, and `inTree` skipping step 1.
  - Step 3's SpringBoard re-check withholding the in-tree tap while a SpringBoard alert is up.
  - Exclusion, and matching in the order the rules arrive (Swift never reorders them).
  - *Unidentified* recorded on every check, and a native showing tapped and recorded once.
  - The `InterruptionPolicyStore` entry point that the interruption monitor calls. For a showing that
    already has a key, it answers "press, do not record", inside the re-tap delay and past the
    ceiling alike. The monitor's closure (`RunnerUITest.swift`) sits outside the `BajutsuRunner`
    package target that these tests build, so the decision lives in that entry point.
  - Re-arming of a native key. An actuation that completes after a press re-arms the key, whether or
    not the monitor interrupted it. A prompt that recurs before the next check is therefore recorded
    *answered* on each showing.
  - No re-arming from a refused actuation, from a press on a SpringBoard alert's own button (step 1,
    the monitor, `/systemAlert/resolve`), or from any actuation on an in-tree key. An unlanded native
    press therefore still reaches the ceiling and *gave up*.
  - The pacing memo and the *gave up* record. A native showing still up after the re-tap delay is
    re-pressed up to the ceiling, then recorded *gave up*.
  - `/systemAlert/resolve` sharing the pacing memo, re-pressing up to the ceiling, and returning
    `pending` for a withheld call.
  - `POST /systemAlert/resolve`, including its `answered`, `changed`, `absent`, `unidentified`, and
    `pending` replies and the label-set guard.
  - The `springboardChecked` and `withheld` facts in the reply.
  - A push inside a scenario keeping the per-showing keys and the pacing memo, and a `scenarioStart`
    push clearing them.
  - An in-tree *answered* recorded at the tap, and a later *gave up* recorded beside it without
    withdrawing it.
  - A changed-screen signature declining a re-tap.
  - A fading nested in-tree shape that matches a narrower sibling not being re-tapped inside the
    re-tap delay.
- **Python.** The fake actuator implements the capability. The tests cover the following:
  - The thin gate and the report-once rule (each record is reported by the drain that consumes it),
    with an *unidentified* record that leaves the step running.
  - A `handleSystemAlert` step's in-tree-only polls, and its call to `/systemAlert/resolve`.
  - A `/systemAlert/resolve` give-up during a `handleSystemAlert` wait reaching the timeout as
    `uncleared_prompt_note`.
  - A note from an *unidentified* or *gave up* record surviving the collapsed-tree proxy's later
    polls.
  - An *unidentified* note from `/systemAlert/resolve` clearing once the step's own
    `/systemAlert/query` no longer reads the buttons it names.
  - `push_interruption_policy` raising `ValueError` on a rule that sets both `native` and `in_tree`.
  - The narrowed retry never following a write whose outcome is unknown.
  - The retry after a `not-hittable` tap on a target that an in-tree sheet still covers because step 3
    deferred it, with its recovery read forced.
  - A refused tap on a target that a just-answered, still-fading alert covers. The pacing memo
    withholds the forced recovery read, and the step settles and re-runs once on `withheld`.
  - A `swipe` whose selector-resolution read raises `ElementNotFound` under a SpringBoard alert that
    the rate-limited check skipped. The step sends one forced recovery read and re-runs once on an
    answer.
  - A refused tap, or a selector-resolution `ElementNotFound`, after the body's own resolving read
    answered an in-tree alert that is gone by the recovery read. The step settles and re-runs once.
  - A failed `assert` or `expect` check sending one `query_resolving(true, force_check=True)`. On an
    answer, it settles through `settle_after_alert_dismiss`, re-runs once, and takes its note from
    that reply.
  - A failed `extract` sending no recovery read and never re-running the step body.
  - A failed position-rule `handleSystemAlert` step (BE-0445) sending one
    `query_resolving(true, force_check=True)` at the end-of-step site and taking its blocked-screen
    note from that reply, while a failed label-named one sends none.
  - A TipKit retry or alert retry re-run sending `query_resolving(true)` for its guarded waits.
  - A failed `expect` check's recovery records being reported by the `expect` phase's own drain,
    never by the next scenario's.
  - No call to `AlertGuardConfig.__call__` under the capability, from either the end-of-step retry or
    the `expect` path.
  - A backend without `HANDLE_SYSTEM_ALERT` still tapping no in-tree prompt once `__call__`'s halves
    are gone.
  - `_is_retry_eligible` refusing to re-send a delivered `resolveAlerts` read, in both the BE-0207
    retry and the BE-0287 recovery re-issue.
  - `XcuitestChannelError` on a `resolveAlerts` reply that lacks the records field.
  - `_timeout_for` giving a `resolveAlerts` read the actuation window.
  - The collapsed-tree proxy stopping a wait early only on a reply whose `springboardChecked` is true.
  - `query_resolving` returning each reply's records and also folding them into the carry.
  - An in-tree answer whose label matches the step's selector not passing a `handleSystemAlert` step.
  - A step's own alert, answered by the monitor between polls, still letting the `handleSystemAlert`
    step proceed under the capability.
- **On device.** Three showcase scenarios run green on the four iOS versions with Unit 7 applied:
  - `demos/showcase/scenarios/permission.yaml`
  - `demos/showcase/scenarios/save_password_browser.yaml` (BE-0399's two-prompt scenario)
  - `demos/showcase/scenarios/save_password_interrupts_step.yaml`

### Unit 9 — Documentation

`DESIGN.md`, `docs/architecture.md`, and their `docs/ja/` mirrors describe the guard as a Python
detector. The change that deletes the detector updates them as well (BE-0113).

## Alternatives considered

| Option | What it does | Why it was not adopted |
|---|---|---|
| Runner-resident watcher thread on its own clock | The previous version of this proposal. A runner thread sweeps for alerts every interval and answers them. | An independent clock records between requests. That forced reservation endpoints, policy generations, and a Swift port of selector matching with regular-expression parity checks. It also forced a way to read records without consuming them, a presence bit with a `/tap` re-check, and changes to drain order. We would reconsider it if on-device verification (Unit 8) shows an alert must clear while no request is in flight. |
| Runner raises a flag, Python decides | The runner reports a visible alert on every response, and Python taps. | The tap round trip and the gate's state machine stay, so the complexity this item targets remains. |
| Prevention alone | Configure the Simulator and the application so no prompt appears. | Prevention cannot reach notifications, ATT, or banners. It cannot serve a scenario that tests the prompt itself either. Unit 6 keeps it as a complement. |
| Explicit steps alone, with fail-by-name | Every prompt needs a `handleSystemAlert` step, and an unexpected prompt fails the step by name. | The most deterministic option. Prompts and banners arrive asynchronously, though, which pushes timing onto scenario authors. It could become an opt-in strict mode later. |
| Server push to the orchestrator | The runner streams alert events over Server-Sent Events (SSE) or a WebSocket. | The runner answers the alert itself, so the orchestrator gains no new information from a second channel. |
| Resolve the button in Swift from the alert's text | The runner decides the button without a pushed policy. | BE-0399 rejected this, because the candidates, per-prompt rules, and locale table live in Python. The policy stays there. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — measure snapshot behavior, SpringBoard cost, press latency, and step cost (gate)
- [x] Unit 2 — alert-aware `/elements` behind `resolveAlerts`
- [x] Unit 3 — `POST /systemAlert/resolve` for the `handleSystemAlert` step
- [x] Unit 4 — thin Python path behind the `RESOLVE_ALERTS` capability
- [x] Unit 5 — narrowed once-per-step retry
- [x] Unit 6 — prevention: launch arguments and an AutoFill Passwords spike
- [x] Unit 7 — delete the old Python detection path
- [x] Unit 8 — Swift, Python, and on-device verification
- [x] Unit 9 — documentation in both languages

Log:

- [#2152](https://github.com/bajutsu-e2e/bajutsu/pull/2152) — the Unit 1 measurement. A throwaway runner
  build measured the four facts on iOS 18.6, 26.3, 26.4, and 26.5. *Unit 1 results* holds the numbers.

  `app.snapshot()` never reaches the interruption handler, so Unit 2 keeps its SpringBoard check. The
  one-second rate limit stays too. One candidate run lost its runner to the BE-0416 banner path.
  Review weighs that loss before Unit 2 starts.

  The PR also fixes a BE-0428 regression. `bajutsu run` refused a whole scenarios dir even after
  `--exclude` had removed its self-declaring files. That refusal kept `make run-swiftui` from running
  as is during the measurement.
- [#2155](https://github.com/bajutsu-e2e/bajutsu/pull/2155) — Unit 2. Before it started, the
  maintainer judged the Unit 1 runner loss to be an existing defect in the BE-0416 banner path, not
  a cost of resolving, and let Unit 2 proceed.

  `GET /elements` gains `resolveAlerts` (`true` or `inTree`) and `forceCheck`. A resolving read
  checks SpringBoard at most once per pushed `pollIntervalSeconds` and takes the snapshot. It then
  answers the first matching in-tree rule after a SpringBoard re-check finds no alert. The reply
  drains every record and carries `springboardChecked` and `withheld`.

  `InterruptionPolicyStore` holds the per-showing keys and the pacing memo. The interruption monitor,
  the resolving read, and actuation completions share them. The store also records *unidentified* and
  *gave up*. `push_interruption_policy` sends the in-tree rules in `tree_dedup_rules` order, with the
  guard's pacing and a `scenarioStart` mark. It raises `ValueError` on a rule that claims both
  surfaces.

  The driver treats a resolving read as a write for the BE-0207 retry, the BE-0287 re-issue, and the
  timeout window. It raises `XcuitestChannelError` on a reply without records, and folds the records
  into `_drain_carry` as current. Nothing sends a resolving read yet: Unit 4 adds `query_resolving`
  and the `RESOLVE_ALERTS` token.

  The change departs from the design text in one place. The design says `push_interruption_policy`
  already refuses a native rule with an exclusion set, but it does not. `notifications` is pushed
  with its exclusion, and the runner honors it, so this change adds no such refusal. The pacing
  constants moved to `alert_guard_config.py`, and the gate reads them from there until Unit 7.

  Until Unit 4 sends resolving reads, a completed actuation is the only event that re-arms a native
  key. Suppose the interaction the monitor interrupted then raises. The key stays, and the same prompt
  returning before the next completed actuation is pressed but not reported again. The design accepts
  this window. The checks in Unit 4's resolving reads close it.
- [#2164](https://github.com/bajutsu-e2e/bajutsu/pull/2164) — Units 3 to 9, completing the item.

  The runner gains `POST /systemAlert/resolve`. It answers by policy while the alert still offers the
  labelled buttons the step read, and not otherwise. It shares the pacing memo with the resolving
  read. Python gains `Capability.RESOLVE_ALERTS` and `AlertResolvingTarget`. Under a governing guard,
  waits and action selector reads go through a resolving view. Assertions and evidence keep the plain
  read.

  The gate shrinks to a reader of the records plus the collapsed-tree proxy. The end-of-step,
  `expect`, and scenario-entry recoveries each send one forced resolving read. Unit 7 deletes
  `probe_native`, `dismiss_from_tree_once`, `AlertGuardConfig.__call__`, and the gate's native probe
  and in-tree dismissal.

  Four showcase scenarios ran on device: `permission.yaml`, `permission_system_alert.yaml`,
  `save_password_browser.yaml`, and `save_password_interrupts_step.yaml`. Each passed on iOS 18.6,
  26.3, 26.4, and 26.5, before Unit 7 and again after it.

  The first device run exposed a Unit 2 defect: `LegacyBackedTransport` matched routes on the full
  path, so `GET /elements?resolveAlerts=true` answered 404. Unit 2's tests called the handler
  directly. This change fixes the routing and adds an over-the-wire test. The change departs from the
  design text in five places:
  - The scenario-entry check (`_clear_entry_alert`) also used `probe_native`. The design did not list
    it. It now sends one forced resolving read.
  - A press inside a read is not a driver actuation. A step's actuation record carries no
    `systemAlert` entry for it any more; the press reaches the report as an alert event.
  - An `email` step takes no alert retry any more. The narrowed retry covers four failures: a refused
    actuation, an unfound target, a check, and a position-rule `handleSystemAlert` step.
  - `/systemAlert/resolve` compares the labelled buttons alone, the set the driver's own read
    reports. It answers `pending` for a showing the memo stopped, as for one it withheld.
  - Unit 6 documents `launchArgs` and `launchEnv` as the prevention path and confirms that
    `simctl privacy` has no notifications or ATT service. The AutoFill Passwords spike found no
    supported way to turn the setting off before a run. On 26.5 the switch did not flip under
    automation, and toggling it changed no preference file that a pre-run step could write. The
    `simulator.autofillPasswords` opt-in does not ship.

## References

- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
  — the rule against re-sending a delivered write.
- [BE-0276 — Declarative per-scenario permission state](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)
  — why notification authorization sits outside `simctl privacy`.
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
  — the crash-recovery re-issue that shares the retry predicate.
- [BE-0315 — Native system-alert handling](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  — the native SpringBoard probe and the single-main-thread load argument.
- [BE-0316 — Explicit mid-flow step for permission-prompt alerts](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)
  — the `handleSystemAlert` step and its explicit endpoints.
- [BE-0399 — Answer an interrupting alert by the scenario's policy](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md)
  — the monitor, the pushed policy, and the drain this item reuses.
- [BE-0406 — Declare system alerts by prompt alone](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md)
  — failing a step by name, and why application-owned rules never reach the monitor.
- [BE-0407 — Cut step latency by deduplicating evidence reads and tuning driver internals](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md)
  — the records `/tap` folds into its reply (Unit 6).
- [BE-0416 — Swipe away an interrupting iOS notification banner reactively](../BE-0416-ios-notification-banner-swipe-dismiss/BE-0416-ios-notification-banner-swipe-dismiss.md)
  — the banner path this item leaves in place.
- [BE-0418 — Let the end-of-step alert guard clear more than one alert](../BE-0418-ios-alert-guard-one-shot-retry/BE-0418-ios-alert-guard-one-shot-retry.md)
  — the end-of-step retry this item turns off under the capability.
- [`BajutsuKit/Runner/Sources/RunnerUITest.swift`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift)
  — the interruption monitor.
- [`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift)
  — the serial `operations` queue and the `/tap` reply fold.
- [`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift`](../../BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift)
  — the pushed policy, its matching discipline, and the record store.
- [`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`](../../bajutsu/common/orchestrator/waits/_alert_guard_gate.py)
  — the gate this item thins.
- [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py)
  — the end-of-step drain that reports the step's records.
