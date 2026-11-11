**English** · [日本語](BE-0317-rename-dismiss-alerts-to-alert-handling-ja.md)

# BE-0317 — Rename the dismissAlerts guard to alertHandling to match its grant-or-dismiss behavior

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0317](BE-0317-rename-dismiss-alerts-to-alert-handling.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0317") |
| Implementing PR | [#1331](https://github.com/bajutsu-e2e/bajutsu/pull/1331) |
| Topic | Scenario authoring features |
| Related | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) |
<!-- /BE-METADATA -->

## Introduction

This item renames the scenario field `dismissAlerts` to `alertHandling`. The rename covers the
config-default and CLI-flag surfaces of the field as well. The old spelling stays as a deprecated
alias, so existing scenarios, configs, and command lines keep working unchanged. The behavior is
untouched. The change is purely a naming change.

`alertHandling` names what the guard actually does. The guard is the vision-based recovery for an OS
prompt that the app-scoped accessibility tree cannot see or tap. On a blocked step, the guard
screenshots the screen, asks the model where to tap, taps, and retries. The guard does not merely
*dismiss* the prompt.

With an instruction, the guard taps whatever button the instruction names, including an accepting
button. The setting `dismissAlerts: { instruction: "tap Allow" }` grants a permission instead of
dismissing the prompt. The name should cover both the dismissing and the accepting case, and
`alertHandling` does.

## Motivation

The verb *dismiss* means to send away or reject. The name `dismissAlerts` reads as
"dismiss the prompts": tap the dismissive button and move on. That reading describes half of the guard's
behavior.

The `instruction` field of the guard taps any named button. The documented, load-bearing use of the
field is to *accept* a prompt:

- `docs/scenarios.md` gives `dismissAlerts: { instruction: "tap Allow" }` as the way to **grant** a
  permission.
- The field's own docstring in `bajutsu/scenario/models/scenario.py` carries the same "e.g. `tap
  Allow` to grant a prompt" example.

A name that says *dismiss* misleads a reader about what the field does, because the field's headline
documented use is to *grant*. The mismatch is between a narrow name and a broader behavior. The
sibling proposal that adds an explicit `handleSystemAlert` step (`ios-permission-alert-step`) hit
the same mismatch when that proposal first tried the name `permissionAlert`.

A second reason reinforces the first. The sibling proposal adds a new deterministic step,
`handleSystemAlert`. The step taps a system alert at an author-chosen point with no model call. The
step is an *action* inside `steps`, while the guard renamed here is a scenario-level *setting*.

Bajutsu's DSL already tells those two apart grammatically. Actions read as verbs (`tap`, `wait`,
`swipe`). Scenario-level settings read as nouns (`permissions`, `preconditions`). The explicit action
takes the verb phrase `handleSystemAlert`, and the reactive guard's setting takes the noun
phrase `alertHandling`. A reader tells the two roles apart by grammatical category, not by two
look-alike names.

The rename is worth doing for the accuracy reason alone. The clearer role boundary beside
`handleSystemAlert` is a bonus, not the justification.

## Detailed design

Proposal altitude. The work is MECE along the units below. The guiding constraint is that no existing
scenario, config, or command line may break. Every renamed surface keeps the old spelling as an
accepted, deprecated alias.

The canonical spelling and the deprecated alias parse to the same guard, so both scenarios below are
equivalent:

```yaml
# canonical (new)
- name: grant the notification prompt
  alertHandling: { instruction: "tap Allow" }
  steps: [ ... ]

# deprecated dismissAlerts alias — still accepted, emits a one-time deprecation notice
- name: grant the notification prompt
  dismissAlerts: { instruction: "tap Allow" }
  steps: [ ... ]

# the bare-boolean form is unchanged; alertHandling: false turns the guard off for one scenario
```

The CLI mirrors the rename. The flag `bajutsu run --alert-handling` / `--no-alert-handling` is the
canonical flag. The flag `--dismiss-alerts` / `--no-dismiss-alerts` keeps working as a hidden,
deprecated alias.

- **Scenario schema.** Rename the `DismissAlerts` model and the `Scenario.dismiss_alerts` field to
  `AlertHandling` and `alert_handling`. The YAML key `alertHandling` becomes the canonical alias.
  The key `dismissAlerts` stays as an additional accepted input alias (Pydantic `AliasChoices`), so a
  scenario written either way parses. A dumped scenario emits the new `alertHandling` key. The two
  on-disk forms (bare boolean, or `{ instruction: "..." }`) stay unchanged.
- **Config-default surface.** The app-level default lives in the target config
  (`bajutsu/config/schema.py`, surfaced through `bajutsu/config/effective.py` and
  `bajutsu/config/resolve.py`) under the same `dismissAlerts` key. Rename the key to `alertHandling`
  the same way, and keep `dismissAlerts` as an accepted alias.
- **CLI flags.** Make `--alert-handling` / `--no-alert-handling` the canonical flag on all three
  commands that carry the flag today: `run`, `record`, and `crawl`. Keep `--dismiss-alerts` /
  `--no-dismiss-alerts` as hidden, deprecated aliases that map to the same option, so existing
  invocations and CI still work. The flag `--alert-instruction` (also on all three commands) already
  reads as alert-neutral, so the flag stays as is. Update the `run` capability's `claude_flag`
  (`bajutsu/capabilities.py`) to the canonical `--alert-handling` spelling.
- **Deprecation signal.** Emit a one-time deprecation notice when the old `dismissAlerts` key or the
  `--dismiss-alerts` flag is used. The notice points to the new name. The notice is a log line on the
  authoring and CLI path. The notice never appears on the deterministic `run` verdict path (prime
  directive 1), and the notice never changes the run's outcome. The alias behaves identically to the
  canonical name.
- **Docs.** Rename every documented mention across `docs/` and the `docs/ja/` mirror to
  `alertHandling`. Add a short note that `dismissAlerts` is the accepted deprecated alias.

  The mentions today span eleven files on each side: `scenarios.md` (the section heading and the
  field table), `dsl-grammar.md`, `configuration.md`, `recording.md`, `ai-boundary.md`,
  `cookbook.md`, `cli.md`, `concepts.md`, `ci.md`, `self-hosting.md`, and `architecture.md`.

  Renaming the `scenarios.md` section heading changes the heading's slug. The anchor links that point
  at the heading must change in the same change, or the links dangle. The links sit in the
  field-table `[below]` link of `scenarios.md`, in `cli.md`, and in `recording.md`, in both
  languages.

  Coordinate with the docs of the sibling `handleSystemAlert` proposal. The docs of the two proposals
  should contrast the two features by role (reactive guard versus explicit step) in the same place,
  instead of leaving the features to collide by name.
- **Tests.** Cover five behaviors:
  - The canonical `alertHandling` and the `dismissAlerts` alias parse to the same model.
  - The config default works under either key.
  - Both CLI flag spellings work on each of `run`, `record`, and `crawl`.
  - A dump emits the new key.
  - The deprecation notice fires on the old spelling.

## Alternatives considered

- **Keep `dismissAlerts` and resolve the confusion in docs alone.** The sibling `handleSystemAlert`
  proposal already plans a docs contrast between the reactive guard and the explicit step. The
  contrast helps a reader keep the two features apart. The contrast does nothing for the primary
  problem: `dismiss` names the dismissing half of a field whose documented headline use is to
  *grant*. No amount of surrounding prose makes the field name itself accurate. Rejected as
  insufficient.
- **Rename with no alias (a hard break).** The code stays simpler, but the rename breaks every
  existing scenario, target config, and CI command line that names `dismissAlerts` or
  `--dismiss-alerts`. Rejected. The accuracy gain does not justify breaking users, and an alias makes
  the break unnecessary.
- **Pick a different name (`alertGuard`, `autoAlerts`).** The name `alertGuard` matches the existing
  "system-alert guard" phrasing, but leans on the reactive-guard framing. The name `autoAlerts`
  stresses the automatic-versus-explicit contrast with `handleSystemAlert`, but reads oddly as a
  scenario field. The authors chose the name `alertHandling` because *handling* covers both the dismissing
  and the accepting behavior without leaning on either. The name also reads naturally as a
  scenario-level setting, a noun phrase, beside the action verb `handleSystemAlert`.
- **Rename `handleSystemAlert` instead, to move the collision the other way.** The sibling proposal
  already settled `handleSystemAlert` as a target-neutral name that fits the step's future scope: any
  SpringBoard alert, not just a permission prompt. The narrow, behavior-mismatched name is
  `dismissAlerts`, so `dismissAlerts` is the side worth renaming.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Scenario schema — `AlertHandling` / `alertHandling`, `dismissAlerts` kept as an input alias.
- [x] Config-default surface — `alertHandling` key with `dismissAlerts` alias.
- [x] CLI flags — `--alert-handling` canonical on `run`/`record`/`crawl`, `--dismiss-alerts` hidden deprecated alias; capability `claude_flag`.
- [x] Deprecation signal on the old key / flag (authoring / CLI path only).
- [x] Docs — rename every mention across `docs/` + `docs/ja/` (eleven files each side), fix the anchor
      links the heading-slug change breaks, note the alias, contrast with `handleSystemAlert`.
- [x] Tests — both spellings parse, config default, both flags, dump emits new key, deprecation notice.

## References

- Sibling proposal `ios-permission-alert-step` (`handleSystemAlert`): the explicit, deterministic
  step that the renamed setting of this guard sits beside. The two are told apart by part of speech
  (verb action versus noun setting). The docs of the two proposals should contrast the reactive guard
  against the explicit step in one place.
- [BE-0269: Early mid-wait intervention for the iOS alert guard](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md).
  The guard being renamed.
- [BE-0276: Declarative per-scenario permission state](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md).
  The deterministic pre-launch complement to the guard, which the guard's own docs already contrast
  against.
- `bajutsu/scenario/models/scenario.py` (`DismissAlerts`), `bajutsu/agents/alerts.py`,
  `bajutsu/cli/commands/run.py`, `bajutsu/cli/commands/record.py`, `bajutsu/cli/commands/crawl.py`,
  `bajutsu/config/schema.py`, and `bajutsu/capabilities.py`: the surfaces that the rename touches.
