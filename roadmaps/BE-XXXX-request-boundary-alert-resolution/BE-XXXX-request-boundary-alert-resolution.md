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
then shrinks from an 801-line detector to a reader of about 50 lines.

## Motivation

The guard's detection logic has grown into the largest single piece of the wait loop.
`_AlertGuardGate` (`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`) is 801 lines long. It
interleaves three detectors that answer overlapping questions:

- **The native SpringBoard probe**, rate-limited to `poll_interval` (one second by default).
- **The in-tree dismissal** for alerts the application raises itself. It runs on the 50 ms poll,
  but on a poll whose probe has reported no SpringBoard alert (BE-0399).
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
reservation state, no policy generations, and no peek protocol. It also needs no presence bit and no
change to the order of drains.

No scenario changes, and no language model call enters a run (prime directive 1).

A later reader can tell the change arrived by three observations:

- Outside a `handleSystemAlert` step, a wait under a governing guard sends no `/systemAlert/query`,
  and every `/elements` it sends carries `resolveAlerts=true`. The runner's request log shows both.
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
   The in-tree rule pass alone remains.
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
   no rule picks one, the handler taps nothing and records *unidentified*. It neither taps nor
   records one showing twice. A showing is keyed on the matched rule's identifying labels (on the
   button set for an *unidentified* alert), and the key re-arms only when a later check no longer
   finds it.
2. **Take the application snapshot**, as a plain `/elements` does.
3. **Apply the in-tree rules.** A pushed in-tree rule matches when every identifying label appears
   exactly once among the snapshot's buttons and no excluded label appears. The handler tries the
   rules in `AlertGuardConfig.tree_dedup_rules` order, so a nested shape's wider sibling comes
   first. On a match it re-checks SpringBoard in the same handler. It taps and records only when no
   SpringBoard alert is up; otherwise it leaves the in-tree alert for a later request. It then takes
   a fresh snapshot.
4. **Reply.** The reply returns the tree with the records this request drained. `/tap` already folds
   its drained records into its reply in the same way (BE-0407 Unit 6, `APIHandler.swift`). The
   reply always carries the records field, empty when nothing happened. The driver raises
   `XcuitestChannelError` when a `resolveAlerts` reply lacks it, so a stale runner build fails
   loudly instead of returning a plain tree.

The re-check in step 3 keeps an order today's gate enforces. An XCUITest tap made while a
SpringBoard alert is up would let the monitor answer that alert first.

The in-tree tap keeps today's pacing. A small per-rule memo in `InterruptionPolicyStore` holds the
re-tap delay, the per-showing tap ceiling, and the give-up bound. The constants move from
`waits/_alert_guard_gate.py` and `waits/_functions.py` into
`bajutsu/common/orchestrator/types/alert_guard_config.py`, and the orchestrator pushes them. Python
still derives the give-up bound from `poll_interval`. A give-up records *gave up*, naming the rule's
identifying labels.

The wire format gains two optional fields per rule: an `exclude` list, and the rule's surface
(`native` or `inTree`, from `ResolvedAlertRule`). `push_interruption_policy`
(`bajutsu/common/orchestrator/types/_functions.py`) stops dropping in-tree rules before the push.
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

`POST /systemAlert/resolve` applies the pushed policy to whatever SpringBoard alert is showing. It
returns the record it produced. A `handleSystemAlert` step uses it to answer an alert other than its
own.

The step keeps its own `/systemAlert/query` polls, which never resolve anything. Its `/elements`
polls run with `resolveAlerts=inTree`. That mode runs Unit 2's steps 2 to 4 and skips the
SpringBoard check in step 1, so an application-owned alert such as the Save Password sheet is still
cleared while the step waits (BE-0406). Step 3's SpringBoard re-check finds the step's own alert and
withholds the in-tree tap, as `probe_native`'s `"reserved"` answer does today. When the alert the
step sees does not match the step's selector, Python decides so with the existing
`selector_names_button` (`bajutsu/common/orchestrator/types/_functions.py`). Python then calls
`/systemAlert/resolve`. The step's claim on its own alert becomes a choice made per request, with no
state in the runner. Selector matching stays in Python, so no Swift port of it is needed.

The request carries the button labels the step's last `/systemAlert/query` saw. The runner answers
only when the alert showing now offers exactly that label set. Otherwise it returns `changed` and
taps nothing, so the step's own alert, arriving in place of the one Python judged, is never answered
by policy. When no alert is showing, the endpoint returns `absent`. When it finds an alert that no
rule names, it returns `unidentified` and taps nothing. The `handleSystemAlert` step keeps waiting,
as it does today.

### Unit 4 — The thin Python path

