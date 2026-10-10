**English** · [日本語](BE-XXXX-request-boundary-alert-resolution-ja.md)

# BE-XXXX — Resolve system alerts at runner request boundaries instead of watching from Python

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-request-boundary-alert-resolution.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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
then shrinks from an 801-line detector to a reader of a few dozen lines plus the kept collapsed-tree proxy.

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
reservation state, no policy generations, and no way to read records without consuming them. It also needs no cached presence bit that a later `/tap` must re-check, and no
change to the order of drains.

No scenario changes, and no language model call enters a run (prime directive 1).

A later reader can tell the change arrived by three observations:

- Outside a `handleSystemAlert` step, a wait under a governing guard sends no `/systemAlert/query`,
  and every poll's tree read carries `resolveAlerts=true`. The handle re-read
that `dismiss_blocking_tip` makes for a showing TipKit tip stays a plain `/elements`. The runner's request log shows both.
- The gate file no longer contains the native probe or the in-tree dismissal.
- The showcase scenario that answers the notification request and the "Save Password" sheet together
  stays green on iOS 18.6, 26.3, 26.4, and 26.5, the four versions BE-0399 measured.

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
   The in-tree rule pass alone remains. A plain `/elements` then answers SpringBoard alerts too, so three parts of the design must be revisited before Unit 2 starts: Unit 4's claim that evidence tree dumps show the real screen; Unit 3's reliance on `resolveAlerts=inTree` to leave the step's own alert alone; and the two outputs only step 1 produces, the *unidentified* record behind `alert_block_note` and the `springboardChecked` fact that lets the collapsed-tree proxy stop a wait early.
2. **The cost of a SpringBoard check.** The spike records the 50th-percentile (p50) and
   95th-percentile (p95) time of one `springboard.alerts.firstMatch.exists` call. That number sets
   the check's rate limit.
3. **The time `/elements` takes when it presses a button.** BE-0399's activity log shows one
   press at about 1.6 seconds. The spike compares the longest observed `/elements` against the
   15-second read window (`bajutsu/common/drivers/xcuitest/_functions.py`).
4. **The cost per step.** Per version and on one host, the showcase suite runs ten
   times on the baseline runner and ten times on the candidate. The candidate passes when its median
   step duration is within 5 % of the baseline. Its p95 step duration must be within 10 %, and no
   run may crash the runner.

The thresholds in the fourth item are starting values for review. The spike records them with
the measured numbers in this item.

### Unit 2 — An alert-aware `/elements`

`GET /elements` gains a query parameter, `resolveAlerts`, which takes `false` (the default), `true`,
or `inTree`. A plain read stays a pure read. With `true`, the handler runs four steps on
`APIHandler.operations`, the runner's serial operation queue:

1. **Check SpringBoard.** The handler checks `springboard.alerts` at most once per
   `pollIntervalSeconds`. The orchestrator pushes that interval from
   `systemAlertHandling.pollInterval` (one second by default). When an alert is up and
   `InterruptionPolicy.label(for:)` picks a button, the handler taps it and records *answered*. When
   no rule picks one, the handler taps nothing and records *unidentified* on every check. A native
   rule's showing is keyed on the matched rule's identifying labels, so one showing is recorded *answered* once.
   It is pressed again only under the pacing memo described below. The key re-arms when a later check no longer finds it. A SpringBoard *answered* record is immediate, because the runner pressed the button. The alert is usually gone by the end of the press. A press that did not land is pressed again under the memo.
2. **Take the application snapshot**, as a plain `/elements` does.
3. **Apply the in-tree rules.** A pushed in-tree rule matches when every identifying label appears
   exactly once among the snapshot's labelled buttons that carry no identifier (the set `tree_buttons` in `bajutsu/common/drivers/elements.py` builds today) and no excluded label appears. The handler tries the rules in the order they arrive (Python sends `tree_dedup_rules` order), so a nested shape's wider sibling comes
   first. On a match it re-checks SpringBoard in the same handler. It taps only when no SpringBoard alert is up; otherwise it leaves the in-tree alert for a later request. It then takes a fresh snapshot. The in-tree tap records *answered* at the tap, meaning the runner pressed this button.
