**English** · [日本語](BE-0406-system-alert-declared-prompts-ja.md)

# BE-0406 — Declare system alerts by prompt alone, and answer them during a handleSystemAlert step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0406](BE-0406-system-alert-declared-prompts.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0406") |
| Implementing PR | [#1871](https://github.com/bajutsu-e2e/bajutsu/pull/1871) (unit 1), [#1894](https://github.com/bajutsu-e2e/bajutsu/pull/1894) (units 2a, 3), [#1903](https://github.com/bajutsu-e2e/bajutsu/pull/1903) (unit 2b, unit 5), [#1908](https://github.com/bajutsu-e2e/bajutsu/pull/1908) (unit 4) |
| Topic | Platform support |
| Related | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md), [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md), [BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation.md), [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md) |
<!-- /BE-METADATA -->

## Introduction

We propose two changes to how Bajutsu answers an operating-system prompt that the application's own
accessibility tree cannot see.

First, the `handleSystemAlert` step gains a guard. Its wait moves out of the XCUITest driver and
into the orchestrator. A declared alert that interrupts the step is then answered while the step is
still waiting, not after the step has already failed.

Second, a scenario names prompts instead of buttons. `systemAlertHandling` drops `labels`, the
ordered list of candidate button labels. Every path that matched against `labels` is re-keyed to the
`rules` a scenario writes. That includes the in-tree dismissal, the one path that can clear an alert
living in the application's own process. Making the motivating case declarable means adding iOS's
save-password alert to the prompt table. It is the first entry there that SpringBoard does not own.

