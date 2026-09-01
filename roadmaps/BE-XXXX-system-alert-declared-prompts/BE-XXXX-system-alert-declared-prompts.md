**English** · [日本語](BE-XXXX-system-alert-declared-prompts-ja.md)

# BE-XXXX — Declare system alerts by prompt alone, and answer them during a handleSystemAlert step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-system-alert-declared-prompts.md) |
| Author | [@akiramatsuda](https://github.com/akiramatsuda) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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

One built-in default survives, and the reason is worth stating up front because it qualifies the
claim above. XCUITest answers an alert that interrupts one of its interactions before it synthesizes
that interaction, and a monitor that declines to answer does not leave the alert alone: XCUITest's
own default handler takes it and taps its *default* button, which grants. At that one surface the
choice is not between answering and not answering, so the ordered dismissive defaults stay as the
policy pushed to the runner's interruption monitor. Everywhere the guard itself acts, it acts only on
what a scenario declared.

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

A later reader can tell whether this proposal arrived by running a showcase scenario that places a
`handleSystemAlert` step for the notification prompt while the save-password alert holds the screen.
That scenario fails today with the timeout quoted above, and passes afterwards with no `wait` step
placed to cover the interruption. The migrated demos are the second observable difference: both answer the
save prompt with a `savePassword` rule in place of `labels: ["Not Now"]`, and
`save_password_browser.yaml` drops the `gone` wait it carries as a workaround.

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

The step polls on the guard's own `pollInterval` rather than the wait machinery's `_POLL`. Three
reads in a poll cross a process boundary — the SpringBoard query in step 1, the tree query
`gate.observe` consumes, and the gate's own SpringBoard probe — and BE-0315 already records that querying SpringBoard once per `_POLL`
would roughly double the load on the runner's single main thread. The step's driver-side loop polls
at 0.2 seconds today, so the guard's one-second default is the slower of the two; a scenario that
needs the old cadence sets `pollInterval` down.

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

### Unit 2 — declare an alert by its prompt, and re-key every path that matched a label

`labels` is removed from `SystemAlertHandling` in `bajutsu/scenario/models/scenario.py`, and the
`--alert-labels` option is removed from `run`, so `_flag_alert_policy` in
`bajutsu/cli/commands/run.py` keeps only `--alert-vision-instruction` and `--alert-poll-interval`. No
flag replaces it: a rule pairs a prompt with a choice, which BE-0401 already recorded as the reason
no flag carries `rules` legibly, so a per-prompt declaration stays a scenario-file and
target-config declaration. `AlertGuardConfig` loses its `labels` field, and the layering table
BE-0401 established loses one row — `rules` stays a list key concatenated innermost layer first,
`visionInstruction` and `pollInterval` stay scalars resolved by the innermost layer that supplies
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
acceptable word is on a button. Those three are `pick_alert_label`'s only callers, so the function
is deleted with `labels`; the Swift side keeps its own ordered-candidate matching for the
interruption monitor, which is the one surface that still needs it.

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
carries an exclusion set, and `BajutsuKit` needs no change. That holds by construction rather than
by coincidence: the push refuses a rule that carries an exclusion set and is not in-tree-only, so a
later SpringBoard-reachable shape needing an exclusion fails loudly instead of reaching the monitor
with its exclusion silently dropped — which is the subset-match collision, one table entry later.

`DEFAULT_DISMISSIVE_LABELS` survives, scoped to `set_interruption_policy`'s `candidates` alone. The
reason is that declining is not a neutral act at that surface. XCUITest resolves an alert
interrupting one of its interactions before it synthesizes the interaction, and a monitor that
declines hands the alert back to XCUITest's own default handler, which taps the alert's *default*
button — granting a permission the scenario never spoke about, with no `AlertEvent` in the report.
That silence is what BE-0399 exists to end, and `save_password_browser.yaml`'s `expect` asserts
against it by name. So an empty candidate list is not "answer nothing" but "let XCUITest grant it",
and the ordered dismissive defaults stay as the deny-first answer for an alert no rule identifies.
The guard's own two surfaces — the native probe and the in-tree dismissal — answer only declared
prompts; the interruption monitor answers everything, because it has no third option.

Besides `push_interruption_policy`'s candidate fallback, which the paragraph above already
resolves, two more places read `labels`, and neither is a matching site. `_vision_instruction` in
`bajutsu/cli/commands/run.py` derives the vision fallback's instruction from the innermost layer
that supplied labels, and `_warn_target_rules_reach` reads `layer.labels or layer.vision_instruction`
to decide whether a scenario answers for itself. `_vision_instruction` loses its label argument, and
the predicate is re-keyed to `layer.rules or layer.vision_instruction`. The vision fallback's own
behavior is unchanged by this: its docstring already records that a rules-only scenario leaves the
locator on its least-destructive default, because a rule's tap label is by construction some other
prompt's answer and must never steer the fallback. Removing `labels` makes every scenario a
rules-only scenario, which is a path that function already takes.

`_resolve_rules` raises `UncoveredSystemAlertLocale` naming `labels` as the remedy for an uncovered
language. The message is rewritten to name the remaining two remedies: add the language to
`bajutsu/scenario/system_alerts.py`, or pin a locale the table covers.

The Swift side keeps `InterruptionPolicy.candidates`, since the interruption monitor is exactly the
consumer that still needs it, and `Driver.set_interruption_policy` keeps both arguments. Nothing in
`BajutsuKit` changes in this unit.

A scenario that still writes `labels` fails to load with an error naming `rules`. BE-0401 removed
`enabled` and two deprecated spellings the same way, and stated plainly that compatibility was not
preserved; this proposal follows that precedent rather than carrying an alias.

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
wherever they do, and its failure mode is a non-match rather than a wrong tap.

### Unit 4 — migrate the demos and add the regression scenario

Both save-password demos answer the save prompt with a rule instead of `labels: ["Not Now"]`.
`save_password_browser.yaml` already declares `rules: [{ prompt: notifications, choice: deny }]`,
which its `expect` depends on, so the `savePassword` entry joins that list rather than replacing it.
`save_password_native.yaml` declares no rules today and gains the single-entry list. `save_password_browser.yaml` additionally drops its
`wait: { until: { gone: { label: "Not Now" … } } }`, which exists only to give the guard a step to
run inside. `save_password_native.yaml` keeps its `wait: { for: { id: signin.value, value:
"signedIn" } }`: that wait is the scenario's synchronization for an asynchronous sign-in, not an
alert window, and `expect` is evaluated once, so dropping it would leave the scenario racing the
submission.

A third scenario is added for the case this proposal is written from: it places a
`handleSystemAlert` step for the notification prompt at a point where the save-password alert holds
the screen, with no `wait` step covering the interruption. It joins the `systemalert`-tagged
scenarios on the `ios-e2e` lane, beside the two it complements.

### Unit 5 — documentation

`docs/scenarios.md` and its `docs/ja/` mirror lose the `labels` key and its layering row, gain
`savePassword` and the rule-only restriction on it, and record that the `handleSystemAlert` step now
answers a declared interruption while it waits. `docs/architecture.md` and its mirror record the
wait's move from the driver to the orchestrator, since that page describes where each layer's waits
live.

### Out of scope

This proposal changes no code path of the artificial-intelligence (AI) vision fallback. It does
narrow one input: with `labels` gone, no scenario can supply the label-derived hint
`_vision_instruction` renders, so every scenario becomes the rules-only case that function already
handles by leaving the locator on its least-destructive default. `visionInstruction` remains the one
way to steer it. Removing the fallback from `run` altogether is
[BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md)'s
subject, the removal is decided, and the work is under way as a separate change.

The two proposals meet at the `"unhandled"` value `probe_native` returns for an alert no rule names.
This one narrows what reaches that return, by removing the `labels` matching and the built-in
defaults from the guard's own resolution. BE-0402 removes what happens after it. Neither item
depends on the other's order, so whichever lands first is correct on its own.

## Alternatives considered

| Option | Outline | Why not adopted |
|---|---|---|
| Pass a poll callback into the driver | Give `handle_system_alert` an `on_poll` parameter and let the driver's existing loop call back into the orchestrator's guard each iteration | The loop keeps its place, but the change lands on the `Driver` interface, so every backend, the fake driver, and the conformance suite move with it. The wait is orchestration, not actuation, and the orchestrator already owns every other condition wait |
| Return application-owned alerts from the SpringBoard query | Extend `querySystemAlertButtons` to read `app.alerts` alongside `springboard.alerts` | Would let a `handleSystemAlert` step name `savePassword` directly, which Unit 3 otherwise has to forbid. It erases the boundary BE-0399 measured and relies on, and the reactive guard loses the `probed_absent` signal that licenses its in-tree tap — the signal that orders the two prompts correctly |
| Delete the label matching instead of re-keying it | Remove `labels` and let the in-tree dismissal go with it, so the guard acts only through the native SpringBoard path | The simplest reading of "declare prompts, not buttons", and wrong: the in-tree dismissal is the only path that can clear an application-owned alert, so removing it would make `savePassword` undeclarable in practice and leave the motivating timeout unfixed |
| Drop `DEFAULT_DISMISSIVE_LABELS` along with `labels` | Push an empty candidate list to the interruption monitor, so nothing undeclared is ever answered | Reads as the strictest option and is the least safe. An empty policy makes the monitor decline, and XCUITest's default handler then grants the alert with nothing in the report — the silence BE-0399 ended. "Answer nothing" is not available at that surface |
| Match a shape on the alert's exact button set | Separate the 26.5 in-app save sheet from the credit-card update sheet by requiring the shape's labels to equal the alert's whole button set, instead of excluding a label | Reads as the cleanest disambiguation and is not computable where it would be needed. A `savePassword` rule is only ever matched by the in-tree paths, which see every identifier-less labelled button in the poll's whole tree rather than one alert's buttons, so a set equality would fail against any application that carries another such button anywhere on screen |
| Document the workaround | Record in `docs/scenarios.md` that a `wait` step belongs before `handleSystemAlert` where an interruption is possible | Costs no implementation, and remains available if this proposal is deferred. It asks an author to predict the operating system's timing, and it cannot be written when that timing is unknown, which is the case the showcase scenarios' comments already describe |
| Add a step-local declaration | Give `handleSystemAlert` its own field naming the interruptions to clear while it waits | Puts the author's intent directly above the step that needs it. It duplicates `systemAlertHandling` for one step kind, and needs its own schema, locale resolution, bilingual documentation, and codegen support |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — move the `handleSystemAlert` wait into the orchestrator, driving one gate per step on
      the guard's `pollInterval`, and fail with a reason that names the alert that held the screen.
- [ ] Unit 2 — remove `labels` and `--alert-labels`, re-key the native probe and both in-tree
      dismissals to `rules`, keep `DEFAULT_DISMISSIVE_LABELS` scoped to the interruption monitor,
      and reject a scenario that still writes `labels`.
- [ ] Unit 3 — key a prompt's language entry to a list of shapes with an optional exclusion-label
      set, add `savePassword` with its three shapes and the per-prompt surface record, and reject it
      in a `handleSystemAlert` step.
- [ ] Unit 4 — migrate both save-password demos to `rules`, drop the browser demo's workaround wait,
      and add the regression scenario to the `ios-e2e` lane.
- [ ] Unit 5 — update `docs/scenarios.md`, `docs/architecture.md`, and both `docs/ja/` mirrors.

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
  — the proposal that removes the vision fallback this one leaves in place.