4. **Reply.** The reply returns the tree with the records this request drained. `/tap` already folds
   its drained records into its reply in the same way (BE-0407 Unit 6, `APIHandler.swift`). The reply always carries the records field, empty when nothing happened. It also carries `springboardChecked`, which is `true` when this reply's own step 1 queried SpringBoard and found no alert. The driver raises
   `XcuitestChannelError` when a `resolveAlerts` reply lacks the records field, so a stale runner build fails
   loudly instead of returning a plain tree.

The re-check in step 3 keeps an order today's gate enforces. An XCUITest tap made while a
SpringBoard alert is up would let the monitor answer that alert first.

**Pacing.** Native and in-tree showings share one pacing memo, a small per-rule memo in
`InterruptionPolicyStore`. It holds three values:

- the re-tap delay;
- the per-showing tap ceiling;
- the give-up bound.

These constants move from `waits/_alert_guard_gate.py` and `waits/_functions.py` into
`bajutsu/common/orchestrator/types/alert_guard_config.py`, and the orchestrator pushes them. Python
still derives the give-up bound from `poll_interval`. The in-tree tap keeps today's pacing. A native
showing that a later check still finds after the re-tap delay is pressed again. The handler stops at
the per-showing tap ceiling.

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
  rule's identifying labels and the button it chose. `uncleared_prompt_note` names that button and
  tells the author the press did not clear the alert.
- One showing therefore yields at most one *answered* and at most one *gave up*.

**Policy pushes.** `InterruptionPolicyStore.setPolicy` clears the pending records on every push.
Inside a scenario, Python drains before it pushes. The push at scenario start
(`bajutsu/common/runner/pipeline.py`) discards the previous scenario's records on purpose.

- The scenario-start push carries a `scenarioStart` mark. It also clears the per-showing keys and
  the pacing memo, so nothing a previous scenario answered or declined suppresses the same prompt
  after a relaunch.
- The pushes inside a scenario (`_reserve_declared_alert` and its restore) keep the keys and the memo.
  A showing that spans them is neither re-pressed early nor recorded twice.
- A key re-arms when its alert is gone.

The wire format gains two optional fields per rule: an `exclude` list, and the rule's surface
(`native` or `inTree`, from `ResolvedAlertRule`). At the policy level it also gains `pollIntervalSeconds`, the three pacing values above, and the `scenarioStart` mark. `ResolvedAlertRule` carries `native` and `in_tree` as two independent flags. No declared prompt sets both today, and `push_interruption_policy` raises `ValueError` on a rule that does, as it already does for a native-reachable rule with an exclusion set, rather than pushing it on one surface only. `push_interruption_policy`
(`bajutsu/common/orchestrator/types/_functions.py`) stops dropping in-tree rules before the push and sends them in `AlertGuardConfig.tree_dedup_rules` order. The runner keeps the wire order, so `_widest_first` stays in Python alone.
The interruption monitor and the SpringBoard check in step 1 match only `native` rules through
`InterruptionPolicy.label(for:)`. The in-tree pass in step 3 matches only `inTree` rules and honors
`exclude`. Its refusal of an exclusion set stays for a native-reachable rule, because such a rule
reaches the monitor's subset match, which would discard the exclusion. Foreground notification
banners keep BE-0416's swipe path.

A `resolveAlerts` read is not idempotent, because it may press a button. BE-0207 lets the driver
re-send a read whose response timed out after delivery. The driver treats a `resolveAlerts` read
like a write instead: it retries a request that was never delivered and fails loudly after delivery.
The change goes in `_is_retry_eligible` (`bajutsu/common/drivers/xcuitest/_functions.py`), which
today treats every `GET` as idempotent. BE-0287's crash-recovery re-issue uses the same predicate,
so one change covers both.