Two paths answer such a prompt today. A [scenario](../../docs/glossary.md#scenario-authoring) places
a `handleSystemAlert` step at the one point where it expects a prompt, and that step taps a named
button. Separately, a reactive guard configured by `systemAlertHandling` clears a prompt wherever
one interrupts the run. The guard taps a button that the scenario's policy names.

One surface cannot be re-keyed the same way, for a reason worth stating up front. XCUITest resolves
an alert that interrupts one of its interactions *before* it synthesizes that interaction, and
something always answers it. A monitor that declines hands the alert to XCUITest's own default
handler. That handler taps the alert's *default* button and clears it, exactly as happened before
the monitor existed. Declining is the only safe response to an alert no rule identifies. Claiming an
interruption without clearing it gets the monitor re-invoked on every following interaction. BE-0399
measured that looping until the runner died.

Nothing in this proposal can stop that tap from landing. The proposal changes whether the tap
happens in silence. The interruption monitor now reports the buttons of any alert it declined. The
step or `expect` that saw one fails, naming those buttons, instead of the run continuing as if
nothing had answered on the scenario's behalf.

Compatibility is deliberately not preserved. A scenario that still writes `labels` fails to load,
with an error naming `rules` as its replacement.
[BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation.md)
gave the keys it removed the same treatment.

## Motivation

A `handleSystemAlert` step times out when iOS's save-password alert is on screen while the step
waits. We measured this against an application that signs in and then requests notification
authorization. The step never sees the prompt it was placed for and spends its whole `timeout`
polling. It fails with `no system alert appeared within <timeout>s`. Nothing in the run's output
names the alert that was actually up, so the failure reads as a missing permission prompt, not as an
interruption.

The cause is that the step's wait and the guard that could clear the interruption live on opposite
sides of a call. `XcuitestDriver.handle_system_alert` polls `springboard.alerts` to its own deadline
inside `bajutsu/drivers/xcuitest.py`. The reactive guard is `_AlertGuardGate` in
`bajutsu/orchestrator/waits.py`, an orchestrator-side object that the `wait` step drives one poll at
a time. No thread runs the guard in the background. `_run_step_body` in
`bajutsu/orchestrator/loop.py` passes `alert_guard` through for `kind == "wait"` alone. Its own
docstring records that other step kinds ignore it. While the driver's loop runs, no guard can
intervene. The end-of-step guard does fire on any failed step, `handleSystemAlert` included, but
only after the step has spent its full timeout and failed.

The query the step polls cannot see the save-password alert.
[BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md)
measured where each prompt lives. The notification authorization request is a SpringBoard alert in
another process. iOS raises the save-password alert into the *application's own* process instead.
There it reaches the application's own tree as identifier-less labelled buttons. A web form gets
`app.alerts`. The application's own fields get an `app.sheets` sheet. The runner's
`querySystemAlertButtons` reads `springboard.alerts.buttons` and nothing else, so the save-password
alert reads as no alert at all. The step's poll finds an empty button list for as long as the alert
holds the screen. Because the alert is modal over the application, the permission request the step
was placed for is never raised either.

Scenario authors work around the gap today by placing a `wait` step, the one step kind the guard is
wired into. `demos/showcase/scenarios/save_password_browser.yaml` carries a `wait: { until: { gone:
{ label: "Not Now" … } } }` placed for no other purpose. Its comment explains which wait form stays
correct whichever way the operating system's timing falls. The workaround demands that an author
predict when iOS will interrupt. It cannot be written at all when the interruption's timing is
genuinely unknown.

The second half of the problem is what a scenario may declare. `systemAlertHandling` takes two keys.
`rules` names a prompt and a choice. `labels` is an ordered list of candidate button labels,
consulted for whatever no rule identifies. A label names a button, not a prompt. So `labels` lets
the guard tap a button on an alert no scenario ever described.

BE-0399 recorded the danger directly: "Cancel" and "Close" are ordinary application vocabulary that
a real screen can legitimately show. It therefore gated the in-tree dismissal on a scenario having
declared `labels` of its own, and kept that dismissal off the built-in defaults. The gate treats a
scenario's `labels` as a proxy for author intent. A proxy is all it is: the declaration says which
button text to accept, never which alert is expected.

Naming the prompt instead is more than tidier, because the in-tree dismissal is where a declaration
does the most work. `AlertGuardConfig.dismiss_from_tree_once` and
`_AlertGuardGate._dismiss_from_tree` both match through `pick_alert_label(self.labels, …)`. They
match over the identifier-less labelled buttons in the poll's own tree. They are the only mechanism
in the codebase that can clear an alert `springboard.alerts` cannot see. Re-keying them to `rules`
lets a scenario
say *which* alert it expects the guard to clear, not which words it is willing to see tapped.

One answer path cannot simply leave an unmatched alert alone, and today it does not fail on one
either: it grants it. XCUITest resolves an interrupting alert before it synthesizes the interaction
it interrupted. The runner's interruption monitor answers that resolution in one of two ways. It
matches a rule and taps it. Or it declines and hands the alert to XCUITest's own default handler,
which taps whichever button that alert calls its default. The second way is the same silent grant
this document opens with, for any alert `rules` does not name.

`DEFAULT_DISMISSIVE_LABELS` in `bajutsu/orchestrator/types.py` is pushed as the monitor's fallback
candidates today. It narrows how often an alert reaches that grant, by answering conservatively for
any alert that offers one of its words. It does not close the gap. An alert offering none of those
words is declined exactly as before. Unit 2 traces why that decline is unavoidable and, since
BE-0399, deliberately safe.

A later reader can tell whether this proposal arrived from three observable differences:

- A showcase scenario places a `handleSystemAlert` step for the notification prompt while the
  save-password alert holds the screen. It fails today with the timeout quoted above. Afterwards it
  passes, with no `wait` step placed to cover the interruption.
- The migrated demos answer the save prompt with a `savePassword` rule in place of `labels: ["Not
  Now"]`. `save_password_browser.yaml` also drops the `gone` wait it carries as a workaround.
- A scenario runs an ordinary `tap` while an alert that no `rules` entry names interrupts it. Today
  that step passes, or fails for an unrelated reason the alert's own default answer happened to
  cause. After this proposal it fails by name, with the alert's buttons in the report.

## Detailed design

### Unit 1 — move the step's wait into the orchestrator

`Driver.handle_system_alert` stops waiting. It keeps its signature. Callers pass a timeout of zero
for a query-and-tap against an alert already known to be up. `AlertGuardConfig.probe_native` already
does this through `_NATIVE_TAP_TIMEOUT`.

The XCUITest implementation drops the polling loop at `bajutsu/drivers/xcuitest.py:1105-1114` and
resolves the selector against a single `/systemAlert/query` read. No Swift change is needed, and no
other backend is affected. `capability_preflight` already rejects the step on a backend that does
not advertise `HANDLE_SYSTEM_ALERT`. The driver keeps raising `UnsupportedAction` as the mid-run
backstop.

The wait itself moves to the step's handler, `_do_handle_system_alert`. It lives in
`bajutsu/orchestrator/actions/handlers/gestures.py` and gains the guard as a parameter. The handler
builds one `_AlertGuardGate` for the step and polls to the step's own deadline. That is the
shape `_wait` already uses: `_wait` constructs the gate once and calls `gate.observe(elements)` on
each poll. Each poll does two things, in this order:

1. Read `driver.system_alert_labels()`. When the step's own selector resolves against those labels,
   tap it through `driver.handle_system_alert(sel, 0)` and return.
2. Otherwise, hand the poll's tree to `gate.observe`, and keep polling.

The tap in step 1 carries the same time-of-check race that `probe_native` already handles, and
handles it the same way. An `ElementNotFound` means the alert closed itself between the query and
the tap. An `AmbiguousSelector` means the alert is still up and now offers the selector's label
twice. Neither is the step's verdict. The handler keeps polling to its deadline, so a benign race
costs one poll rather than the step.

The outer loop polls at `_POLL` (0.05s), matching `_wait`'s own cadence. That tick is a cheap
timestamp comparison, not a cross-process read on every iteration. The two reads it may trigger keep
their own, independent rates, unchanged from what each already pays today:

- Step 1's own `system_alert_labels()` read is throttled to `_SYSTEM_ALERT_POLL_SECONDS` (0.2s)
  internally. That is exactly the cadence the driver's own polling loop paid before this unit moved
  it. The step's response to its own target prompt is no slower than today.
- `gate.observe()`'s cross-process SpringBoard probe keeps its existing, separate throttle.
  `_observe_native` already rate-limits itself to `guard.poll_interval` regardless of how often
  `observe()` is called (`_last_native`, unchanged by this proposal). BE-0315 already accepts that
  load for every `wait` step a guard covers.

An earlier draft of this unit coupled the two reads to one shared cadence. That was the error. It
would have forced the deliberately wide `pollInterval: 5` of `save_password_browser.yaml` onto the
step's own responsiveness too. The scenario sets that value so both stacked prompts stay up across a
probe.

Under the coupled cadence, the Unit 4 regression scenario's `handleSystemAlert` step would have had
only a `tap`-sized window to notice its own target. That window would sit inside a 5-second gap.
Decoupling the two rates
removes that tension. A scenario that needs a wide `pollInterval` for the guard's SpringBoard probe
pays nothing for it. The step still notices its own prompt as fast as before.

The unit reuses the gate instead of lifting `_dismiss_from_tree` out of it, and that choice makes
the cross-poll state work. The re-tap delay, the decline give-up, and the per-label tap ceiling are
all fields on the gate, carried between polls. A free function extracted from the gate would have
nowhere to keep them. Re-creating the gate per poll would reset them every time.

Reusing the gate also gives the step the ordering BE-0399 established, unchanged and unrestated.
`_observe_native` probes SpringBoard first and answers a rule-identified alert there. It issues an
in-tree tap only on a poll whose own probe just reported no SpringBoard alert.

Checking the step's own selector before the gate runs keeps the two from competing for the same
prompt. A scenario may hold a reactive rule for the prompt the step is placed to answer, with the
opposite choice. An example is `rules: [{ prompt: notifications, choice: deny }]` alongside a step
naming `choice: grant`. Whichever party reads the alert first would decide it.

Step 1 above settles the common case by giving the step the first read on every poll. The residual
case is a prompt that surfaces between the step's read and the gate's own probe within a single
poll. The gate closes that case. The handler passes the step's selector to the gate. `probe_native`
then declines an alert that the selector resolves against, leaving it for the step's next poll. The
gate answers what the step is not waiting for, and nothing else.

`_run_step_body` passes `alert_guard` through for `kind == "handle_system_alert"` alongside `wait`,
and its docstring is updated to name both. The end-of-step guard is unchanged. A step that now
answers interruptions while waiting simply reaches it less often.

When the deadline passes, the failure names what the step saw. The message replaces today's `no
system alert appeared within <timeout>s` with a reason that distinguishes three cases:

1. No alert appeared at all.
2. An alert appeared whose buttons the selector did not match.
3. An undeclared alert held the screen for the wait.

The third case carries the buttons that were on screen, so a reader of the report can add the rule
the scenario was missing.

### Unit 2a — declare an alert by its prompt, and re-key every path that matched a label

`labels` is removed from `SystemAlertHandling` in `bajutsu/scenario/models/scenario.py`. The
`--alert-labels` option is removed from `run`, so `_flag_alert_policy` in
`bajutsu/cli/commands/run.py` keeps only `--alert-poll-interval`. BE-0402 already retired
`--alert-vision-instruction` from `run` with the fallback it steered.

No flag replaces `rules`. An entry pairs a prompt with a choice, and BE-0401 already recorded that
pairing as the reason no flag carries it legibly. A per-prompt declaration therefore stays a
scenario-file and target-config declaration.

`AlertGuardConfig` loses its `labels` field, and the layering table BE-0401 established loses one
row. `rules` stays a list key concatenated innermost layer first. `pollInterval` stays the sole
scalar, resolved by the innermost layer that supplies one. The on/off boolean is unchanged.

Three call sites matched against `labels`, and each is re-keyed rather than deleted:

| Call site | Today | After |
|---|---|---|
| `AlertGuardConfig.probe_native` | `match_alert_rule(self.rules, …)` then `pick_alert_label(self.labels or DEFAULT_DISMISSIVE_LABELS, …)` | `match_alert_rule(self.rules, …)` alone; no rule identified means `"unhandled"`, as today |
| `AlertGuardConfig.dismiss_from_tree_once` | armed by `self.labels` being non-empty (`if not self.labels: return None`), matches `pick_alert_label(self.labels, …)` | armed by holding at least one in-tree-capable rule, matches `match_alert_rule(…)` over those rules |
| `_AlertGuardGate._dismiss_from_tree` | armed by `if self.guard.labels and probed_absent` | armed by at least one in-tree-capable rule *and* `probed_absent`, matches `match_alert_rule(…)` over those rules |

The two in-tree rows are the load-bearing ones. They match over the identifier-less labelled buttons
in the poll's own tree. They are the only mechanism that clears an alert `springboard.alerts` cannot
see, the save-password alert included. Deleting their matching outright, rather than re-keying it,
would leave `rules: [{ prompt: savePassword, choice: deny }]` with no path that can act on it.

Everything else about those two paths is untouched:

- the uniqueness pre-check before the tap;
- the re-tap delay;
- the decline give-up;
- the per-label tap ceiling;
- BE-0399's rule that an in-tree tap is issued only on a poll whose own probe reported no
  SpringBoard alert.

What changes is only the question they ask of the tree. They now ask which declared alert is showing,
instead of which acceptable word is on a button. These three sites are `pick_alert_label`'s only
Python callers, so the function is deleted with `labels`.

"In-tree-capable" in the table above is not a new concept. It is the per-prompt surface record that
Unit 3 introduces, read from the other side. Arming the in-tree paths on *any* rule would widen them
past what an author asked for. A scenario declaring `notifications` alone would arm an in-tree match
for a prompt that only ever appears in SpringBoard. An application screen that happens to show
identifier-less "Allow" and "Don't Allow" buttons would then be tapped. Only a rule whose prompt is
recorded as in-tree-capable arms the in-tree paths, which keeps that widening from happening.

The same record decides what `push_interruption_policy` sends. A rule for an in-tree-only prompt is
dropped from the pushed rule list. The interruption monitor exists for an alert in another process
that interrupts an XCUITest interaction. An alert living in the application's own process never
reaches it.

Dropping the rule is more than tidy. `InterruptionPolicy.label(for:)` matches a rule by subset.
Pushing `savePassword`'s in-app shape would therefore re-open on the Swift side exactly the
collision that Unit 3 closes on the Python side. With those rules never pushed, no rule the monitor
sees carries an exclusion set. Neither of the two changes below has to account for one.

That property holds by construction, not by coincidence. The push refuses a rule that carries an
exclusion set and is not in-tree-only. A later SpringBoard-reachable shape that needs an exclusion
therefore fails loudly. Otherwise it would reach the monitor with its exclusion silently dropped,
which is the subset-match collision one table entry later.

One more place reads `labels`, besides `push_interruption_policy`'s candidate fallback, now removed.
It is not a matching site. `_warn_target_rules_reach` in `bajutsu/cli/commands/run.py` reads
`any(layer.labels for layer in inner_layers)` to decide whether a scenario answers for itself. The
predicate is re-keyed to `any(layer.rules for layer in inner_layers)`.

No vision-fallback instruction needs re-keying alongside it. BE-0402 already deleted
`_vision_instruction` with `run`'s fallback. A scenario's `labels` has not steered anything there
since that landed.

`_resolve_rules` raises `UncoveredSystemAlertLocale` naming `labels` as the remedy for an uncovered
language. The message is rewritten to name the remaining two remedies: add the language to
`bajutsu/scenario/system_alerts.py`, or pin a locale the table covers.

A scenario that still writes `labels` fails to load with an error naming `rules`. BE-0401 removed
`enabled` and two deprecated spellings the same way, and stated plainly that compatibility was not
preserved. This proposal follows that precedent rather than carrying an alias.

### Unit 2b — report the interruption monitor's declined alerts

`DEFAULT_DISMISSIVE_LABELS` is removed rather than kept, and the Swift-side matching it fed is
removed with it. `InterruptionPolicy.candidates` in
`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift` is deleted. `label(for:)` matches
`rules` alone. `InterruptionPolicyRequest` in `openapi.yaml` drops the matching field.

The reason is that declining is already documented as the *safe* answer at this surface, not merely
the least bad one:

- `docs/architecture.md:708-709` records that a prompt the policy names no button on "is left to
  XCUITest's own default handler, unchanged (BE-0399)".
- `docs/scenarios.md:168-169` records that it "is left to XCUITest, which is what happened before
  this existed."
- `RunnerUITest.swift`'s own comment calls declining "the only safe fallback… what happened before
  this monitor existed and does clear it".

The loop that can take a resident runner down comes from a monitor that *claims* an interruption
without clearing it. A monitor that hands the alert back never causes it. Nothing requires the
monitor to guess an answer for an alert `rules` does not name. The built-in candidates only narrowed
how often an alert reached that unrecorded grant. They never closed it.

What replaces the guess is a report. But "the guard actually governs" is not the same fact as "the
pushed rule list is non-empty", and today's `isEmpty` check conflates them.

`InterruptionPolicy` gains a `governs: Bool` field alongside `rules`, and no longer has
`candidates`. `Driver.set_interruption_policy` and `InterruptionPolicyRequest` swap their
`candidates` parameter and field for that same `governs` one. `push_interruption_policy` sets it to
`guard is not None`. That value is true for any scenario whose `systemAlertHandling` is on. It does
not depend on whether any of that scenario's rules survived the in-tree-only filter above.

Without `governs`, a scenario whose *only* rule is `savePassword` pushes an empty rule list once the
in-tree-only filter drops it. Unit 4 makes `save_password_native.yaml` exactly that scenario. An
`isEmpty` check would read the empty list as "this scenario declared nothing". It would silently
reopen the grant that Unit 2b exists to close, for every *other* alert that scenario never ruled on.
A real declaration, filtered down to nothing this surface can act on, is not the same as no
declaration at all.

`governs` is false only when `guard is None`, which means an absent guard or `systemAlertHandling:
false`. That is the one case still meant to keep today's silent, unrecorded grant, since declaring
nothing is the author's own choice.

The monitor records what it could not answer before it declines. `InterruptionPolicyStore` gains a
second drained list beside the one it already keeps for tapped labels. The new list holds the button
labels of each alert that `label(for:)` returned `nil` for, on a poll whose policy `governs`. The
monitor appends to it, then returns `false` exactly as before. The tap that follows is XCUITest's
own, on whichever button the alert calls its default. It is unchanged, because nothing at that point
can change it.

Two related declines record nothing, deliberately:

- `guard policy.governs else { return false }` in `RunnerUITest.swift` runs first and short-circuits
  before `label(for:)` is ever called. An absent guard's decline keeps today's silent grant.
  Declaring nothing is the author's own choice, not an omission this proposal exists to catch.
- A label that did match, whose button then vanished before the tap (`guard button.exists else {
  return false }`), declines without recording anything. This is the race that `probe_native`
  already treats as benign elsewhere in this proposal. The alert was not undeclared; its button
  simply lost a race with its own dismissal.

`POST /interruptionPolicy/drain`'s reply gains an `unmatched` field beside its existing `labels`.
The field holds one button list per declined-and-recorded alert, oldest first.
`Driver.drain_interruptions` in `bajutsu/drivers/base.py` changes its return type from `list[str]`
to a small pair. The pair holds the tapped labels, unchanged, and the button lists of what was
declined. The `drain_interruptions` wrapper in `bajutsu/orchestrator/types.py` turns the second half
into `UndeclaredInterruption` records rather than `AlertEvent`s. Nothing was answered on the
scenario's behalf for those alerts, so there is no dismissal to report.

`run_scenario` in `bajutsu/orchestrator/loop.py` reads that second list everywhere it already drains
the first. Counting the retry, that is three points rather than two.

1. **End of a step.** `outcome.alerts.extend(drain_interruptions(active_driver))` is reached after
   `outcome.ok` and `outcome.reason` are set from the step's own body. An undeclared interruption
   overrides them. The step fails unconditionally, naming the buttons the alert offered, even if it
   otherwise passed. This proposal exists to stop trusting the appearance of success, and a step
   that passed while an unnamed prompt was silently resolved is exactly that appearance, on this
   surface as much as on the other two.
2. **During `expect`.** `expect_alerts.extend(drain_interruptions(driver))` gets the same override,
   which sets `failure` before the assertion results are consulted.
3. **The alert-guard retry in the `expect` phase.** The first drain runs once before the phase's own
   alert-guard retry. Today nothing drains again after the retry. The retry's comment already
   records that "nothing else drains this phase". That used to cost only a missing `AlertEvent`, and
   would now cost a missed failure. So the retry gains the second drain that the comment describes
   as absent. It folds its `UndeclaredInterruption`s into the same check before `failure` is
   decided.

Neither call site's handling of a *matched* interruption changes. Matched interruptions still become
`AlertEvent`s and never fail anything on their own.

### Unit 3 — add the save-password prompt to the label table

`bajutsu/scenario/system_alerts.py` gains `savePassword` in `SystemAlertPrompt` and in `_LABELS`.
The prompt breaks three assumptions the table currently rests on, and closing each is part of this
unit.

**It is not owned by SpringBoard.** The table is a source of button *labels*. A separate choice,
based on whether the SpringBoard query can see the alert, picks the path that taps them. Nothing in
the lookup depends on which process owns the alert. The docstring records the distinction, so that a
later reader does not infer a SpringBoard alert from an entry's presence.

**A `handleSystemAlert` step cannot name it.** The step resolves its selector against
`driver.system_alert_labels()`. That call reads `springboard.alerts` and nothing else. A step naming
`prompt: savePassword` would poll an empty button list until its deadline, which is the very failure
this proposal exists to remove. So `savePassword` is declarable as a `systemAlertHandling` rule
alone. `HandleSystemAlert` rejects it at parse time, with a message naming the rule form, rather
than accepting a step that could never resolve.

This is the first prompt for which the declaration surfaces diverge. The table therefore gains a
per-prompt record of which surfaces a prompt reaches. The three surfaces are the `handleSystemAlert`
step, the guard's native SpringBoard probe, and the guard's in-tree dismissal. That record does
three jobs, as Unit 2a describes:

- It rejects `savePassword` in a step.
- It decides which rules arm the in-tree paths.
- It keeps an in-tree-only rule out of the policy pushed to the interruption monitor.

**It renders three sets of buttons.** The labels below are transcribed from
`WebUI.framework/<lang>.lproj/Localizable.strings`. That file lives in the Simulator runtime's
cryptex at `System/Cryptexes/OS/System/Library/PrivateFrameworks/`. Two properties of that location
matter for anyone re-checking the table under a new runtime, as BE-0320 asks:

- The three prompts already in the table come from frameworks under
  `System/Library/PrivateFrameworks` in the runtime root, outside the cryptex.
- Every `.strings` file is an Apple binary property list, so it yields its contents to `plutil`
  rather than to a plain-text search.

| Alert | iOS | English | Japanese |
|---|---|---|---|
| Raised for a web form | 18.6 and 26.5 | Save Password / Never for This Website / Not Now | パスワードを保存 / このWebサイトでは保存しない / 今はしない |
| Raised for the application's own fields | 18.6 | Save Password / Not Now | パスワードを保存 / 今はしない |
| Raised for the application's own fields | 26.5 | Save / Not Now | 保存 / 今はしない |

The accepting button is what moves. iOS 26.5 carries a key that 18.6 does not: `"Save Password (save
login information sheet in app)"`, whose value is "Save". The same accepting intent therefore reads
"Save Password" on a web form and "Save" in the application's own fields. The refusing button is
"Not Now" in all three, and its Japanese is 今はしない throughout.

