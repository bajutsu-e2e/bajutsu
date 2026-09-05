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

Bajutsu answers an operating-system prompt that the application's own accessibility tree cannot see
over two paths. A [scenario](../../docs/glossary.md#scenario-authoring) places a `handleSystemAlert`
step at the one point where it expects a prompt, and that step taps a named button. Separately, a
reactive guard configured by `systemAlertHandling` clears a prompt wherever one interrupts the run,
tapping a button the scenario's policy names.

We propose two changes to that pair. First, the `handleSystemAlert` step gains the guard it does not
have today: its wait moves out of the XCUITest driver and into the orchestrator, so a declared alert
interrupting the step is answered while the step is still waiting, rather than after it has already
failed. Second, a scenario stops naming buttons and names prompts instead. `systemAlertHandling`
drops `labels`, the ordered list of candidate button labels, and every path that matched against it
is re-keyed to the `rules` a scenario writes — including the in-tree dismissal, the one path that can
clear an alert living in the application's own process. Making the motivating case declarable means
adding iOS's save-password alert to the prompt table, which is the first entry there not owned by
SpringBoard.

One surface cannot be re-keyed the same way, and the reason is worth stating up front. XCUITest
resolves an alert that interrupts one of its interactions *before* it synthesizes that interaction,
and something always answers it: a monitor that declines — the only safe response to an alert no
rule identifies, since claiming an interruption without clearing it gets the monitor re-invoked on
every following interaction, which BE-0399 measured looping until the runner died — hands the alert
to XCUITest's own default handler, which taps the alert's *default* button and clears it, exactly
what happened before that monitor existed. Nothing in this proposal can stop that tap from landing.
What it changes is whether it happens in silence: the interruption monitor now reports the buttons
of any alert it declined, and the step or `expect` that saw one fails, naming them, rather than the
run continuing as if nothing had answered on the scenario's behalf.

Compatibility is deliberately not preserved. A scenario that still writes `labels` fails to load with
an error naming `rules` as its replacement, which is the same treatment
[BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation.md)
gave the keys it removed.

## Motivation

A `handleSystemAlert` step times out when iOS's save-password alert is on screen while the step
waits. Measured against an application that signs in and then requests notification authorization,
the step never sees the prompt it was placed for, spends its whole `timeout` polling, and fails with
`no system alert appeared within <timeout>s`. Nothing in the run's output names the alert that was
actually up, so the failure reads as a missing permission prompt rather than as an interruption.

The cause is that the step's wait and the guard that could clear the interruption live on opposite
sides of a call. `XcuitestDriver.handle_system_alert` polls `springboard.alerts` to its own deadline
inside `bajutsu/drivers/xcuitest.py`, and the reactive guard is `_AlertGuardGate` in
`bajutsu/orchestrator/waits.py`, an orchestrator-side object driven one poll at a time by the
`wait` step. No thread runs the guard in the background, and `_run_step_body` in
`bajutsu/orchestrator/loop.py` passes `alert_guard` through for `kind == "wait"` alone — its own
docstring records that other step kinds ignore it. While the driver's loop is running, therefore, no
guard can intervene. The end-of-step guard does fire on any failed step, `handleSystemAlert`
included, but only once the step has already spent its full timeout and failed.

The save-password alert in particular is invisible to the query the step polls.
[BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md)
measured where each prompt lives: the notification authorization request is a SpringBoard alert in
another process, whereas iOS raises the save-password alert into the *application's own* process,
where it reaches the application's own tree as identifier-less labelled buttons — `app.alerts` for a
web form, and an `app.sheets` sheet for the application's own fields. The runner's
`querySystemAlertButtons` reads `springboard.alerts.buttons` and nothing else, so the save-password
alert reads as no alert at all. The step's poll therefore finds an empty button list for as long as
the alert holds the screen, and — because the alert is modal over the application — the permission
request the step was placed for is never raised either.

Scenario authors work around the gap today by placing a `wait` step, the one step kind the guard is
wired into. `demos/showcase/scenarios/save_password_browser.yaml` carries a
`wait: { until: { gone: { label: "Not Now" … } } }` placed for no other purpose, and its comment
explains which wait form stays correct whichever way the operating system's timing falls. The
workaround demands that an author predict when iOS will interrupt, and it cannot be written at all
when the interruption's timing is genuinely unknown.

The second half of the problem is what a scenario is allowed to declare. `systemAlertHandling` takes
`rules`, which names a prompt and a choice, and `labels`, an ordered list of candidate button labels
consulted for whatever no rule identifies. A label names a button, not a prompt, so `labels` lets the
guard tap a button on an alert no scenario ever described. BE-0399 recorded the danger directly:
"Cancel" and "Close" are ordinary application vocabulary that a real screen can legitimately show, so
it gated the in-tree dismissal on a scenario having declared `labels` of its own and kept that
dismissal off the built-in defaults. The gate treats a scenario's `labels` as a proxy for author
intent, but a proxy is all it is: the declaration still says which button text to accept, never which
alert is expected.

Naming the prompt instead is not merely tidier, because the in-tree dismissal is where a declaration
does the most work. `AlertGuardConfig.dismiss_from_tree_once` and `_AlertGuardGate._dismiss_from_tree`
both match through `pick_alert_label(self.labels, …)` over the identifier-less labelled buttons in
the poll's own tree, and they are the only mechanism in the codebase that can clear an alert
`springboard.alerts` cannot see. Re-keying them to `rules` is what lets a scenario say *which* alert
it expects the guard to clear, rather than which words it is willing to see tapped.

One answer path cannot simply leave an unmatched alert alone, and today it does not fail on one
either — it grants it. XCUITest resolves an interrupting alert before it synthesizes the interaction
it interrupted, and the runner's interruption monitor answers that resolution one way or another:
matching a rule and tapping it, or declining and handing the alert to XCUITest's own default
handler, which taps whichever button that alert calls its default — the same silent grant this
document opens with, for any alert `rules` does not name. `bajutsu/orchestrator/types.py`'s
`DEFAULT_DISMISSIVE_LABELS`, pushed as the monitor's fallback candidates today, narrows how often an
alert reaches that grant, by answering conservatively for whatever alert happens to offer one of its
words — but it does not close the gap. An alert offering none of them is declined exactly as before, and
Unit 2 traces exactly why that decline is unavoidable and, since BE-0399, deliberately safe.

A later reader can tell whether this proposal arrived by running a showcase scenario that places a
`handleSystemAlert` step for the notification prompt while the save-password alert holds the screen.
That scenario fails today with the timeout quoted above, and passes afterwards with no `wait` step
placed to cover the interruption. The migrated demos are the second observable difference: both answer the
save prompt with a `savePassword` rule in place of `labels: ["Not Now"]`, and
`save_password_browser.yaml` drops the `gone` wait it carries as a workaround. The third is a
scenario that runs an ordinary `tap` while an alert no `rules` entry names interrupts it: today that
step passes (or fails for an unrelated reason the alert's own default answer happened to cause), and
after this proposal it fails by name, with the alert's buttons in the report.

## Detailed design

### Unit 1 — move the step's wait into the orchestrator

`Driver.handle_system_alert` stops waiting. It keeps its signature, and callers pass a timeout of
zero for a query-and-tap against an alert already known to be up, which is what
`AlertGuardConfig.probe_native` already does through `_NATIVE_TAP_TIMEOUT`. The XCUITest
implementation drops the polling loop at `bajutsu/drivers/xcuitest.py:1105-1114` and resolves the
selector against a single `/systemAlert/query` read. No Swift change is needed, and no other
backend is affected: `capability_preflight` already rejects the step on a backend that does not
advertise `HANDLE_SYSTEM_ALERT`, and the driver keeps raising `UnsupportedAction` as the mid-run
backstop.

The wait itself moves to the step's handler, `_do_handle_system_alert` in
`bajutsu/orchestrator/actions/handlers/gestures.py`, which gains the guard as a parameter. The
handler builds one `_AlertGuardGate` for the step and polls to the step's own deadline, which is the
same shape `_wait` already uses: `_wait` constructs the gate once and calls `gate.observe(elements)`
on each poll. Each poll then does two things, in this order:

1. Read `driver.system_alert_labels()`. When the step's own selector resolves against those labels,
   tap it through `driver.handle_system_alert(sel, 0)` and return.
2. Otherwise, hand the poll's tree to `gate.observe`, and keep polling.

The tap in step 1 carries the same time-of-check race `probe_native` already handles, and handles it
the same way. An `ElementNotFound` means the alert closed itself between the query and the tap, and
an `AmbiguousSelector` means it is still up and now offers the selector's label twice. Neither is
the step's verdict: the handler keeps polling to its deadline, so a benign race costs one poll
rather than the step.

The outer loop polls at `_POLL` (0.05s), matching `_wait`'s own cadence, but that tick is a cheap
timestamp comparison, not a cross-process read on every iteration: the two reads it may trigger keep
their own, independent rates, unchanged from what each already pays today. Step 1's own
`system_alert_labels()` read is throttled to `_SYSTEM_ALERT_POLL_SECONDS` (0.2s) internally, exactly
the cadence the driver's own polling loop paid before this unit moved it — the step's response to
its own target prompt is no slower than it is today. `gate.observe()`'s cross-process SpringBoard
probe keeps its existing, separate throttle: `_observe_native` already rate-limits itself to
`guard.poll_interval` regardless of how often `observe()` is called (`_last_native`, unchanged by
this proposal), which is the same load BE-0315 already accepts for every `wait` step a guard covers.
Coupling the two to one shared cadence — which an earlier draft of this unit did — was the error:
it would have forced `save_password_browser.yaml`'s deliberately wide `pollInterval: 5` (set so both
stacked prompts stay up across a probe) onto the step's own responsiveness too, leaving a `tap`-sized
window inside a 5-second gap for the Unit 4 regression scenario's `handleSystemAlert` step to notice
its own target in. Decoupling the two rates removes that tension: a scenario that needs a wide
`pollInterval` for the guard's SpringBoard probe pays nothing for it in how fast the step notices its
own prompt.

Reusing the gate rather than lifting `_dismiss_from_tree` out of it is what makes the cross-poll
state work. The re-tap delay, the decline give-up, and the per-label tap ceiling are all fields on
the gate, carried between polls; a free function extracted from it would have nowhere to keep them,
and re-creating the gate per poll would reset them every time. Reusing the gate also gives the step
the ordering BE-0399 established, unchanged and unrestated: `_observe_native` probes SpringBoard
first, answers a rule-identified alert there, and issues an in-tree tap only on a poll whose own
probe just reported no SpringBoard alert.

Checking the step's own selector before the gate runs is what keeps the two from competing for the
same prompt. A scenario may hold a reactive rule for the prompt the step is placed to answer, and
with the opposite choice — `rules: [{ prompt: notifications, choice: deny }]` alongside a step
naming `choice: grant`. Whichever party reads the alert first would decide it. Step 1 above settles
the common case by giving the step the first read on every poll. The residual case is a prompt that
surfaces between the step's read and the gate's own probe within a single poll, and the gate closes
it: the handler passes the step's selector to the gate, and `probe_native` declines an alert that
selector resolves against, leaving it for the step's next poll. The gate answers what the step is
not waiting for, and nothing else.

`_run_step_body` passes `alert_guard` through for `kind == "handle_system_alert"` alongside `wait`,
and its docstring is updated to name both. The end-of-step guard is unchanged; a step that now
answers interruptions while waiting simply reaches it less often.

When the deadline passes, the failure names what the step saw. The message replaces today's `no
system alert appeared within <timeout>s` with a reason that distinguishes three cases: no alert
appeared at all, an alert appeared whose buttons the selector did not match, or an undeclared alert
held the screen for the wait. The third case carries the buttons that were on screen, so a reader
of the report can add the rule the scenario was missing.

### Unit 2a — declare an alert by its prompt, and re-key every path that matched a label

`labels` is removed from `SystemAlertHandling` in `bajutsu/scenario/models/scenario.py`, and the
`--alert-labels` option is removed from `run`, so `_flag_alert_policy` in
`bajutsu/cli/commands/run.py` keeps only `--alert-poll-interval` — BE-0402 already retired
`--alert-vision-instruction` from `run` with the fallback it steered. No
flag replaces `rules`: an entry pairs a prompt with a choice, which BE-0401 already recorded as the
reason no flag carries it legibly, so a per-prompt declaration stays a scenario-file and
target-config declaration. `AlertGuardConfig` loses its `labels` field, and the layering table
BE-0401 established loses one row — `rules` stays a list key concatenated innermost layer first,
`pollInterval` stays the sole scalar resolved by the innermost layer that supplies
one, and the on/off boolean is unchanged.

Three call sites matched against `labels`, and each is re-keyed rather than deleted:

| Call site | Today | After |
|---|---|---|
| `AlertGuardConfig.probe_native` | `match_alert_rule(self.rules, …)` then `pick_alert_label(self.labels or DEFAULT_DISMISSIVE_LABELS, …)` | `match_alert_rule(self.rules, …)` alone; no rule identified means `"unhandled"`, as today |
| `AlertGuardConfig.dismiss_from_tree_once` | armed by `self.labels` being non-empty (`if not self.labels: return None`), matches `pick_alert_label(self.labels, …)` | armed by holding at least one in-tree-capable rule, matches `match_alert_rule(…)` over those rules |
| `_AlertGuardGate._dismiss_from_tree` | armed by `if self.guard.labels and probed_absent` | armed by at least one in-tree-capable rule *and* `probed_absent`, matches `match_alert_rule(…)` over those rules |

The two in-tree rows are the load-bearing ones. They match over the identifier-less labelled buttons
in the poll's own tree, and they are the only mechanism that clears an alert `springboard.alerts`
cannot see — the save-password alert included. Deleting their matching outright, rather than
re-keying it, would leave `rules: [{ prompt: savePassword, choice: deny }]` with no path that can
act on it. Everything else about those two paths is untouched: the uniqueness pre-check before the
tap, the re-tap delay, the decline give-up, the per-label tap ceiling, and BE-0399's rule that an
in-tree tap is issued only on a poll whose own probe reported no SpringBoard alert. What changes is
only the question they ask of the tree — which declared alert is showing, instead of which
acceptable word is on a button. Those three are `pick_alert_label`'s only Python callers, so the function is deleted with `labels`.

"In-tree-capable" in the table above is not a new concept but the per-prompt surface record Unit 3
introduces, read from the other side. Arming the in-tree paths on *any* rule would widen them past
what an author asked for: a scenario declaring `notifications` alone would arm an in-tree match for
a prompt that only ever appears in SpringBoard, and an application screen happening to show
identifier-less "Allow" and "Don't Allow" buttons would be tapped. Only a rule whose prompt is
recorded as in-tree-capable arms them, which is what keeps the widening from happening.

The same record decides what `push_interruption_policy` sends. A rule for an in-tree-only prompt is
dropped from the pushed rule list, because the interruption monitor exists for an alert in another
process that interrupts an XCUITest interaction, and an alert living in the application's own
process never reaches it. Dropping it is not merely tidy: `InterruptionPolicy.label(for:)` matches a
rule by subset, so pushing `savePassword`'s in-app shape would re-open on the Swift side exactly the
collision Unit 3 closes on the Python side. With those rules never pushed, no rule the monitor sees
carries an exclusion set, and neither of the two changes below has to account for one. That holds by
construction rather than by coincidence: the push refuses a rule that carries an exclusion set and is
not in-tree-only, so a later SpringBoard-reachable shape needing an exclusion fails loudly instead of
reaching the monitor with its exclusion silently dropped — which is the subset-match collision, one
table entry later.

Besides `push_interruption_policy`'s candidate fallback, now removed, one more place reads `labels`,
and it is not a matching site: `_warn_target_rules_reach` in `bajutsu/cli/commands/run.py` reads
`any(layer.labels for layer in inner_layers)` to decide whether a scenario answers for itself. The
predicate is re-keyed to `any(layer.rules for layer in inner_layers)`. There is no vision-fallback
instruction to re-key alongside it: BE-0402 already deleted `_vision_instruction` with `run`'s
fallback, so a scenario's `labels` has not steered anything there since that landed.

`_resolve_rules` raises `UncoveredSystemAlertLocale` naming `labels` as the remedy for an uncovered
language. The message is rewritten to name the remaining two remedies: add the language to
`bajutsu/scenario/system_alerts.py`, or pin a locale the table covers.

A scenario that still writes `labels` fails to load with an error naming `rules`. BE-0401 removed
`enabled` and two deprecated spellings the same way, and stated plainly that compatibility was not
preserved; this proposal follows that precedent rather than carrying an alias.

### Unit 2b — report the interruption monitor's declined alerts

`DEFAULT_DISMISSIVE_LABELS` is removed rather than kept, and the Swift-side matching it fed is
removed with it: `InterruptionPolicy.candidates` in
`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift` is deleted, `label(for:)` matches `rules`
alone, and `InterruptionPolicyRequest` in `openapi.yaml` drops the matching field. The reason is
that declining is already documented as the *safe* answer at this surface, not merely the least bad
one. `docs/architecture.md:708-709` records that a prompt the policy names no button on "is left to
XCUITest's own default handler, unchanged (BE-0399)," and `docs/scenarios.md:168-169` that it "is
left to XCUITest, which is what happened before this existed." `RunnerUITest.swift`'s own comment
calls declining "the only safe fallback… what happened before this monitor existed and does clear
it" — the loop that can take a resident runner down comes from a monitor that *claims* an
interruption without clearing it, never from one that hands it back. Nothing, then, requires the
monitor to guess an answer for an alert `rules` does not name; the built-in candidates only narrowed
how often an alert reached that unrecorded grant, never closed it.

What replaces the guess is a report — but "the guard actually governs" is not the same fact as "the
pushed rule list is non-empty," and today's `isEmpty` check conflates them. `InterruptionPolicy`
gains a `governs: Bool` field alongside `rules` (and no longer `candidates`), and
`Driver.set_interruption_policy` and `InterruptionPolicyRequest` swap their `candidates`
parameter/field for that same `governs` one. `push_interruption_policy` sets it
to `guard is not None`: true for any scenario whose `systemAlertHandling` is on, independent of
whether any of that scenario's rules survived the in-tree-only filter above. Without it, a scenario
whose *only* rule is `savePassword` — exactly what Unit 4 makes `save_password_native.yaml` — pushes
an empty rule list once the in-tree-only filter drops it, and an `isEmpty` check reading that as "this
scenario declared nothing" would silently reopen the grant Unit 2b exists to close for every *other*
alert that scenario never ruled on: a real declaration, filtered down to nothing this surface can act
on, is not the same as no declaration at all. `governs` is true in exactly the first case and false
only when `guard is None` — an absent guard or `systemAlertHandling: false` — which is the one case
still meant to keep today's silent, unrecorded grant, since declaring nothing is the author's own
choice.

The monitor records what it could not answer before it declines: `InterruptionPolicyStore` gains a
second drained list beside the one it already keeps for tapped labels — the button labels of each
alert `label(for:)` returned `nil` for, on a poll whose policy `governs` — and the monitor appends to
it, then returns `false` exactly as before, so the tap that follows (XCUITest's own, whichever
button that alert calls its default) is unchanged, because nothing at that point can change it. Two
related declines record nothing, deliberately. `guard policy.governs else { return false }` in
`RunnerUITest.swift` runs first and short-circuits before `label(for:)` is ever called: an absent
guard's decline keeps today's silent grant, since declaring nothing is the author's own choice, not
an omission this proposal exists to catch. And the race `probe_native` already treats as benign
elsewhere in this proposal recurs here: a label that did match, whose button then vanished before the
tap (`guard button.exists else { return false }`), declines without recording anything, because the
alert was not undeclared — its button simply lost a race with its own dismissal. `POST
/interruptionPolicy/drain`'s reply gains an `unmatched` field beside its existing `labels`, one
button list per declined-and-recorded alert, oldest first. `Driver.drain_interruptions` in
`bajutsu/drivers/base.py` changes its return type from `list[str]` to a small pair — the tapped
labels, unchanged, and the button lists of what was declined — and
`bajutsu/orchestrator/types.py`'s `drain_interruptions` wrapper turns the second half into
`UndeclaredInterruption` records rather than `AlertEvent`s, since nothing was answered on the
scenario's behalf for them to report as a dismissal.

`run_scenario` in `bajutsu/orchestrator/loop.py` reads that second list everywhere it already drains
the first, which is three points rather than two once the retry is counted. At the end of a step —
`outcome.alerts.extend(drain_interruptions(active_driver))`, reached after `outcome.ok`/`outcome.reason`
are set from the step's own body — an undeclared interruption overrides them: the step fails,
unconditionally, naming the buttons the alert offered, even one that otherwise passed. A step that
passed while an unnamed prompt was silently resolved is exactly the appearance of success this
proposal exists to stop trusting, on this surface as much as the other two. During `expect` —
`expect_alerts.extend(drain_interruptions(driver))` — the same override sets `failure` before the
assertion results are consulted. That drain runs once before the phase's own alert-guard retry, and
today nothing drains again after it: the retry's comment already records that "nothing else drains
this phase," which used to cost only a missing `AlertEvent` and would otherwise now cost a missed
failure. So the retry gains the second drain the comment describes as absent, folding its
`UndeclaredInterruption`s into the same check before `failure` is decided. Neither call site's
handling of a *matched* interruption changes: those still become `AlertEvent`s and never fail
anything on their own.

### Unit 3 — add the save-password prompt to the label table

`bajutsu/scenario/system_alerts.py` gains `savePassword` in `SystemAlertPrompt` and in `_LABELS`.
The prompt breaks three assumptions the table currently rests on, and closing each is part of this
unit.

**It is not owned by SpringBoard.** The table is a source of button *labels*, and the path that taps
them is chosen separately, by whether the SpringBoard query can see the alert, so nothing in the
lookup depends on which process owns the alert. The docstring records the distinction, so that a
later reader does not infer a SpringBoard alert from an entry's presence.

**It cannot be named by a `handleSystemAlert` step.** The step resolves its selector against
`driver.system_alert_labels()`, which reads `springboard.alerts` and nothing else, so a step naming
`prompt: savePassword` would poll an empty button list until its deadline — the very failure this
proposal exists to remove. `savePassword` is therefore declarable as a `systemAlertHandling` rule
only. `HandleSystemAlert` rejects it at parse time, with a message naming the rule form, rather than
accepting a step that could never resolve. This is the first prompt for which the declaration
surfaces diverge, so the table gains a per-prompt record of which surfaces a prompt reaches:
the `handleSystemAlert` step, the guard's native SpringBoard probe, and the guard's in-tree
dismissal. That record does three jobs — it rejects `savePassword` in a step, it decides which rules
arm the in-tree paths, and it keeps an in-tree-only rule out of the policy pushed to the interruption
monitor, both as Unit 2 describes.

**It renders three sets of buttons.** The labels below are transcribed from
`WebUI.framework/<lang>.lproj/Localizable.strings`, which lives in the Simulator runtime's cryptex
at `System/Cryptexes/OS/System/Library/PrivateFrameworks/`. Two properties of that location matter
for anyone re-checking the table under a new runtime, as BE-0320 asks: the three prompts already in the table come from
frameworks under `System/Library/PrivateFrameworks` in the runtime root, outside the cryptex, and
every `.strings` file is an Apple binary property list, so it yields its contents to `plutil` rather
than to a plain-text search.

| Alert | iOS | English | Japanese |
|---|---|---|---|
| Raised for a web form | 18.6 and 26.5 | Save Password / Never for This Website / Not Now | パスワードを保存 / このWebサイトでは保存しない / 今はしない |
| Raised for the application's own fields | 18.6 | Save Password / Not Now | パスワードを保存 / 今はしない |
| Raised for the application's own fields | 26.5 | Save / Not Now | 保存 / 今はしない |

The accepting button is what moves. iOS 26.5 carries a key 18.6 does not — `"Save Password (save
login information sheet in app)"`, whose value is "Save" — so the same accepting intent reads "Save
Password" on a web form and "Save" in the application's own fields. The refusing button is "Not
Now" in every one of the three, and its Japanese is 今はしない throughout.

