**English** · [日本語](BE-XXXX-rename-dismiss-alerts-to-alert-handling-ja.md)

# BE-XXXX — Rename the dismissAlerts guard to alertHandling to match its grant-or-dismiss behavior

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-rename-dismiss-alerts-to-alert-handling.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) |
<!-- /BE-METADATA -->

## Introduction

This item renames the scenario field `dismissAlerts` — and its config-default and CLI-flag
surfaces — to `alertHandling`, keeping the old spelling as a deprecated alias so existing scenarios,
configs, and command lines keep working unchanged. The behavior is untouched: this is a naming
change only. `alertHandling` names what the guard actually does. The guard is the vision-based
recovery for an OS prompt the app-scoped accessibility tree cannot see or tap — on a blocked step it
screenshots the screen, asks the model where to tap, taps, and retries — and it does not merely
*dismiss* the prompt. With an instruction it taps whatever button that instruction names, including
an accepting one: `dismissAlerts: { instruction: "tap Allow" }` grants a permission rather than
dismissing it. The name should cover both the dismissing and the accepting case, and `alertHandling`
does.

## Motivation

The verb *dismiss* means to send away or reject, so `dismissAlerts` reads as "dismiss the prompts" —
tap the dismissive button and move on. That describes only one of the two things the guard does. Its
`instruction` field taps any named button, and the documented, load-bearing use of it is to
*accept* a prompt: `docs/scenarios.md` gives `dismissAlerts: { instruction: "tap Allow" }` as the
way to **grant** a permission, and the field's own docstring in `bajutsu/scenario/models/scenario.py`
carries the same "e.g. `tap Allow` to grant a prompt" example. A name that says *dismiss* while the
field's headline documented use is to *grant* misleads a reader about what the field does — the same
mismatch, between a narrow name and a broader behavior, that the sibling proposal to add an explicit
`handleSystemAlert` step (`ios-permission-alert-step`) hit when it first tried the name `permissionAlert`.

A second reason reinforces the first. The sibling proposal adds a new deterministic step,
`handleSystemAlert`, that taps a system alert at an author-chosen point with no model call. That
step is an *action* inside `steps`, whereas the guard renamed here is a scenario-level *setting*.
Bajutsu's DSL already tells those apart grammatically — actions read as verbs (`tap`, `wait`,
`swipe`), scenario-level settings as nouns (`permissions`, `preconditions`) — so the explicit
action takes the verb phrase `handleSystemAlert` and the reactive guard's setting takes the noun
phrase `alertHandling`, and a reader tells the two roles apart by that grammatical category rather
than by two look-alike names. The rename is worth doing for the accuracy reason alone; the clearer
role boundary beside `handleSystemAlert` is a bonus, not the justification.

## Detailed design

Proposal altitude. The work is MECE along the units below. The guiding constraint is that no
existing scenario, config, or command line may break: every renamed surface keeps the old spelling
as an accepted, deprecated alias.

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

The CLI mirrors the rename: `bajutsu run --alert-handling` / `--no-alert-handling` is the canonical
flag, and `--dismiss-alerts` / `--no-dismiss-alerts` keeps working as a hidden, deprecated alias.

- **Scenario schema.** Rename the `DismissAlerts` model and the `Scenario.dismiss_alerts` field to
  `AlertHandling` / `alert_handling`, with the YAML key `alertHandling` as the canonical alias and
  `dismissAlerts` kept as an additional accepted input alias (Pydantic `AliasChoices`), so a
  scenario written either way parses. A dumped scenario emits the new `alertHandling` key. The two
  on-disk forms (bare boolean, or `{ instruction: "..." }`) are unchanged.
- **Config-default surface.** The app-level default lives in the target config
  (`bajutsu/config/schema.py`, surfaced through `bajutsu/config/effective.py` /
  `bajutsu/config/resolve.py`) under the same `dismissAlerts` key. Rename it to `alertHandling` the
  same way, with `dismissAlerts` kept as an accepted alias.
- **CLI flags.** Make `--alert-handling` / `--no-alert-handling` the canonical flag on all three
  commands that carry it today — `run`, `record`, and `crawl` — and keep `--dismiss-alerts` /
  `--no-dismiss-alerts` as hidden, deprecated aliases that map to the same option so existing
  invocations and CI still work. `--alert-instruction` (also on all three) already reads as
  alert-neutral, so it stays as is. Update the `run` capability's `claude_flag`
  (`bajutsu/capabilities.py`) to the canonical `--alert-handling` spelling.