`_LABELS` therefore keys a prompt's language entry to a *list* of shapes, not to one `{grant, deny}`
pair. A shape carries the labels that identify the alert, the label each choice taps, and an
optional set of labels whose presence rules the shape *out*. `notifications`, `tracking`, and
`paste` each declare a single shape with no exclusions, and every value they resolve to is
unchanged. `savePassword` declares three shapes, in the order of the table above.

`_resolve_rules` emits one `ResolvedAlertRule` per shape. `match_alert_rule` gains the exclusion
check. A rule matches when every identifying label is present exactly once, as today, *and* no
excluded label is present at all.

The web-form shape needs no exclusion, because no other alert offers its three buttons together. The
18.6 in-app shape is identified by "Save Password" and "Not Now". The web-form alert also satisfies
that pair, which is harmless because both shapes tap the same label for the same choice.

The 26.5 in-app shape is the one that needs the exclusion. Its labels are "Save" and "Not Now". The
same `WebUI.framework` gives a credit-card update sheet "Save", "Never for This Card", and "Not
Now". The pair alone would answer that sheet too. The shape therefore excludes "Never for This
Card", the one label that tells the two apart.

The design uses an exclusion set, rather than requiring the shape's labels to *be* the alert's whole
button set, because the matching site can actually compute an exclusion. The in-tree paths are the
only ones a `savePassword` rule ever reaches. They match over every identifier-less labelled button
in the poll's whole tree, not over one alert's buttons.