`_LABELS` therefore keys a prompt's language entry to a *list* of shapes rather than to one
`{grant, deny}` pair. A shape carries the labels that identify the alert, the label each choice
taps, and an optional set of labels whose presence rules the shape *out*. `notifications`,
`tracking`, and `paste` each declare a single shape with no exclusions, and every value they resolve
to is unchanged. `savePassword` declares three, in the order of the table above.

`_resolve_rules` emits one `ResolvedAlertRule` per shape, and `match_alert_rule` gains the exclusion
check: a rule matches when every identifying label is present exactly once, as today, *and* no
excluded label is present at all. The web-form shape needs no exclusion — its three buttons are not
offered together by any other alert. The 18.6 in-app shape is identified by "Save Password" and "Not
Now", a pair the web-form alert also satisfies, which is harmless because both shapes tap the same
label for the same choice.

The 26.5 in-app shape is the one that needs the exclusion. Its labels are "Save" and "Not Now", and
the same `WebUI.framework` gives a credit-card update sheet "Save", "Never for This Card", and "Not
Now", so the pair alone would answer that sheet too. The shape therefore excludes "Never for This
Card", the one label that tells the two apart.

An exclusion set, rather than requiring the shape's labels to *be* the alert's whole button set, is
what the matching site can actually compute. The in-tree paths are the only ones a `savePassword`
rule ever reaches, and they match over every identifier-less labelled button in the poll's whole
tree, not over one alert's buttons — `_dismiss_from_tree` builds its candidate list that way, and
`shows_app_ui`'s docstring records that a whole application can legitimately have no identifiers at
all. There is no "the alert's button set" for those paths to compare against. An exclusion is a
question about the same flat list the identifying labels are already asked about, so it holds
wherever they do — for the web-form and 18.6 in-app shapes, whose identifying pairs ("Save Password",
"Never for This Website") are specific enough that a coincidental match elsewhere on screen is
unlikely.