### Unit 3 — A `POST /systemAlert/resolve` endpoint

`POST /systemAlert/resolve` applies the pushed policy to whatever SpringBoard alert is showing. It drains the runner's store into its reply, as `/tap` does, and the driver folds that reply into `_drain_carry`, so the drain that consumes it, `_policy_answered_alert` during the step's wait, reports the answer once. A `handleSystemAlert` step uses it to answer an alert other than its own. `_policy_answered_alert` stays in that branch and runs after `resolve_system_alert`, so an alert of the step's own that the monitor answered between polls still lets the step proceed (BE-0406 Unit 2b). Its selector match counts only native answers:
an in-tree *answered* record carries its surface through the drain, so an application-owned button
that shares the step's label is reported as an unrelated alert and never passes the step.

The step keeps its own `/systemAlert/query` polls, which never resolve anything. The tree reads the step makes alongside them go through `query_resolving(inTree)`. That mode runs Unit 2's steps 2 to 4 and skips the
SpringBoard check in step 1, so an application-owned alert such as the Save Password sheet is still
cleared while the step waits (BE-0406). Step 3's SpringBoard re-check finds the step's own alert and
withholds the in-tree tap, as `probe_native`'s `"reserved"` answer does today. When the alert the
step sees does not match the step's selector, Python decides so with the existing
`selector_names_button` (`bajutsu/common/orchestrator/types/_functions.py`). Python then calls `resolve_system_alert`, which sends `/systemAlert/resolve`. The step's claim on its own alert becomes a choice made per request, with no runner state beyond today's policy push. The policy push a `handleSystemAlert` step makes through `_reserve_declared_alert` (`loop/_step_runner.py`, BE-0406 Unit 2b), and the restore after the step, stay unchanged under `RESOLVE_ALERTS`. Selector matching stays in Python, so no Swift port of it is needed.

The request carries the button labels the step's last `/systemAlert/query` saw. The runner answers
only when the alert showing now offers exactly that label set. Otherwise it returns `changed` and
taps nothing, so the step's own alert, arriving in place of the one Python judged, is never answered
by policy. When no alert is showing, the endpoint returns `absent`. When it finds an alert that no
rule names, it returns `unidentified` and taps nothing. The `handleSystemAlert` step keeps waiting,
as it does today.

### Unit 4 — The thin Python path

The wait loop reaches the new behavior through a narrow driver protocol, `AlertResolvingTarget` in `bajutsu/common/drivers/base/`, beside `InterruptionPolicyTarget`. Its `query_resolving(mode)` takes `true` or `inTree` and returns the elements together with that reply's records. Its `resolve_system_alert(labels)` sends Unit 3's `POST /systemAlert/resolve` with the label set the step last saw and returns `answered`, `changed`, `absent`, or `unidentified`. The `RESOLVE_ALERTS` token marks a backend that implements it. The driver also folds both replies' records into `_drain_carry`, so the gate reads the returned records without consuming anything and the drain that consumes the records reports each one once. `Driver.query` and every other backend stay unchanged, and evidence capture keeps calling `Driver.query`.

On a backend that advertises `RESOLVE_ALERTS`, the wait loop and selector resolution under a governing guard call `query_resolving(true)` (which sends `GET /elements?resolveAlerts=true`), except
inside a `handleSystemAlert` step.
[Evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage) screenshots and tree dumps
never set the flag, so evidence shows the real screen. A scenario that turns the guard off keeps calling `Driver.query`, so it sends no resolving read.

The gate shrinks to a few dozen lines plus the kept collapsed-tree proxy. It reads the records that `query_resolving` returns and keeps them in the step's state. It also builds the blocked-screen note: from *unidentified* records through `alert_block_note`, which names the buttons no rule identifies, and from *gave up* records through `uncleared_prompt_note`, which names the button a rule chose but could not clear.