`_dismiss_from_tree` builds its candidate list that way. `shows_app_ui`'s docstring records that a
whole application can legitimately have no identifiers at all. So there is no "the alert's button
set" for those paths to compare against.

An exclusion is a question about the same flat list the identifying labels are already asked about,
so it holds wherever they do. That covers the web-form and 18.6 in-app shapes. Their identifying
pairs ("Save Password", "Never for This Website") are specific enough that a coincidental match
elsewhere on screen is unlikely.

The 26.5 pair is a weaker case. We state it plainly rather than leaving a later reader to find it.
"Save" and "Not Now" are ordinary application vocabulary, the same category BE-0399 flagged "Cancel"
and "Close" as. The flat-tree match this shape shares with every other in-tree rule carries two
risks, and the exclusion closes only one of them:

- **A false match.** An application screen that happens to show identifier-less "Save" and "Not Now"
  buttons, with no save sheet up, satisfies the pair and gets tapped. The exclusion set does nothing
  for this case, since it only rules out the credit-card sheet.
- **A false non-match.** `match_alert_rule` requires each identifying label present *exactly once*
  (`bajutsu/orchestrator/types.py:342`). A second identifier-less "Save" anywhere else in the same
  tree, such as behind the sheet on the screen it covers, pushes the count to two, and the shape
  stops matching at all. That reinstates the very timeout this proposal exists to fix, on that one
  iOS version.