The 26.5 pair is a weaker case, and worth stating plainly rather than leaving a later reader to find
it. "Save" and "Not Now" are ordinary application vocabulary — the same category BE-0399 flagged
"Cancel" and "Close" as — and the flat-tree match this shape shares with every other in-tree rule
carries both directions of risk, not only the one the exclusion closes. An application screen that
happens to show identifier-less "Save" and "Not Now" buttons with no save sheet up satisfies the pair
and gets tapped: a false match the exclusion set does nothing for, since it only rules out the
credit-card sheet. And `match_alert_rule` requires each identifying label present *exactly once*
(`bajutsu/orchestrator/types.py:342`), so a second identifier-less "Save" anywhere else in the same
tree — behind the sheet, on the screen it covers — pushes the count to two and the shape stops
matching at all: a false non-match that reinstates the very timeout this proposal exists to fix, on
that one iOS version. Neither risk is new to this proposal; single-label matching against the whole
tree (`labels: ["Not Now"]`, in production before this proposal) already carries the same two-sided
risk for any word a screen might repeat, and a two-label pair narrows rather than widens it. What is
new is relying on a pair this ordinary for a save/deny decision an author cannot see coming, and this
proposal has no recourse for it within `systemAlertHandling`: `savePassword` is declared as a rule
only, `handleSystemAlert` rejects it, and the in-tree path is the sole mechanism that ever reaches
this alert, so there is no second declaration route to fall back on the way there is for a
SpringBoard prompt. Both `_dismiss_from_tree` call sites filter to identifier-less buttons, so the
one lever left is the application's own accessibility markup: a colliding "Save" button that carries
an identifier stops matching the flat list this mechanism scans, at the cost of a change outside a
scenario author's own control when the application under test is not theirs to edit. Given that, this
is accepted as a bounded, known risk rather than a solved one — no worse than the single-label
matching already shipping, narrower than it in the cases the pair does disambiguate, and confined to
one iOS version's one shape.

