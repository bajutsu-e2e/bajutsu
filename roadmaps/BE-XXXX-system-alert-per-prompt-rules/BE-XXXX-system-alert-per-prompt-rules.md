**English** · [日本語](BE-XXXX-system-alert-per-prompt-rules-ja.md)

# BE-XXXX — Let the reactive system-alert guard answer each covered prompt by its own rule

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-system-alert-per-prompt-rules.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1672](https://github.com/bajutsu-e2e/bajutsu/pull/1672) |
| Topic | Scenario authoring features |
| Related | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario's `systemAlertHandling` setting clears an operating-system prompt that the application's
own accessibility tree cannot see — the "Allow Notifications" permission request, App Tracking
Transparency (ATT), the cross-process paste consent — by tapping one of the prompt's buttons
reactively, wherever the prompt interrupts a run. The setting carries one button policy for the whole
scenario, applied unchanged to whichever prompt interrupts the run. This proposal adds an ordered `rules`
list to the setting: each entry names one prompt and the choice to make on it, so a single scenario
can grant the notification request and refuse tracking. Each rule identifies the prompt on screen
from the button labels the operating system already reports, and resolves its own label through the
locale-keyed table that the proactive `handleSystemAlert` step already uses.

The contribution is not expressive power the setting lacks. The ordered candidate-label list the
setting takes today can already reach both answers, but only through an ordering the author must
derive from which labels two prompts happen to share — and the ordering that reads naturally gives
the opposite answer, with no error. Naming the prompt makes the author's intent explicit in the file
and makes the inverted answer unreachable.

## Motivation

The guard's button policy is one ordered list of candidate labels, applied to whatever prompt
appears. `AlertGuardConfig.probe_native` (`bajutsu/orchestrator/types.py`) reads the alert's buttons
through the `system_alert_labels()` read that
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
added over BE-0316's SpringBoard query, and `pick_alert_label` returns the first candidate present
on the alert exactly once, which the guard then taps. One list, one pass, and no notion of which
prompt is showing.

That list can nonetheless express two different answers, because it is ordered and its labels are
compared exactly. To grant the notification request and refuse tracking, an author writes
`instruction: ["Ask App Not to Track", "Allow"]`. The notification prompt offers no button named "Ask
App Not to Track", so the guard falls through to "Allow" and grants the request; the tracking prompt
offers both buttons, so the guard takes the earlier entry and refuses. Every combination of grant and
refuse across the three prompts the label table covers is reachable this way, because "Allow" is the
one label that two of those prompts share.

The encoding is the problem, not the set of outcomes it reaches. Three properties make an ordered
label list a poor way to record which answer belongs to which prompt.

First, the ordering that reads naturally gives the opposite answer, and gives it silently. "Grant
notifications, refuse tracking" reads as `["Allow", "Ask App Not to Track"]`, and that list grants
tracking as well: "Allow" is present on the tracking prompt, so the guard returns it and never
reaches the second entry. No step fails and no warning is printed. The run passes with the
application tracking the user, which is the answer the scenario was written to refuse.

Second, the list never records which prompt an entry answers. Reading `["Ask App Not to Track",
"Allow"]`, a contributor cannot tell that the second entry exists to accept the notification request
rather than to accept anything offering a button named "Allow". The discrimination lives in the
order, which is derived knowledge rather than stated intent, so an edit that adds an answer for a
third prompt has to re-derive the whole ordering. That derivation also rests on today's label sets:
an operating-system prompt introduced later whose accepting button is also "Allow" would be granted
by the same list, silently, because the list says nothing about which prompt it meant.

Third, the labels are literal text in a single language. Under a Japanese locale the same intent
becomes `["アプリにトラッキングしないように要求", "許可"]`, transcribed by hand — the transcription
trap that
[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
removed for the proactive `handleSystemAlert` step by letting a scenario name a prompt and a choice
instead of the text. The guard still carries the resulting gap, and its own documentation records it:
the built-in dismissive labels are literal English, so under a non-English locale the native path
matches nothing and falls back to the AI-vision guard. A rule that names the prompt reads its labels
from the same locale-keyed table the step uses, which closes that gap for every prompt the table
covers.

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

`rules` is an ordered list whose entries each carry a `prompt` and a `choice` — the two vocabularies
the proactive `handleSystemAlert` step already takes, so `prompt` is `notifications`, `tracking`, or
`paste`, and `choice` is `grant` or `deny`. Both fields are required on an entry, because a rule
identifies its prompt by name and an author who wants to name a literal button keeps `instruction`
for that.

`rules` and `instruction` compose rather than exclude each other, which answers the question an
author reaches this feature with. The guard consults the rules first; an alert that no rule
identifies falls through to `instruction`, or to the built-in dismissive labels when the scenario
supplies none. A scenario therefore answers by name the prompts it knows about and still dismisses
whatever else appears.

Two entries naming the same `prompt` within one list fail at parse time. Silently taking the first
would hide an authoring mistake, and the schema refuses it for the reason an ambiguous selector fails
rather than tapping its first match.

### Identifying the prompt on screen

A rule matches the alert on screen when **both** of its prompt's labels — the accepting one and the
refusing one, resolved for the run's locale — are present among the alert's buttons, each exactly
once. Both labels, not only the one the rule will tap, because the pair is what identifies the
prompt: "Allow" alone appears on the notification request and on the tracking prompt alike, while the
pair `{"Allow", "Ask App Not to Track"}` appears on the tracking prompt alone. Requiring each label
exactly once follows the rule `pick_alert_label` already applies, so an alert carrying two buttons of
the same label resolves to nothing rather than to whichever matched first.

The guard walks the rules in the order the file gives and taps the first match's `choice` label. For
the three prompts the table covers today the order cannot change the outcome, since their label pairs
are pairwise distinct, so at most one rule can match any of those prompts' alerts. The order is defined all the same, so
that adding a prompt whose pair overlaps another's cannot leave the behavior undefined.

Identification costs no extra device work. The guard already holds the alert's button labels from the
presence query it performs before deciding what to tap, so a rule is a comparison over data in hand:
no screenshot, no model round trip, and no second query. The AI-vision guard stays exactly where
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
left it — a fallback for what the native path cannot name — so prime directive 1 is untouched, and
the rules path runs without an `ANTHROPIC_API_KEY`.

A rule contributes nothing to what that fallback is told, which is deliberate rather than an omission.
Every path reaching the vision guard is one where no rule identified the alert: a backend without the
capability, a surface `springboard.alerts` cannot enumerate, or an alert whose prompt no rule matched.
A rule's tap label is therefore, by construction, some *other* prompt's answer, and the locator's own
policy is to follow an instruction when given one and fall back to the least-destructive button
otherwise. Passing rule labels down would steer it to accept a prompt the scenario never named — the
silent inversion this proposal removes, re-created one layer lower. A scenario carrying only `rules`
leaves the locator on its least-destructive default, exactly as it did before `rules` existed.

### Resolving a rule's labels, and failing loudly

A rule's two labels come from `system_alert_label(prompt, choice, locale)`
(`bajutsu/scenario/system_alerts.py`), the locale-keyed table
[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
introduced and
[BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md)
extended to the paste consent. The guard resolves them once while it is being built for a scenario,
using that scenario's own resolved locale — the same value the run pins the Simulator's system
language to — rather than at each match, so a rule compares plain strings on the hot path.

Resolving early is also what lets a rule fail loudly. A locale whose language the table does not
cover raises `UncoveredSystemAlertLocale`, and raising it while the guard is under construction
surfaces the mistake before the run performs any device work — the raise-rather-than-guess choice
the proactive step also makes, for the same reason: a guessed label would tap nothing, or tap the
other button.

### Layering over the target configuration

The target configuration reuses the scenario model for this setting
([BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)), so a
project-wide default set of rules needs no separate schema. A scenario's rules are consulted **before**
the configuration's, so a scenario's own answer for a prompt shadows the project default for that
prompt while the project's answers for every other prompt still apply. Ordering the scenario first
keeps this setting consistent with every other layered setting, where the scenario wins; a duplicate
`prompt` across the two layers is therefore an override, not the parse error a duplicate within one
list is.

The target configuration's rules apply only when neither the scenario nor `--alert-instruction`
already sets its own `instruction`. `instruction` and `rules` are two vocabularies for the same
button policy, resolved independently, so without this a project-wide rule added later could
silently invert what a scenario's `instruction` already decided for an unrelated prompt — precisely
the failure mode this proposal exists to remove, reappearing one layer up. Requiring an explicit
`instruction` at the scenario or flag tier to opt out of every target rule, rather than only the one
answering the same prompt, matches the coarse, all-or-nothing precedence `instruction` itself already
has against the target configuration.

### Work breakdown

1. **Schema.** A `SystemAlertRule` model and a `rules` field on `SystemAlertHandling`
   (`bajutsu/scenario/models/scenario.py`), with the duplicate-`prompt` validator, exported from the
   scenario package the way the surrounding models are.
2. **Matching.** A resolved-rule type carrying a prompt's two identifying labels and the label to
   tap, a pure matching function beside `pick_alert_label`, and `AlertGuardConfig.probe_native`
   consulting the rules ahead of its candidate labels (`bajutsu/orchestrator/types.py`).
3. **Wiring.** `_alert_guard_factory` (`bajutsu/cli/commands/run.py`) resolving each rule's labels
   against the scenario's locale and layering the scenario's rules ahead of the target
   configuration's, while leaving the AI-vision fallback's instruction untouched by `rules`;
   `_apply_system_alert_handling` preserving `rules` alongside the existing button policy when the
   `--system-alert-handling` flag rewrites a scenario's setting.
4. **Documentation.** The `systemAlertHandling` section of `docs/scenarios.md` and its `docs/ja/`
   mirror, including the recorded limitation about English-only default labels that rules close, plus
   the configuration and grammar references that list the setting's fields.
5. **Tests.** The deterministic suite covering each unit above.

### Machine-checkable outcome

The gate is `make check`, and the behavior is covered by unit tests in the Simulator-free suite:
matching returns a prompt's tap label only when that prompt's full label pair is present, and returns
nothing when only the shared label is; `probe_native` prefers a matching rule over the catch-all
labels and falls back to them when no rule matches; the guard factory resolves labels per locale,
raises on a language the table does not cover, and lets a scenario's rule shadow a configuration rule
for the same prompt; and the schema rejects a duplicate `prompt` within one list. No assertion
depends on a model, and no new call reaches one.

## Alternatives considered

**Keep the single ordered `instruction` list and document the ordering trick.** The list already
reaches every combination, so this alternative costs nothing to ship. It is rejected because the
failure mode is a silent inversion: the ordering an author writes first grants the prompt the
scenario meant to refuse, and no assertion catches a permission the scenario never asserted on. A
documented trick also stays correct only while today's label sets hold.

**A general matcher over literal button labels** — an entry pairing the labels to look for with the
label to tap. Such a matcher would cover alerts outside the table, including a third-party
software development kit's own dialog, but it hands the author back the locale-specific transcription
that BE-0320 removed, and it gives the schema two ways to say what one prompt-named rule says. The
existing `instruction` already covers a literal-label catch-all, so the gap this alternative fills is
narrower than it appears.

**Match on the alert's title or body text.** Matching the prompt's own wording would identify alerts
the button pair cannot distinguish. It is not available: the runner's SpringBoard snapshot reads the
alerts' buttons alone, so the title would need a new snapshot in the Swift runner, a new driver
method, and a runner rebuild. Every prompt the table covers is already identified by its button pair,
so this alternative is deferred rather than adopted.

**Place a `handleSystemAlert` step per prompt.** This works today and is fully deterministic, but the
step is proactive: it taps at the one point an author places it, so it requires knowing where in the
flow each prompt fires. The reactive guard exists for prompts whose timing a scenario cannot predict,
which is exactly when a step cannot be placed.

**Widen `instruction` into a mapping keyed by prompt.** A mapping would read compactly, and
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
did widen this very field rather than add one beside it — but what that item warned against was a
second field of *button labels*, duplicating the vocabulary `instruction` already carried, and it
asked an implementer to converge on one grammar instead of growing another. A prompt-named rule is
neither: it reuses the `prompt` and `choice` grammar the proactive step already defines, while both
of `instruction`'s existing forms are label policies. Folding a third, differently-shaped vocabulary
into that one field would mix the two rather than converge them. A mapping also has no order to fall
back on, so a later prompt whose label pair overlapped another's would leave the match undefined.

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