Neither risk is new to this proposal. Single-label matching against the whole tree already carries
the same two-sided risk for any word a screen might repeat. That matching (`labels: ["Not Now"]`)
was in production before this proposal. A two-label pair narrows that risk rather than widening it.

What is new is relying on a pair this ordinary for a save/deny decision an author cannot see coming.
This proposal has no recourse for that within `systemAlertHandling`. `savePassword` is declared as a
rule only, and `handleSystemAlert` rejects it. The in-tree path is the sole mechanism that ever
reaches this alert. There is no second declaration route to fall back on, the way there is for a
SpringBoard prompt.

Both `_dismiss_from_tree` call sites filter to identifier-less buttons, so one lever is left: the
application's own accessibility markup. A colliding "Save" button that carries an identifier stops
matching the flat list this mechanism scans. The cost is a change outside a scenario author's own
control when the application under test is not theirs to edit.

Given that, we accept the risk as bounded and known, not solved. It is no worse than the
single-label matching already shipping. It is narrower than that matching in the cases the pair does
disambiguate. It is confined to one iOS version's one shape.

### Unit 4 — migrate the demos and add the regression scenario

Both save-password demos answer the save prompt with a rule instead of `labels: ["Not Now"]`.

- `save_password_browser.yaml` already declares `rules: [{ prompt: notifications, choice: deny }]`,
  which its `expect` depends on. The `savePassword` entry joins that list rather than replacing it.
  The demo also drops its `wait: { until: { gone: { label: "Not Now" … } } }`, which exists only to
  give the guard a step to run inside.