A new capability token, `RESOLVE_ALERTS`, marks a backend that implements Units 2 and 3. On such a
backend the wait loop and selector resolution call `/elements` with `resolveAlerts=true`, except
inside a `handleSystemAlert` step.
[Evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage) screenshots and tree dumps
never set the flag, so evidence shows the real screen.

The gate shrinks to about 50 lines with two jobs. It folds each reply's records into the step's
state. It also builds the blocked-screen note from *unidentified* records.

Each record has a single owner. The end-of-step drain (`_drain_step_interruptions` in
`bajutsu/common/orchestrator/loop/_step_runner.py`) stays the sole place that turns records into the
step's `AlertEvent`s. That drain fails the step by name on a declined record (BE-0406), as today. An
*unidentified* record feeds the blocked-screen note and leaves the step running, so a scenario that
answers the prompt in a later `handleSystemAlert` step keeps working. The driver puts each reply's
records into its existing `_drain_carry`. A resolving `/elements` drains the runner's whole store,
so its reply marks the carry current, as a `/tap` reply does. Every other call still clears
`is_current`, because the monitor records during any XCUITest interaction and those replies carry
nothing. That includes a plain `/elements` (which fires the monitor too if Unit 1 finds the snapshot
does), a gesture, and typing. Under that rule, the carry-only fast path in `drain_interruptions()`
stays valid. A *gave up* record withdraws the matching answered event in that drain's result. Python
composes the note text through `uncleared_prompt_note`
(`bajutsu/common/orchestrator/types/_functions.py`), so the wording keeps one home.

### Unit 5 — A narrower retry

The once-per-step retry now covers a single case: an application-owned alert that appears between
the resolving `/elements` and the `/tap`. A SpringBoard alert in that gap needs no retry, because
the monitor answers it during the tap.

The retry fires on a definite `not-found` or `not-hittable` refusal when the step's records show an
answer. It resolves the selector again and re-issues the actuation once. It never follows a write
whose outcome is unknown, because a second delivery could double-actuate (BE-0207).

Under the capability, two other paths no longer run. The end-of-step retry in
`AlertGuardConfig.__call__` (BE-0418) stays off. The `expect` path's own call to `__call__`
(`bajutsu/common/orchestrator/loop/_functions.py`) stays off too. As a result, a step retries at
most once.

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

After on-device verification, the Python side loses what Units 2 to 5 replace. The removal covers
`probe_native` and `_observe_native`. It also covers `_dismiss_from_tree` with its latches and its
licence to tap, and the alert threading in `waits/_functions.py`. No flag restores them, because a
second switch would be a second vocabulary for one behavior.

A backend without the capability keeps today's gate. The explicit `handleSystemAlert` step keeps
`/systemAlert/query` and `/systemAlert/tap` (BE-0316).

### Unit 8 — Verification

- **Swift.** `FakeElementProvider` in `BajutsuKit/Tests/BajutsuRunnerTests/` drives the
  `resolveAlerts` handler without a Simulator. The tests cover:
  - the handler's step order and the SpringBoard rate limit;
  - exclusion, widest-first matching, and the once-per-showing *unidentified* record;
  - the pacing memo and the *gave up* record;
  - `POST /systemAlert/resolve`, including its `changed` and `absent` replies.
- **Python.** The fake actuator implements the capability. The tests cover:
  - the thin gate, and the single-owner rule for reporting, with an *unidentified* record that
    leaves the step running;
  - a `handleSystemAlert` step's in-tree-only polls, and its call to `/systemAlert/resolve`;
  - the narrowed retry, which never follows a write whose outcome is unknown;
  - no call to `AlertGuardConfig.__call__` under the capability.
- **On device.** `demos/showcase/scenarios/permission.yaml`, BE-0399's two-prompt scenario, and
  `demos/showcase/scenarios/save_password_interrupts_step.yaml` run green on the four iOS versions
  with Unit 7 applied.

### Unit 9 — Documentation

`DESIGN.md`, `docs/architecture.md`, and their `docs/ja/` mirrors describe the guard as a Python
detector. The change that deletes the detector updates them as well (BE-0113).

## Alternatives considered

| Option | What it does | Why it was not adopted |
|---|---|---|
| Runner-resident watcher thread on its own clock | The previous version of this proposal. A runner thread sweeps for alerts every interval and answers them. | An independent clock records between requests. That forced reservation endpoints, policy generations, and a Swift port of selector matching with regular-expression parity checks. It also forced a peek protocol, a presence bit with a `/tap` re-check, and changes to drain order. We would reconsider it if Unit 1 shows an alert must clear while no request is in flight. |
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
  — the end-of-step drain that stays the sole reporter.