- **Deprecation signal.** Emit a one-time deprecation notice when the old `dismissAlerts` key or
  `--dismiss-alerts` flag is used, pointing to the new name. The notice is a log line on the
  authoring / CLI path, never anything on the deterministic `run` verdict path (prime directive 1),
  and it never changes the run's outcome — the alias behaves identically to the canonical name.
- **Docs.** Rename every documented mention across `docs/` and its `docs/ja/` mirror to
  `alertHandling`, with a short note that `dismissAlerts` is the accepted deprecated alias. The
  mentions today span ten files on each side: `scenarios.md` (the section heading and the field
  table), `dsl-grammar.md`, `configuration.md`, `recording.md`, `ai-boundary.md`, `cookbook.md`,
  `cli.md`, `concepts.md`, `ci.md`, and `self-hosting.md`. Renaming the `scenarios.md` section
  heading changes its slug, so the anchor links pointing at it (in `scenarios.md`'s own field-table
  `[below]` link, `cli.md`, and `recording.md`, both languages) must be updated in the same change
  or they dangle. Coordinate with the sibling
  `handleSystemAlert` proposal's docs so the two features are contrasted by role (reactive guard versus
  explicit step) in the same place, rather than left to collide by name.
- **Tests.** Cover both the canonical `alertHandling` and the `dismissAlerts` alias parsing to the
  same model; the config default under either key; both CLI flag spellings on each of `run`,
  `record`, and `crawl`; a dump emitting the new key; and the deprecation notice firing on the old
  spelling.

## Alternatives considered

- **Keep `dismissAlerts` and resolve the confusion in docs alone.** The sibling `handleSystemAlert`
  proposal already plans a docs contrast between the reactive guard and the explicit step. That
  helps a reader keep the two features apart, but it does nothing for the primary problem: `dismiss`
  names only the dismissing half of a field whose documented headline use is to *grant*. No amount
  of surrounding prose makes the field name itself accurate. Rejected as insufficient.
- **Rename with no alias (a hard break).** Simpler in the code, but it breaks every existing
  scenario, target config, and CI command line that names `dismissAlerts` / `--dismiss-alerts`.
  Rejected: the accuracy gain does not justify breaking users, and an alias makes the break
  unnecessary.
- **Pick a different name (`alertGuard`, `autoAlerts`).** `alertGuard` matches the existing
  "system-alert guard" phrasing but leans on the reactive-guard framing; `autoAlerts` stresses the
  automatic-versus-explicit contrast with `handleSystemAlert` but reads oddly as a scenario field.
  `alertHandling` was chosen because *handling* covers both the dismissing and the accepting behavior
  without leaning on either, and reads naturally as a scenario-level setting — a noun phrase —
  beside the action verb `handleSystemAlert`.
- **Rename `handleSystemAlert` instead, to move the collision the other way.** The sibling proposal
  already settled `handleSystemAlert` as a target-neutral name that fits its future scope (any SpringBoard
  alert, not only a permission prompt). The narrow, behavior-mismatched name is `dismissAlerts`, so
  this is the side worth renaming.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Scenario schema — `AlertHandling` / `alertHandling`, `dismissAlerts` kept as an input alias.
- [ ] Config-default surface — `alertHandling` key with `dismissAlerts` alias.
- [ ] CLI flags — `--alert-handling` canonical on `run`/`record`/`crawl`, `--dismiss-alerts` hidden deprecated alias; capability `claude_flag`.
- [ ] Deprecation signal on the old key / flag (authoring / CLI path only).
- [ ] Docs — rename every mention across `docs/` + `docs/ja/` (ten files each side), fix the anchor
      links the heading-slug change breaks, note the alias, contrast with `handleSystemAlert`.
- [ ] Tests — both spellings parse, config default, both flags, dump emits new key, deprecation notice.

## References

- Sibling proposal `ios-permission-alert-step` (`handleSystemAlert`) — the explicit, deterministic
  step this guard's renamed setting sits beside, the two told apart by part of speech (verb action
  versus noun setting); the two proposals' docs should contrast the reactive guard against the
  explicit step in one place.
- [BE-0269 — Early mid-wait intervention for the iOS alert guard](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md) —
  the guard being renamed.
- [BE-0276 — Declarative per-scenario permission state](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) —
  the deterministic pre-launch complement to the guard, which the guard's own docs already contrast against.
- `bajutsu/scenario/models/scenario.py` (`DismissAlerts`), `bajutsu/agents/alerts.py`,
  `bajutsu/cli/commands/run.py`, `bajutsu/cli/commands/record.py`, `bajutsu/cli/commands/crawl.py`,
  `bajutsu/config/schema.py`, `bajutsu/capabilities.py` — the surfaces the rename touches.