The gate keeps today's collapsed-tree proxy on top of `springboardChecked`. That flag is a fact of the same request, not a cached licence. Once the tree has stayed collapsed for `frozenScreenTimeout` under a scenario that declares an in-tree rule, a collapsed reply whose `springboardChecked` is true sets the stuck note through `collapsed_tree_note` and stops the wait early. A reply whose flag is false never sets it. The native probe's "absent" does the same today, for the iOS 26.5 Save Password sheet left mid-presentation.

Each record is reported once, by the drain that consumes it. Outside three drains that keep their role today, the end-of-step drain (`_drain_step_interruptions` in `bajutsu/common/orchestrator/loop/_step_runner.py`) is the sole reporter. The three are `_policy_answered_alert` in the `handleSystemAlert` wait (`waits/_functions.py`), the `expect` phase's drain (`loop/_functions.py`), and `_reserve_declared_alert`'s drain before its push (`loop/_step_runner.py`). The end-of-step drain fails the step by name on a declined record (BE-0406), as today. An
*unidentified* record feeds the blocked-screen note and leaves the step running, so a scenario that
answers the prompt in a later `handleSystemAlert` step keeps working. The driver puts each reply's
records into its existing `_drain_carry`. A resolving `/elements` drains the runner's whole store,
so its reply marks the carry current, as a `/tap` reply does. Every other call still clears
`is_current`, because the monitor records during any XCUITest interaction and those replies carry
nothing. That includes a plain `/elements` (which fires the monitor too if Unit 1 finds the snapshot
does), a gesture, and typing. Under that rule, the carry-only fast path in `drain_interruptions()`
stays valid. Python composes both note texts through the existing `alert_block_note` and `uncleared_prompt_note` (`bajutsu/common/orchestrator/types/_functions.py`), so the wording keeps one home.

### Unit 5 — A narrower retry

The once-per-step alert retry now covers a single case: an application-owned alert that appears between the resolving `/elements` and the `/tap`, a gap
that includes the plain `/elements` `Driver.tap` sends to mint the element's handle. A SpringBoard alert in that gap needs no retry, because
the monitor answers it during the tap.

On a definite `not-found` or `not-hittable` refusal from the runner, or an `ElementNotFound`
that `Driver.tap` raises from its own handle read before it sends `/tap`, the step resolves the selector again through `query_resolving(true)`, a resolving `/elements`. When that reply's records show an answer, the step re-issues the actuation once. It never follows a write
whose outcome is unknown, because a second delivery could double-actuate (BE-0207).

Under the capability, two other paths no longer run. The end-of-step retry in
`AlertGuardConfig.__call__` (BE-0418) stays off. The `expect` path's own call to `__call__`
(`bajutsu/common/orchestrator/loop/_functions.py`) stays off too. As a result, a step makes at
most one alert retry. The TipKit retry after `_dismiss_blocking_tip` (`loop/_step_runner.py`) is unchanged.

### Unit 6 — Prevention

Prevention makes the reactive path rarer, but it cannot remove that path. Unit 6 adds three pieces:

- **Launch arguments.** The documentation names `targets.<name>` launch arguments (`launchArgs` and
  `launchEnv`) as the supported way for an application to skip its own prompts under test.
- **AutoFill Passwords.** A spike tries an opt-in `simulator.autofillPasswords: off`, which turns
  off the Simulator's AutoFill Passwords setting. It ships if the setting holds on all four iOS
  versions.
- **Notifications and App Tracking Transparency (ATT) stay reactive.** BE-0276 records that
  notification authorization is not a Transparency, Consent, and Control (TCC) service. We believe
  `simctl privacy` has no ATT service either. The spike confirms that belief.

### Unit 7 — Delete the old Python detection path