### Unit 4 — migrate the demos and add the regression scenario

Both save-password demos answer the save prompt with a rule instead of `labels: ["Not Now"]`.
`save_password_browser.yaml` already declares `rules: [{ prompt: notifications, choice: deny }]`,
which its `expect` depends on, so the `savePassword` entry joins that list rather than replacing it.
`save_password_native.yaml` declares no rules today and gains the single-entry list. `save_password_browser.yaml` additionally drops its
`wait: { until: { gone: { label: "Not Now" … } } }`, which exists only to give the guard a step to
run inside. `save_password_native.yaml` keeps its `wait: { for: { id: signin.value, value:
"signedIn" } }`: that wait both synchronizes an asynchronous sign-in and is the window the in-tree
dismissal runs in — its own comment measures the save-password alert landing before that condition
on iOS 18.6 and after it on 26.3, 26.4, and 26.5, and explains why a `gone` wait on the alert's own
button could not serve instead. It stays the scenario's only guarded step once Unit 1 lands, since
`handleSystemAlert` never appears in it, and `expect` is evaluated once, so dropping the wait would
leave the scenario racing the submission on top of losing the alert's clearing window.

A third scenario is added for the case this proposal is written from: it places a
`handleSystemAlert` step for the notification prompt at a point where the save-password alert holds
the screen, with no `wait` step covering the interruption. It joins the `systemalert`-tagged
scenarios on the `ios-e2e` lane, beside the two it complements.

