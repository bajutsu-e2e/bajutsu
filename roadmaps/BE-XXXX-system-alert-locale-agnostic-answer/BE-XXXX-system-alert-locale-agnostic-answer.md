**English** · [日本語](BE-XXXX-system-alert-locale-agnostic-answer-ja.md)

# BE-XXXX — Answer a system alert by button role, so no Simulator language is uncovered

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-system-alert-locale-agnostic-answer.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md), [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md) |
<!-- /BE-METADATA -->

## Introduction

The `handleSystemAlert` step and the reactive `systemAlertHandling` guard clear an iOS system prompt, such as "Allow Notifications", by tapping one of its buttons. Today both find that button through a table of button labels that covers two languages, English and Japanese. Under any other `locale` the step fails with `UncoveredSystemAlertLocale`. A scenario whose `systemAlertHandling.rules` name a prompt fails the same way, before any device work, when the runner resolves those rules.

This item removes the language from the answer. We identify what a button *does* (grant or deny) from properties that do not change with the language, so the `handleSystemAlert` step answers a prompt the same way under every `locale`. The guard keeps its label table, because it must first tell which declared prompt is on screen, and it cannot do that without labels. The label table stays only as a fallback for a prompt whose buttons expose no such property. Before any of this ships, a measurement unit decides which properties SpringBoard, the iOS system process that draws these prompts, actually exposes.

## Motivation

[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md) made the answer deterministic by pinning the Simulator's system language to the scenario's `locale`, then looking labels up in a table keyed by language. That works for the languages in the table and for no others. `bajutsu/common/scenario/system_alerts/_functions.py` holds entries for `en` and `ja` only, and a scenario under `locale: fr_FR` raises `UncoveredSystemAlertLocale`. An author who wants a prompt gone, in any language, has to add rows for that language by hand.

The table also grows with Apple's wording, not with anything the scenario says. Every iOS release can reword a button, as the two shapes of the save-password prompt already show. The maintenance burden is per language, per prompt, and per iOS version, and nothing but a real Simulator can confirm a row.

BE-0320 rejected position-based selection as the *sole* mechanism, because nobody had verified that SpringBoard orders a prompt's buttons the same way in every language. This item treats that as an open measurement rather than a reason to stop. If the ordering or another property holds across languages, the objection disappears. If it does not, this item says so and keeps the table.

A later reader can tell the change arrived by running one notification-permission scenario under `locale: fr_FR`, or any language outside `en` and `ja`. Before the change the step fails with `UncoveredSystemAlertLocale`; after it, the prompt is dismissed and the report shows the tapped role.

## Detailed design

The work splits into measuring what SpringBoard exposes, resolving a role from it, using the role on the step, reporting which role was tapped, and verifying on a real Simulator.

1. **Measure which button properties are language-independent.** For each covered prompt (notifications, tracking, paste), boot Simulators pinned to at least five languages, including one right-to-left language, and dump every button's ordinal, frame, accessibility identifier, element type, and value. The production query cannot supply this: `XcuitestElementProvider.querySystemAlertButtons` reads the label and frame alone, and fills `identifier` and `value` with `nil` and `traits` with a fixed `button`. The measurement therefore runs through a probe of its own that reads each `XCUIElement` in full, and the production query changes only for a property the measurement proves stable. Record which properties stay identical across languages and iOS versions. The save-password prompt stays on the label table, because it is drawn inside the app rather than by SpringBoard. This unit produces a table in this item's *Progress* section and gates units 2 to 4: a property that varies by language is dropped, not worked around.
2. **Resolve a role from a stable property.** Add a resolver next to `system_alert_label` that maps `(prompt, choice)` to a selection rule over the stable properties unit 1 found, such as "the button at ordinal 1 of 2". The step needs no prompt identification, because the author already names the prompt. The `Driver` seam has to carry more than labels: `system_alert_labels()` returns strings, and `handle_system_alert` taps by selector, so this unit extends both across every backend. It also moves the step's resolution from interpolation time (`_resolve_system_alert`) to after the live alert query.
3. **Use the role on the step, and keep the guard on the label table.** The `handleSystemAlert` step resolves `prompt` plus `choice` through the role rule first and the label table second. The reactive guard stays as it is. Its rules ([BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md)) name prompts, and the guard must match the alert on screen against them. The notification, tracking, and paste prompts all show two buttons, so the guard may be unable to tell which one is showing without labels. Under a language the label table does not cover, a guard rule therefore keeps failing loudly with `UncoveredSystemAlertLocale`, rather than tapping a role on an alert no rule identifies. [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md) removed that kind of guessed tap, and BE-0320 chose the same loud failure for an uncovered language (prime directive 2). On the step, `UncoveredSystemAlertLocale` remains only for a prompt whose buttons expose no stable property, and its message says so.
4. **Report the tap by role.** Record the tapped button's label and the rule that chose it on the `handleSystemAlert` step's outcome, which carries neither today. A run under an unfamiliar language then shows what was tapped and why. The guard's `AlertEvent.label` already records the button the guard tapped, and the guard gains no role rule to report.
5. **Verify on a real Simulator.** Run the showcase permission scenario under `en_US`, `ja_JP`, `fr_FR`, and `ar_SA`, and confirm `grant` and `deny` tap different buttons, each leaving the matching app-side authorization status. Confirm also that a guard rule under `fr_FR` still fails loudly before any device work. Update `docs/configuration.md` and the scenario documentation, in both languages.

This item does not change the Simulator language pinning of BE-0320 and adds no model call. The verdict stays with the deterministic runner.

## Alternatives considered

- **Add more languages to the label table.** This is the simplest change, and BE-0320 already rejected it as the sole mechanism. It stays as the fallback in unit 3, because a prompt whose buttons expose no stable property still needs it.
- **Pin only SpringBoard to English while the app keeps its own `locale`.** The existing English rows would then answer every run. The app and the alert would render different languages in one run, which hides real localization defects in the alert-adjacent screens a scenario asserts on.
- **Route unknown languages through the vision guard.** [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md) removed that fallback from `run`, since a model call must not decide a step's outcome (prime directive 1).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1: measure which button properties stay identical across languages and iOS versions.
- [ ] Unit 2: resolve a role from a stable property, and extend the Driver seam to carry it.
- [ ] Unit 3: use the role on the `handleSystemAlert` step, with the label table as fallback; the guard keeps the label table and its loud failure.
- [ ] Unit 4: report the tapped label and the chosen rule on the step's outcome.
- [ ] Unit 5: verify under four languages on a real Simulator and update the documentation.

## References

- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md): the Simulator language pinning and the label table this item builds on.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md): the `handleSystemAlert` step and its SpringBoard query.
- [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules.md): the per-prompt rules of the reactive guard.
- [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md): declared prompts and the save-password shapes.
- `bajutsu/common/scenario/system_alerts/_functions.py`: the current label table.
