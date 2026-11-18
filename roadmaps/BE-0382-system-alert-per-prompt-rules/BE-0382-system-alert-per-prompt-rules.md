**English** · [日本語](BE-0382-system-alert-per-prompt-rules-ja.md)

# BE-0382 — Let the reactive system-alert guard answer each covered prompt by its own rule

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0382](BE-0382-system-alert-per-prompt-rules.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0382") |
| Implementing PR | [#1672](https://github.com/bajutsu-e2e/bajutsu/pull/1672) |
| Topic | Scenario authoring features |
| Related | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md) |
<!-- /BE-METADATA -->

## Introduction

The `systemAlertHandling` setting of a scenario clears an operating-system prompt that the
accessibility tree of the application cannot see. Examples are the "Allow Notifications" permission
request, App Tracking Transparency (ATT), and the cross-process paste consent. The setting taps one
button of the prompt reactively, wherever the prompt interrupts a run.

Today the setting carries one button policy for the whole scenario. The policy applies unchanged to
whichever prompt interrupts the run.

This proposal adds an ordered `rules` list to the setting. Each entry names one prompt and the choice
to make on it, so a single scenario can grant the notification request and refuse tracking. Each rule
works in two steps:

- It identifies the prompt on screen from the button labels that the operating system already
  reports.
- It resolves its own label through the locale-keyed table that the proactive `handleSystemAlert`
  step already uses.

The contribution is not expressive power that the setting lacks. The ordered candidate-label list that
the setting takes today can already reach both answers. It reaches them only through an ordering that
the author must derive from the labels that two prompts happen to share. The ordering that reads
naturally gives the opposite answer, with no error. Naming the prompt makes the intent of the author
explicit in the file and makes the inverted answer unreachable.

## Motivation

The button policy of the guard is one ordered list of candidate labels, applied to whatever prompt
appears. The pieces work as follows:

- `AlertGuardConfig.probe_native` (`bajutsu/orchestrator/types.py`) reads the buttons of the alert
  through the `system_alert_labels()` read that
  [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  added over the SpringBoard query of BE-0316.
- `pick_alert_label` returns the first candidate present on the alert exactly once.
- The guard then taps that label.

There is one list and one pass. No notion of which prompt is showing exists.

That list can nonetheless express two different answers, because it is ordered and its labels are
compared exactly. Take an author who wants to grant the notification request and refuse tracking. The
author writes `instruction: ["Ask App Not to Track", "Allow"]`.

- The notification prompt offers no button named "Ask App Not to Track". The guard falls through to
  "Allow" and grants the request.
- The tracking prompt offers both buttons. The guard takes the earlier entry and refuses.

Every combination of grant and refuse across the three prompts that the label table covers is
reachable this way, because "Allow" is the one label that two of those prompts share.

The encoding is the problem, not the set of outcomes it reaches. An ordered label list is a poor way
to record which answer belongs to which prompt, for three reasons.

First, the ordering that reads naturally gives the opposite answer, and gives it silently. "Grant
notifications, refuse tracking" reads as `["Allow", "Ask App Not to Track"]`. That list grants
tracking as well. "Allow" is present on the tracking prompt, so the guard returns it and never
reaches the second entry. No step fails and no warning is printed. The run passes with the
application tracking the user, which is the answer that the scenario was written to refuse.

Second, the list never records which prompt an entry answers. A contributor reading
`["Ask App Not to Track", "Allow"]` cannot tell why the second entry exists. It accepts the
notification request. It does not accept anything that offers a button named "Allow".

The discrimination lives in the order. The order is derived knowledge, not stated intent. An edit
that adds an answer for a third prompt therefore has to re-derive the whole ordering. That derivation
also rests on today's label sets. An operating-system prompt introduced later may also have "Allow"
as its accepting button. The same list would then grant that prompt silently, because the list says
nothing about which prompt it meant.

Third, the labels are literal text in a single language. Under a Japanese locale the same intent
becomes `["アプリにトラッキングしないように要求", "許可"]`, transcribed by hand.
[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
removed this transcription trap for the proactive `handleSystemAlert` step. It lets a scenario name a
prompt and a choice instead of the text.

The guard still carries the resulting gap, and its own documentation records it. The built-in
dismissive labels are literal English. Under a non-English locale the native path matches nothing and
falls back to the AI-vision guard. A rule that names the prompt reads its labels from the same
locale-keyed table that the step uses. That closes the gap for every prompt the table covers.

## Detailed design

### The schema

```yaml
- name: onboarding — accept notifications, refuse tracking
  systemAlertHandling:
    rules:
      - prompt: notifications
        choice: grant
      - prompt: tracking
        choice: deny
    instruction: ["Not Now"]          # every alert no rule identifies
  steps:
    - tap:  { id: onboarding.start }
    - wait: { for: { id: home.title }, timeout: 10 }
```

`rules` is an ordered list. Each entry carries a `prompt` and a `choice`. These are the two
vocabularies that the proactive `handleSystemAlert` step already takes:

- `prompt` is `notifications`, `tracking`, or `paste`.
- `choice` is `grant` or `deny`.

Both fields are required on an entry. A rule identifies its prompt by name. An author who wants to
name a literal button keeps `instruction` for that.

`rules` and `instruction` compose and do not exclude each other. This answers the question that an
author brings to this feature. The guard consults the rules first. An alert that no rule identifies
falls through to `instruction`. If the scenario supplies no `instruction`, the alert falls through to
the built-in dismissive labels. A scenario thus answers by name the prompts it knows about and still
dismisses whatever else appears.

Two entries that name the same `prompt` within one list fail at parse time. Silently taking the first
would hide an authoring mistake. The schema refuses it for the reason that an ambiguous selector fails
instead of tapping its first match.

### Identifying the prompt on screen

A rule matches the alert on screen when **both** labels of its prompt are present among the buttons of
the alert, each exactly once. The two labels are the accepting one and the refusing one, resolved for
the locale of the run.

Both labels are required, not only the one the rule will tap, because the pair identifies the prompt.
"Allow" alone appears on the notification request and on the tracking prompt alike. The pair
`{"Allow", "Ask App Not to Track"}` appears on the tracking prompt alone.

Requiring each label exactly once follows the rule that `pick_alert_label` already applies. An alert
that carries two buttons of the same label resolves to nothing, not to whichever matched first.

The guard walks the rules in the order the file gives and taps the `choice` label of the first match.
For the three prompts that the table covers today, the order cannot change the outcome. Their label
pairs are pairwise distinct, so at most one rule can match the alert of any of those prompts. The
order is defined all the same. That way, adding a prompt whose pair overlaps another pair cannot
leave the behavior undefined.

Identification costs no extra device work. The guard already holds the button labels of the alert,
from the presence query that it performs before deciding what to tap. A rule is a comparison over data
in hand. It needs no screenshot, no model round trip, and no second query.

The AI-vision guard stays exactly where
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
left it. It is a fallback for what the native path cannot name. Prime directive 1 is therefore
untouched, and the rules path runs without an `ANTHROPIC_API_KEY`.

A rule contributes nothing to what that fallback is told. That is deliberate, not an omission. Every
path that reaches the vision guard is one where no rule identified the alert:

- a backend without the capability
- a surface that `springboard.alerts` cannot enumerate
- an alert whose prompt no rule matched

The tap label of a rule is therefore, by construction, the answer to some *other* prompt. The own
policy of the locator is to follow an instruction when given one, and to fall back to the
least-destructive button otherwise. Passing rule labels down would steer the locator to accept a
prompt that the scenario never named. That recreates the silent inversion this proposal removes, one
layer lower. A scenario that carries only `rules` leaves the locator on its least-destructive
default, exactly as it did before `rules` existed.

### Resolving a rule's labels, and failing loudly

A rule takes its two labels from `system_alert_label(prompt, choice, locale)`
(`bajutsu/scenario/system_alerts.py`). That is the locale-keyed table that
[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
introduced and
[BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md)
extended to the paste consent.

The guard resolves the labels once, while it is being built for a scenario. It uses the resolved
locale of that scenario, which is the same value the run pins the system language of the Simulator
to. Resolving at build time, not at each match, lets a rule compare plain strings on the hot path.

Resolving early also lets a rule fail loudly. A locale whose language the table does not cover raises
`UncoveredSystemAlertLocale`. The raise happens while the guard is under construction, so it surfaces
the mistake before the run performs any device work. The proactive step makes the same
raise-rather-than-guess choice, for the same reason: a guessed label would tap nothing, or tap the
other button.

### Layering over the target configuration

The target configuration reuses the scenario model for this setting
([BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)). A
project-wide default set of rules therefore needs no separate schema.

The rules of a scenario are consulted **before** the rules of the configuration. A scenario's own
answer for a prompt shadows the project default for that prompt. The project's answers for every other
prompt still apply. Ordering the scenario first keeps this setting consistent with every other layered
setting, where the scenario wins. A duplicate `prompt` across the two layers is thus an override. It
is not the parse error that a duplicate within one list is.

The rules of the target configuration apply only when neither the scenario nor `--alert-instruction`
already sets its own `instruction`. The reason is that `instruction` and `rules` are two vocabularies
for the same button policy, resolved independently. Without this restriction, a project-wide rule
added later could silently invert what the `instruction` of a scenario already decided for an
unrelated prompt. That is precisely the failure mode this proposal exists to remove, reappearing one
layer up.

An explicit `instruction` at the scenario or flag tier is required to opt out of every target rule,
not only the rule that answers the same prompt. This matches the coarse, all-or-nothing precedence
that `instruction` itself already has against the target configuration.

### Work breakdown

1. **Schema.** A `SystemAlertRule` model and a `rules` field on `SystemAlertHandling`
   (`bajutsu/scenario/models/scenario.py`), with the duplicate-`prompt` validator. The scenario package
   exports the model the way it exports the surrounding models.
2. **Matching.** Three pieces:
   - A resolved-rule type that carries the two identifying labels of a prompt and the label to tap.
   - A pure matching function beside `pick_alert_label`.
   - `AlertGuardConfig.probe_native` consulting the rules ahead of its candidate labels
     (`bajutsu/orchestrator/types.py`).
3. **Wiring.** Two pieces:
   - `_alert_guard_factory` (`bajutsu/cli/commands/run.py`) resolves the labels of each rule against the
     locale of the scenario. It layers the rules of the scenario ahead of the rules of the target
     configuration. It leaves the instruction of the AI-vision fallback untouched by `rules`.
   - `_apply_system_alert_handling` preserves `rules` alongside the existing button policy when the
     `--system-alert-handling` flag rewrites the setting of a scenario.
4. **Documentation.** The `systemAlertHandling` section of `docs/scenarios.md` and its `docs/ja/`
   mirror, including the recorded limitation about English-only default labels that rules close. The
   configuration and grammar references that list the fields of the setting change as well.
5. **Tests.** The deterministic suite covering each unit above.

### Machine-checkable outcome

The gate is `make check`. Unit tests in the Simulator-free suite cover the behavior:

- Matching returns the tap label of a prompt only when the full label pair of that prompt is present.
  It returns nothing when only the shared label is present.
- `probe_native` prefers a matching rule over the catch-all labels. It falls back to those labels when
  no rule matches.
- The guard factory resolves labels per locale and raises on a language the table does not cover.
- The guard factory lets the rule of a scenario shadow a configuration rule for the same prompt.
- The schema rejects a duplicate `prompt` within one list.

No assertion depends on a model, and no new call reaches one.

## Alternatives considered

**Keep the single ordered `instruction` list and document the ordering trick.** The list already
reaches every combination, so this alternative costs nothing to ship. It is rejected because the
failure mode is a silent inversion. The ordering that an author writes first grants the prompt that the
scenario meant to refuse. No assertion catches a permission that the scenario never asserted on. A
documented trick also stays correct only while today's label sets hold.

**A general matcher over literal button labels.** An entry would pair the labels to look for with the
label to tap. Such a matcher would cover alerts outside the table, including the own dialog of a
third-party software development kit. It would hand the author back the locale-specific transcription
that BE-0320 removed. It would also give the schema two ways to say what one prompt-named rule says.
The existing `instruction` already covers a literal-label catch-all, so the gap this alternative fills
is narrower than it appears.

**Match on the alert's title or body text.** Matching the wording of the prompt would identify alerts
that the button pair cannot distinguish. It is not available. The SpringBoard snapshot of the runner
reads the buttons of the alerts alone. The title would need a new snapshot in the Swift runner, a new
driver method, and a runner rebuild. Every prompt that the table covers is already identified by its
button pair, so this alternative is deferred, not adopted.

**Place a `handleSystemAlert` step per prompt.** This works today and is fully deterministic. The step
is proactive, though: it taps at the one point an author places it, so the author must know where in
the flow each prompt fires. The reactive guard exists for prompts whose timing a scenario cannot
predict. A step cannot be placed in exactly those cases.

**Widen `instruction` into a mapping keyed by prompt.** A mapping would read compactly.
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
did widen this very field instead of adding one beside it. But BE-0315 warned against a second field of
*button labels*, which would duplicate the vocabulary that `instruction` already carried. It asked an
implementer to converge on one grammar instead of growing another.

A prompt-named rule is not a second field of button labels. It reuses the `prompt` and `choice`
grammar that the proactive step already defines. Both existing forms of `instruction` are label
policies. Folding a third, differently shaped vocabulary into that one field would mix the two
vocabularies instead of converging them. A mapping also has no order to fall back on. A later prompt
whose label pair overlapped another pair would leave the match undefined.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Schema — `SystemAlertRule`, the `rules` field, and the duplicate-`prompt` validator.
- [x] Matching — the resolved-rule type, the matching function, and `probe_native` consulting rules.
- [x] Wiring — locale resolution and scenario-over-configuration layering, leaving the vision fallback's own instruction untouched.
- [x] Documentation — `docs/scenarios.md` and its Japanese mirror, plus the field references.
- [x] Tests — the deterministic suite covering each unit above.

## References

- [BE-0315 — Make the reactive alert guard deterministic and native, reusing BE-0316's SpringBoard path](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  — the native path this proposal branches, and the `instruction` label list the rules fall back to.
- [BE-0316 — Explicit mid-flow step for iOS permission-prompt alerts](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)
  — the proactive step whose `prompt` / `choice` vocabulary the rules reuse, and the SpringBoard
  query and tap the guard already calls.
- [BE-0320 — Make the iOS system-alert button selector deterministic under a non-English Simulator locale](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
  — the locale-keyed label table a rule resolves through, and the pinned system language that makes
  the resolved label the one on screen.
- [BE-0369 — Extend the iOS system-alert prompt/choice table to the paste-consent prompt](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md)
  — the third prompt the table covers, and therefore the third a rule can name.
- [BE-0314 — Deterministic interrupt handlers for unpredictable interstitial screens](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md)
  — the ordered list of conditional handlers this schema follows, for interstitial screens the
  application's own tree can see.
- [BE-0276 — Declarative per-scenario permission state (simctl privacy / pm grant)](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)
  — the pre-launch permission state that avoids a prompt outright, and the reason these three
  prompts remain: no `simctl` command pre-answers them.
- [BE-0177 — Per-target config defaults for run-behavior settings](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)
  — the precedence ladder this setting rides, and the shared model that gives rules a
  configuration-level default.
- [`docs/scenarios.md`](../../docs/scenarios.md) — the `systemAlertHandling` reference, including the
  recorded limitation that the built-in dismissive labels are English only.