- `save_password_native.yaml` declares no rules today and gains the single-entry list. It keeps its
  `wait: { for: { id: signin.value, value: "signedIn" } }`.

That wait in `save_password_native.yaml` serves two purposes. It synchronizes an asynchronous
sign-in, and it is the window the in-tree dismissal runs in. Its own comment measures the
save-password alert landing before that condition on iOS 18.6 and after it on 26.3, 26.4, and 26.5.
The comment also explains why a `gone` wait on the alert's own button could not serve instead.

Once Unit 1 lands, that wait stays the scenario's only guarded step, since `handleSystemAlert` never
appears in it. `expect` is evaluated once. Dropping the wait would leave the scenario racing the
submission, on top of losing the alert's clearing window.

A third scenario is added for the case this proposal is written from. It places a
`handleSystemAlert` step for the notification prompt at a point where the save-password alert holds
the screen. No `wait` step covers the interruption. The scenario joins the `systemalert`-tagged
scenarios on the `ios-e2e` lane, beside the two it complements.

### Unit 5 — documentation

`docs/scenarios.md` and its `docs/ja/` mirror change in three ways:

- They lose the `labels` key and its layering row.
- They gain `savePassword` and the rule-only restriction on it.
- They record that the `handleSystemAlert` step now answers a declared interruption while it waits.

Both pages' account of the interruption monitor is rewritten too. The sentence "a prompt the policy
names no button on is left to XCUITest[’s] own default handler" stops being the whole account. That
behavior still happens, unchanged. The step or `expect` that met it now fails, naming the buttons
the alert offered.

`docs/architecture.md` and its mirror also record the wait's move from the driver to the
orchestrator. That page describes where each layer's waits live.

### Out of scope