After on-device verification, the Python side loses what Units 2 to 5 replace. The collapsed-tree proxy stays, so the removal covers only the native probe and the in-tree dismissal. It covers `probe_native` and `_observe_native`, together with the native half of each `AlertGuardConfig.__call__` round. On a backend without `HANDLE_SYSTEM_ALERT`, `probe_native` returns an empty "absent" at once, and that empty read is what licenses the same round's `dismiss_from_tree_once` tap there. The tree half therefore stays, and it runs unconditionally on the backends that keep `__call__`. It also covers `_dismiss_from_tree` with its latches and its
licence to tap, and the alert threading in `waits/_functions.py`. No flag restores them, because a
second switch would be a second vocabulary for one behavior.

A backend without the capability keeps today's gate. The explicit `handleSystemAlert` step keeps
`/systemAlert/query` and `/systemAlert/tap` (BE-0316).

### Unit 8 — Verification

- **Swift.** `FakeElementProvider` in `BajutsuKit/Tests/BajutsuRunnerTests/` drives the
  `resolveAlerts` handler without a Simulator. The tests cover:
  - the handler's step order and the SpringBoard rate limit;
  - exclusion, widest-first matching, *unidentified* recorded on every check, and a native showing tapped and recorded once;
  - the pacing memo and the *gave up* record;
  - a native showing still up after the re-tap delay re-pressed up to the ceiling, then recorded *gave up*;
  - `POST /systemAlert/resolve`, including its `answered`, `changed`, `absent`, and `unidentified` replies and the label-set guard;
  - the `springboardChecked` fact in the reply;
  - a push inside a scenario keeping the per-showing keys and pacing memo, and a `scenarioStart` push clearing them;
  - an in-tree *answered* recorded at the tap, and a later *gave up* recorded beside it without withdrawing it;
  - a changed-screen signature declining a re-tap.
- **Python.** The fake actuator implements the capability. The tests cover:
  - the thin gate, and the report-once rule (each record is reported by the drain that consumes it), with an *unidentified* record that
    leaves the step running;
  - a `handleSystemAlert` step's in-tree-only polls, and its call to `/systemAlert/resolve`;
  - the narrowed retry, which never follows a write whose outcome is unknown;
  - no call to `AlertGuardConfig.__call__` under the capability, from either the end-of-step retry or the `expect` path;
  - without the capability, the end-of-step `__call__` still clears an in-tree prompt after `probe_native` is gone;
  - `_is_retry_eligible` refusing to re-send a delivered `resolveAlerts` read, in both the BE-0207 retry and the BE-0287 recovery re-issue;
  - `XcuitestChannelError` on a `resolveAlerts` reply that lacks the records field;
  - the collapsed-tree proxy stops a wait early only on a reply whose `springboardChecked` is true;
  - `query_resolving` returns each reply's records and also folds them into the carry;
  - an in-tree answer whose label matches the step's selector does not pass a `handleSystemAlert` step;
  - under the capability, a step's own alert answered by the monitor between polls still lets the `handleSystemAlert` step proceed.
- **On device.** `demos/showcase/scenarios/permission.yaml`, BE-0399's two-prompt scenario, and
  `demos/showcase/scenarios/save_password_interrupts_step.yaml` run green on the four iOS versions
  with Unit 7 applied.

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

- [ ] Unit 1 — measure snapshot behavior, SpringBoard cost, press latency, and step cost (gate)
- [ ] Unit 2 — alert-aware `/elements` behind `resolveAlerts`
- [ ] Unit 3 — `POST /systemAlert/resolve` for the `handleSystemAlert` step
- [ ] Unit 4 — thin Python path behind the `RESOLVE_ALERTS` capability
- [ ] Unit 5 — narrowed once-per-step retry
- [ ] Unit 6 — prevention: launch arguments and an AutoFill Passwords spike
- [ ] Unit 7 — delete the old Python detection path
- [ ] Unit 8 — Swift, Python, and on-device verification
- [ ] Unit 9 — documentation in both languages

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