### Unit 5 — documentation

`docs/scenarios.md` and its `docs/ja/` mirror lose the `labels` key and its layering row, gain
`savePassword` and the rule-only restriction on it, and record that the `handleSystemAlert` step now
answers a declared interruption while it waits. Both pages' account of the interruption monitor is
rewritten too: "a prompt the policy names no button on is left to XCUITest[’s] own default handler"
stops being the whole sentence — it still happens, unchanged, but the step or `expect` that met it
now fails, naming the buttons the alert offered. `docs/architecture.md` and its mirror also record
the wait's move from the driver to the orchestrator, since that page describes where each layer's
waits live.

### Out of scope

This proposal changes no code path of the artificial-intelligence (AI) vision fallback, because `run`
no longer has one:
[BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md)
removed it in [#1843](https://github.com/bajutsu-e2e/bajutsu/pull/1843), already merged. Every path
through `AlertGuardConfig` is deterministic now, and `run` rejects a layer that still supplies
`visionInstruction` outright (`_reject_vision_instruction`) rather than reading it, so `labels`
leaving the schema removes no input that fallback still consumed.

BE-0402 also gave the `"unhandled"` value `probe_native` returns a use this proposal builds on rather
than duplicates: `blocked_note`, appended to a blocked step's or `wait`'s own failure reason so it
names the buttons it saw rather than reading as a bare "element not found." Unit 2b is the same idea
applied to the surface BE-0402 could not reach — an *interrupting* alert, one XCUITest resolves
before synthesizing the interaction it interrupted, rather than one merely *blocking* a step from
completing. A blocking alert nothing clears already fails on its own condition-wait timeout, with
`blocked_note` naming what stopped it; an interrupting alert let XCUITest's own resolution complete
and the interaction proceed as if nothing had happened, with no failure and no note at all, which is
the gap Unit 2b closes to bring the two surfaces to the same standard.

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

- [#1871](https://github.com/bajutsu-e2e/bajutsu/pull/1871) — Unit 1. `Driver.handle_system_alert` stops waiting: the XCUITest implementation resolves
  the step's selector against a single `/systemAlert/query` read, and `timeout` stays on the
  signature (every caller now passes zero) so no other backend moves. The wait becomes
  `wait_for_system_alert` in `bajutsu/common/orchestrator/waits.py`, which polls to the step's own
  deadline at `_POLL`, reads the step's target on its own 0.2s throttle, and hands each poll's tree
  to one `_AlertGuardGate`. `_run_step_body` runs it for `kind == "handle_system_alert"` with the
  scenario's guard, alongside `wait`; the action-handler registry keeps its own entry, without a
  guard, for `record`'s replay. `probe_native` gains a `"reserved"` answer so the guard declines an
  alert the waiting step's selector names, which is what stops the two from deciding the same
  prompt in opposite directions. The timeout now distinguishes no alert at all from one whose
  buttons the selector did not match, and carries the guard's own note for a prompt nothing could
  clear. `docs/scenarios.md` and `docs/architecture.md` record the move, with both `docs/ja/`
  mirrors; the rest of Unit 5 waits on the later units. Paths throughout are the post-reorg
  `bajutsu/common/…` ones, not the `bajutsu/…` ones this proposal was written against. One further
  deviation from the unit's own text: the end-of-step alert guard now skips a failed
  `handleSystemAlert` step outright, rather than staying "unchanged". `wait_for_system_alert`
  already drives that same guard, reserved against the step's own selector, for the step's whole
  timeout — a second, unreserved probe added no coverage and could tap the step's own alert through
  the guard's looser fallback policy, both deciding the prompt on the step's behalf and overwriting
  its specific failure reason with the generic one a doomed retry against the now-cleared screen
  produces.
- [#1894](https://github.com/bajutsu-e2e/bajutsu/pull/1894) — Units 2a and 3, landed together
  because 2a's in-tree re-keying has nothing to arm on until 3 supplies the surface record and the
  one in-tree-capable prompt; splitting them would have left the save-password demos unable to clear
  their own alert in between. `labels`, `--alert-labels`, `alertLabels` and `pick_alert_label` are
  gone; `probe_native` matches `rules` alone, and both in-tree dismissals arm on the new
  `AlertGuardConfig.tree_rules`. `_LABELS` maps a prompt and language to a list of shapes with an
  optional exclusion set, and `_SURFACES` records which of the three answer paths each prompt
  reaches; `savePassword`'s labels are transcribed from `WebUI.framework` under both an 18.6 and a
  26.5 runtime. `handleSystemAlert` rejects a prompt whose `step` surface is false, and
  `push_interruption_policy` drops a rule that surface can never meet and refuses one carrying an
  exclusion its subset match would silently discard. Four demos migrate to `rules`, which removing
  `labels` forced rather than Unit 4 scheduling it. Three deviations from the units' own text:
  `_warn_target_rules_reach` loses its `inner_layers` parameter, since no flag can carry a rule any
  more and the scenario is the only inner layer left; `push_interruption_policy` now pushes the
  built-in dismissive candidates unconditionally, widening what a scenario that used to narrow them
  with `labels` sends to the runner until Unit 2b removes that list; and, at the author's request
  and outside this item's design, the guard's two one-shot dismissal sites let the uncovered screen
  settle before the retry — a bounded, best-effort condition wait, so a step no longer fails against
  a tree still animating the sheet away.
- [#1903](https://github.com/bajutsu-e2e/bajutsu/pull/1903) — Unit 2b, plus the remaining piece
  of Unit 5. Unit 2b removes `DEFAULT_DISMISSIVE_LABELS` from
  `bajutsu/common/orchestrator/types.py`, and `push_interruption_policy` sends `governs` (true for any
  scenario whose guard is on, independent of whether a rule survived the in-tree-only drop) in place
  of the removed candidate list. `Driver.set_interruption_policy` takes `governs` instead of
  `candidates`; `Driver.drain_interruptions` returns a `DrainedInterruptions(tapped, declined)` pair,
  and the orchestrator wrapper turns `declined` into `UndeclaredInterruption` records rather than
  `AlertEvent`s. Every drain site fails unconditionally once `declined` is non-empty — the step-outcome
  drain, the `expect` phase's own drain (now covering the guard's probe regardless of whether it
  clears anything, not just the branch where it does, closing a gap the review pass found), the second
  `expect`-phase drain after the alert-guard retry that a standing comment already flagged as missing,
  and the one immediate return `_handle_action` takes for an uncovered `handleSystemAlert` locale, whose own
  comment already named the same stranding risk for actuations. Every failure this produces appends
  `undeclared_interruption_note` to whatever reason the step or `expect` already carried, rather than
  replacing it, and names every drained record, not just the first. On the Swift side, `InterruptionPolicy`
  drops `candidates`/`isEmpty` for `governs`, and `InterruptionPolicyStore` keeps a second drained list
  the monitor appends to before declining — skipped when every label in the alert's button set is empty,
  the signature of the same alert-closed-itself race `button.exists` already guards below;
  `openapi.yaml`, the generated `APIHandler`, and the legacy `Router` all carry the new
  `governs`/`unmatched` wire shape, with the legacy path now rejecting a missing or mistyped `governs`
  outright rather than defaulting it to `false`. A third round found the `if`/`forEach`/`web` step
  handlers as a third instance of the drain-skipping class: each queries the driver before any nested
  step runs, the same way `_handle_action`'s own immediate return did, so all four now share one
  `_drain_step_interruptions` helper rather than four copies of the check. That round also refined the
  empty-label race guard to check every label in the alert's button set, rather than the list's own
  emptiness alone (`buttons.count` can be non-zero while each individual `.label` has already resolved to
  `""`), and raised a design gap the first two rounds could not have found by re-reading the diff
  alone: a `handleSystemAlert` step's own prompt is not necessarily among the scenario's
  `systemAlertHandling.rules` — the step form exists for the case where an author chooses not
  to declare it there — so an earlier action's own interruption could meet that same prompt first, find
  no rule, and fail an unrelated step for an alert this one was about to answer. `_reserve_declared_alert`
  closes it: while a `prompt`/`choice`-form step's own wait runs, the orchestrator pushes one
  more rule for that step's target alongside the scenario's own, restored once the step returns, fails,
  or the wait raises — covering the case the item's own motivating scenario describes, though a
  `sel`-form step (no identifying label set to reserve) keeps today's behavior. A self-reviewed diff
  (BE-0347's two-role procedure, three rounds, the review's own cap) found and fixed every finding
  above; `swift build` and `swift test --filter BajutsuRunnerTests` (172 tests, including new direct
  coverage of `InterruptionPolicy.label(for:)`) both pass, and the full Python suite (7,177 tests) is
  green. `docs/architecture.md` and `docs/scenarios.md` (with both `docs/ja/` mirrors) drop the
  built-in dismissive-candidate list from their account of the interruption monitor, record that a
  governing policy's decline now fails the step or `expect` that met it, and record the reservation —
  the last piece of Unit 5 still outstanding, so that unit is complete too.
- [#1908](https://github.com/bajutsu-e2e/bajutsu/pull/1908) — Unit 4, the last unit. Dropped
  `save_password_browser.yaml`'s `wait: { until: { gone: { label: "Not Now" } } }`, the workaround
  wait that existed only to give the reactive guard a step to run inside; the following `wait: {
  for: { id: Close } }` gives it the same window, since it stays false for as long as the alert is
  modal over the browser. Added `save_password_interrupts_step.yaml`, the regression scenario this
  item was written from: a `handleSystemAlert` step for the notification prompt with no `wait` step
  covering the interruption, while the save-password alert holds the screen alone. One deviation
  from the unit's own text: it joins `make -C demos/showcase e2e-savepassword` rather than the
  `ios-e2e` CI lane, since neither save-password demo was ever on that lane — it selects scenarios
  by explicit path and serves no browser fixture, so the item's text describing the lane had gone
  stale by the time this unit landed. That target now runs both save-password scenarios in
  sequence, each behind its own device erase. Also filled a gap the new scenario's addition
  exposed in `tests/test_showcase_fixtures.py`'s exclusion-tag guard, which was missing `browser.yaml`,
  `tabs.yaml`, and both save-password demos already. A self-reviewed diff (BE-0347's two-role procedure) found
  and fixed a timing margin too tight against the pre-step page load and typing, and three stale
  comments; re-verified on a real iOS Simulator afterward, with both save-password scenarios passing
  against a freshly erased device. `make check` (7,214 tests) is green, closing the item.

## References

- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)
  — the reactive guard's mid-wait intervention, and the first record of iOS's save-password prompt
  stalling a wait.
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  — the deterministic native path the guard prefers, and the `system_alert_labels` presence query.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) — the
  `handleSystemAlert` step, and the deferral of "cover every alert, not only a permission prompt"
  that this proposal takes up.
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
  — the prompt/choice label table, and the standard that its values are transcribed rather than
  guessed.
- [BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md)
  — the previous prompt added to that table, and the precedent this proposal follows.
- [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md) —
  `rules`, the per-prompt declaration that becomes the only declaration.
- [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md)
  — where each prompt lives, the ordering between them, and the gate on the in-tree dismissal.
- [BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation.md)
  — the consolidation that gave each answer path its own key and introduced `labels`, and the
  precedent for removing a key without an alias.
- [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md)
  — the merged item that removed `run`'s AI-vision fallback and added `blocked_note`, the
  blocking-surface precedent Unit 2b brings the interrupting surface to parity with.