This proposal changes no code path of the artificial-intelligence (AI) vision fallback, because
`run` no longer has one.
[BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md)
removed it in [#1843](https://github.com/bajutsu-e2e/bajutsu/pull/1843), already merged. Every path
through `AlertGuardConfig` is deterministic now. `run` rejects a layer that still supplies
`visionInstruction` outright (`_reject_vision_instruction`) rather than reading it. So `labels`
leaving the schema removes no input that the fallback still consumed.

BE-0402 also gave the `"unhandled"` value that `probe_native` returns a use this proposal builds on
rather than duplicates. That use is `blocked_note`, appended to a blocked step's or `wait`'s own
failure reason so it names the buttons it saw, rather than reading as a bare "element not found."

Unit 2b applies the same idea to the surface BE-0402 could not reach: an *interrupting* alert, which
XCUITest resolves before synthesizing the interaction it interrupted. The earlier case is an alert
merely *blocking* a step from completing.

A blocking alert that nothing clears already fails on its own condition-wait timeout, with
`blocked_note` naming what stopped it. An interrupting alert let XCUITest's own resolution complete,
and the interaction proceeded as if nothing had happened. There was no failure and no note at all.
Unit 2b closes that gap to bring the two surfaces to the same standard.

## Alternatives considered

| Option | Outline | Why not adopted |
|---|---|---|
| Pass a poll callback into the driver | Give `handle_system_alert` an `on_poll` parameter and let the driver's existing loop call back into the orchestrator's guard each iteration | The loop keeps its place, but the change lands on the `Driver` interface, so every backend, the fake driver, and the conformance suite move with it. The wait is orchestration, not actuation, and the orchestrator already owns every other condition wait |
| Return application-owned alerts from the SpringBoard query | Extend `querySystemAlertButtons` to read `app.alerts` alongside `springboard.alerts` | Would let a `handleSystemAlert` step name `savePassword` directly, which Unit 3 otherwise has to forbid. It erases the boundary BE-0399 measured and relies on, and the reactive guard loses the `probed_absent` signal that licenses its in-tree tap — the signal that orders the two prompts correctly |
| Delete the label matching instead of re-keying it | Remove `labels` and let the in-tree dismissal go with it, so the guard acts only through the native SpringBoard path | The simplest reading of "declare prompts, not buttons", and wrong: the in-tree dismissal is the only path that can clear an application-owned alert, so removing it would make `savePassword` undeclarable in practice and leave the motivating timeout unfixed |
| Keep `DEFAULT_DISMISSIVE_LABELS` as a conservative guess | Leave the interruption monitor answering an alert offering one of its words, rather than reporting and failing on every unmatched one | The first draft of this proposal did exactly this, on the mistaken belief that declining was itself unsafe (it is not — BE-0399, `docs/architecture.md`, `docs/scenarios.md`, and `RunnerUITest.swift`'s own comment all record declining as the safe fallback). Once decline is known to be safe, guessing an answer for an undeclared alert is the same silent auto-decision this proposal removes from the native probe and the in-tree dismissal, kept at the one surface that could not resist it either way |
| Attach unmatched interruptions to failures only, never on their own | Report the alert's buttons alongside a step's own failure reason, but never turn a step that otherwise passed into a failure by itself | Leaves an unintended grant that happened to not break anything downstream without consequence, which is the exact appearance of success this whole proposal argues a scenario should not get to keep. An interrupting alert no rule named is evidence a scenario's assumptions were wrong regardless of what the step checked afterward |
| Match a shape on the alert's exact button set | Separate the 26.5 in-app save sheet from the credit-card update sheet by requiring the shape's labels to equal the alert's whole button set, instead of excluding a label | Reads as the cleanest disambiguation and is not computable where it would be needed. A `savePassword` rule is only ever matched by the in-tree paths, which see every identifier-less labelled button in the poll's whole tree rather than one alert's buttons, so a set equality would fail against any application that carries another such button anywhere on screen |
| Document the workaround | Record in `docs/scenarios.md` that a `wait` step belongs before `handleSystemAlert` where an interruption is possible | Costs no implementation, and remains available if this proposal is deferred. It asks an author to predict the operating system's timing, and it cannot be written when that timing is unknown, which is the case the showcase scenarios' comments already describe |
| Add a step-local declaration | Give `handleSystemAlert` its own field naming the interruptions to clear while it waits | Puts the author's intent directly above the step that needs it. It duplicates `systemAlertHandling` for one step kind, and needs its own schema, locale resolution, bilingual documentation, and codegen support |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — move the `handleSystemAlert` wait into the orchestrator, driving one gate per step
      at `_POLL`, with the step's own read throttled to `_SYSTEM_ALERT_POLL_SECONDS` independent of
      the guard's `pollInterval`, and fail with a reason that names the alert that held the screen.
- [x] Unit 2a — remove `labels` and `--alert-labels`; re-key the native probe and both in-tree
      dismissals to `rules` alone; reject a scenario that still writes `labels`.
- [x] Unit 2b — remove `DEFAULT_DISMISSIVE_LABELS` and the Swift-side candidate matching it fed; add
      `governs` to `InterruptionPolicy`/`set_interruption_policy`/`InterruptionPolicyRequest` so a
      scenario whose rules are all in-tree-only still governs; record the interruption monitor's
      declined alerts, except a non-governing policy's own decline and a matched button lost to a
      benign race; extend the drain endpoint and the `Driver.drain_interruptions` protocol to carry
      them; add the second `expect`-phase drain the alert-guard retry is currently missing; and fail
      the step or `expect` that met one.
- [x] Unit 3 — key a prompt's language entry to a list of shapes with an optional exclusion-label
      set, add `savePassword` with its three shapes and the per-prompt surface record, and reject it
      in a `handleSystemAlert` step.
- [x] Unit 4 — migrate both save-password demos to `rules`, drop the browser demo's workaround wait,
      and add the regression scenario to the `make -C demos/showcase e2e-savepassword` target —
      not the `ios-e2e` lane the unit's design text names, since neither save-password demo runs
      on a CI lane.
- [x] Unit 5 — update `docs/scenarios.md`, `docs/architecture.md`, and both `docs/ja/` mirrors,
      including the interruption monitor's now-consequential decline.

Log:

- [#1871](https://github.com/bajutsu-e2e/bajutsu/pull/1871) delivers Unit 1.
  - `Driver.handle_system_alert` stops waiting. The XCUITest implementation resolves the step's
    selector against a single `/systemAlert/query` read. `timeout` stays on the signature (every
    caller now passes zero), so no other backend moves.
  - The wait becomes `wait_for_system_alert` in `bajutsu/common/orchestrator/waits.py`. It polls to
    the step's own deadline at `_POLL`. It reads the step's target on its own 0.2s throttle. It
    hands each poll's tree to one `_AlertGuardGate`.
  - `_run_step_body` runs it for `kind == "handle_system_alert"` with the scenario's guard,
    alongside `wait`. The action-handler registry keeps its own entry, without a guard, for
    `record`'s replay.
  - `probe_native` gains a `"reserved"` answer, so the guard declines an alert that the waiting
    step's selector names. That stops the two from deciding the same prompt in opposite directions.
  - The timeout now distinguishes no alert at all from one whose buttons the selector did not match.
    It carries the guard's own note for a prompt nothing could clear.
  - `docs/scenarios.md` and `docs/architecture.md` record the move, with both `docs/ja/` mirrors.
    The rest of Unit 5 waits on the later units.
  - Paths throughout are the post-reorg `bajutsu/common/…` ones, not the `bajutsu/…` ones this
    proposal was written against.
  - One further deviation from the unit's own text: the end-of-step alert guard now skips a failed
    `handleSystemAlert` step outright, rather than staying "unchanged". `wait_for_system_alert`
    already drives that same guard, reserved against the step's own selector, for the step's whole
    timeout. A second, unreserved probe added no coverage. It could also tap the step's own alert
    through the guard's looser fallback policy. That would both decide the prompt on the step's
    behalf and overwrite the step's specific failure reason with the generic one that a doomed retry
    against the now-cleared screen produces.
- [#1894](https://github.com/bajutsu-e2e/bajutsu/pull/1894) delivers Units 2a and 3 together. Unit
  2a's in-tree re-keying has nothing to arm on until Unit 3 supplies the surface record and the one
  in-tree-capable prompt. Splitting them would have left the save-password demos unable to clear
  their own alert in between.
  - `labels`, `--alert-labels`, `alertLabels` and `pick_alert_label` are gone. `probe_native`
    matches `rules` alone, and both in-tree dismissals arm on the new `AlertGuardConfig.tree_rules`.
  - `_LABELS` maps a prompt and language to a list of shapes with an optional exclusion set.
    `_SURFACES` records which of the three answer paths each prompt reaches. `savePassword`'s labels
    are transcribed from `WebUI.framework` under both an 18.6 and a 26.5 runtime.
  - `handleSystemAlert` rejects a prompt whose `step` surface is false. `push_interruption_policy`
    drops a rule that surface can never meet. It also refuses a rule carrying an exclusion that its
    subset match would silently discard.
  - Four demos migrate to `rules`, which removing `labels` forced rather than Unit 4 scheduling it.
  - Three deviations from the units' own text:
    - `_warn_target_rules_reach` loses its `inner_layers` parameter. No flag can carry a rule any
      more, and the scenario is the only inner layer left.
    - `push_interruption_policy` now pushes the built-in dismissive candidates unconditionally. This
      widens what a scenario that used to narrow them with `labels` sends to the runner, until Unit
      2b removes that list.
    - At the author's request and outside this item's design, the guard's two one-shot dismissal
      sites let the uncovered screen settle before the retry. The settle is a bounded, best-effort
      condition wait. A step no longer fails against a tree still animating the sheet away.
- [#1903](https://github.com/bajutsu-e2e/bajutsu/pull/1903) delivers Unit 2b, plus the remaining
  piece of Unit 5.
  - **Python side.** `DEFAULT_DISMISSIVE_LABELS` is removed from
    `bajutsu/common/orchestrator/types.py`. `push_interruption_policy` sends `governs` in place of
    the removed candidate list. `governs` is true for any scenario whose guard is on, independent of
    whether a rule survived the in-tree-only drop. `Driver.set_interruption_policy` takes `governs`
    instead of `candidates`. `Driver.drain_interruptions` returns a `DrainedInterruptions(tapped,
    declined)` pair, and the orchestrator wrapper turns `declined` into `UndeclaredInterruption`
    records rather than `AlertEvent`s.
  - **Drain sites.** Every drain site fails unconditionally once `declined` is non-empty:
    - the step-outcome drain;
    - the `expect` phase's own drain, which now covers the guard's probe whether or not it clears
      anything (the review pass found it covered only the branch that clears);
    - the second `expect`-phase drain after the alert-guard retry, which a standing comment already
      flagged as missing;
    - the one immediate return `_handle_action` takes for an uncovered `handleSystemAlert` locale
      (its own comment already named the same stranding risk for actuations).
  - Every failure this produces appends `undeclared_interruption_note` to whatever reason the step
    or `expect` already carried, rather than replacing it. The note names every drained record, not
    just the first.
  - **Swift side.** `InterruptionPolicy` drops `candidates` and `isEmpty` for `governs`.
    `InterruptionPolicyStore` keeps a second drained list that the monitor appends to before
    declining. The append is skipped when every label in the alert's button set is empty, which is
    the signature of the same alert-closed-itself race that `button.exists` already guards below.
    `openapi.yaml`, the generated `APIHandler`, and the legacy `Router` all carry the new
    `governs`/`unmatched` wire shape. The legacy path now rejects a missing or mistyped `governs`
    outright rather than defaulting it to `false`.
  - **Third review round.** It found the `if`/`forEach`/`web` step handlers to be a third instance
    of the drain-skipping class. Each queries the driver before any nested step runs, the same way
    `_handle_action`'s own immediate return did. All four now share one `_drain_step_interruptions`
    helper rather than four copies of the check. The round also refined the empty-label race guard
    to check every label in the alert's button set, rather than the list's own emptiness alone
    (`buttons.count` can be non-zero while each individual `.label` has already resolved to `""`).
  - **A design gap the first two rounds could not find by re-reading the diff alone.** A
    `handleSystemAlert` step's own prompt is not necessarily among the scenario's
    `systemAlertHandling.rules`, because the step form exists for the case where an author chooses
    not to declare it there. An earlier action's own interruption could meet that same prompt first,
    find no rule, and fail an unrelated step for an alert this one was about to answer.
    `_reserve_declared_alert` closes the gap. While a `prompt`/`choice`-form step's own wait runs,
    the orchestrator pushes one more rule for that step's target alongside the scenario's own. It
    restores the rules once the step returns, fails, or the wait raises. This covers the case the
    item's own motivating scenario describes. A `sel`-form step has no identifying label set to
    reserve, so it keeps today's behavior.
  - **Verification.** A self-reviewed diff (BE-0347's two-role procedure, three rounds, the review's
    own cap) found and fixed every finding above. `swift build` and
    `swift test --filter BajutsuRunnerTests` (172 tests, including new direct coverage of
    `InterruptionPolicy.label(for:)`) both pass. The full Python suite (7,177 tests) is green.
  - **Documentation.** `docs/architecture.md` and `docs/scenarios.md` (with both `docs/ja/` mirrors)
    drop the built-in dismissive-candidate list from their account of the interruption monitor. They
    record that a governing policy's decline now fails the step or `expect` that met it. They also
    record the reservation. That was the last piece of Unit 5 still outstanding, so the unit is
    complete too.
- [#1908](https://github.com/bajutsu-e2e/bajutsu/pull/1908) delivers Unit 4, the last unit.
  - It drops `save_password_browser.yaml`'s `wait: { until: { gone: { label: "Not Now" } } }`. That
    workaround wait existed only to give the reactive guard a step to run inside. The following
    `wait: { for: { id: Close } }` gives the guard the same window. It stays false for as long as
    the alert is modal over the browser.
  - It adds `save_password_interrupts_step.yaml`, the regression scenario this item was written
    from. The scenario places a `handleSystemAlert` step for the notification prompt with no `wait`
    step covering the interruption. The save-password alert holds the screen alone.
  - One deviation from the unit's own text: the scenario joins `make -C demos/showcase
    e2e-savepassword` rather than the `ios-e2e` CI lane. Neither save-password demo was ever on that
    lane. The lane selects scenarios by explicit path and serves no browser fixture, so the item's
    text describing the lane had gone stale by the time this unit landed. That target now runs both
    save-password scenarios in sequence, each behind its own device erase.
  - It also filled a pre-existing gap that the new scenario surfaced in
    `tests/test_showcase_fixtures.py`'s exclusion-tag guard, which had never covered `browser.yaml`,
    `tabs.yaml`, or either save-password demo.
  - Self-review (BE-0347's two-role procedure) found and fixed a timing margin too tight against the
    pre-step page load and typing. It also fixed three stale comments. The scenarios were
    re-verified on a real iOS Simulator afterward, with both save-password scenarios passing against
    a freshly erased device. `make check` (7,214 tests) is green, closing the item.

## References

- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md):
  the reactive guard's mid-wait intervention, and the first record of iOS's save-password prompt
  stalling a wait.
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md):
  the deterministic native path the guard prefers, and the `system_alert_labels` presence query.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md): the
  `handleSystemAlert` step, and the deferral of "cover every alert, not only a permission prompt"
  that this proposal takes up.
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md):
  the prompt/choice label table, and the standard that its values are transcribed rather than
  guessed.
- [BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md):
  the previous prompt added to that table, and the precedent this proposal follows.
- [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md):
  `rules`, the per-prompt declaration that becomes the only declaration.
- [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md):
  where each prompt lives, the ordering between them, and the gate on the in-tree dismissal.
- [BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation.md):
  the consolidation that gave each answer path its own key and introduced `labels`, and the
  precedent for removing a key without an alias.
- [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md):
  the merged item that removed `run`'s AI-vision fallback and added `blocked_note`, the
  blocking-surface precedent Unit 2b brings the interrupting surface to parity with.
