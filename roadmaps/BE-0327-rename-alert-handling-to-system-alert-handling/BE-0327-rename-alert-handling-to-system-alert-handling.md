**English** · [日本語](BE-0327-rename-alert-handling-to-system-alert-handling-ja.md)

# BE-0327 — Rename the alertHandling guard to systemAlertHandling to name what it handles

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0327](BE-0327-rename-alert-handling-to-system-alert-handling.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0327") |
| Implementing PR | [#1388](https://github.com/bajutsu-e2e/bajutsu/pull/1388) |
| Topic | Scenario authoring features |
| Related | [BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) |
<!-- /BE-METADATA -->

## Introduction

This item renames the scenario field `alertHandling` to `systemAlertHandling`. The rename also covers
the field's config-default and CLI-flag surfaces. The old `alertHandling` and the older
`dismissAlerts` stay as deprecated aliases, so existing scenarios, configs, and command lines keep
working unchanged.

The behavior is untouched. This is a naming change, one step further along the path that
[BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling.md)
already took when it renamed `dismissAlerts` to `alertHandling`.

`systemAlertHandling` names the guard's subject as precisely as its sibling deterministic step,
[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)'s
`handleSystemAlert`. Both names say "system alert" explicitly. A reader tells the two apart by part
of speech: the verb phrase `handleSystemAlert` names the explicit step, and the noun phrase
`systemAlertHandling` names the reactive guard's setting. The two no longer look alike while
differing in whether "system" appears.

## Motivation

`alertHandling` reads naturally on its own, but it says nothing about *what kind* of alert it
handles. Bajutsu's scenario DSL now names two closely related things. One is the reactive guard
renamed here. The other is BE-0316's `handleSystemAlert` step, which taps a SpringBoard permission
prompt at an author-chosen point.

Both act on the same class of thing: a SpringBoard-level system alert that the app-scoped
accessibility tree cannot see. BE-0316's own docs already draw the reactive-versus-proactive contrast
between them.

A reader who has just met `handleSystemAlert` and then meets `alertHandling` a few lines later must
infer from context that the second name is short for the first one's subject. The two names share no
substring beyond the generic word "alert".

A search for "system alert" in `docs/scenarios.md` today finds `handleSystemAlert` and the prose that
introduces `alertHandling`. It does not find the field's own key. An author skims the field table, or
greps the docs for the term the sibling step uses, and misses the field.

`systemAlertHandling` closes that gap the same way
[BE-0317](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling.md)
closed the previous one. The name says directly what the surrounding prose already has to explain.

BE-0317 established the pattern this item continues. A scenario-level setting reads as a noun phrase.
The rename breaks no existing scenario, config, or command line, because the previous spelling stays
as a deprecated alias.

This item applies the same move to the same field a second time, under the same standard: a reader
should learn what a field does from its name, not from a paragraph beside it.

## Detailed design

This section works at proposal altitude. The work is MECE along the units below. The guiding
constraint carries over unchanged from BE-0317: no existing scenario, config, or command line may
break.

Every renamed surface keeps both older spellings as accepted, deprecated aliases. These are
`alertHandling` (BE-0317's canonical name) and `dismissAlerts` (the original name BE-0317 replaced).
A three-deep alias chain resolves to one guard.

The canonical spelling and both deprecated aliases parse to the same guard, so the following three
scenarios are equivalent:

```yaml
# canonical (new)
- name: grant the notification prompt
  systemAlertHandling: { instruction: "tap Allow" }
  steps: [ ... ]

# deprecated alertHandling alias (BE-0317's former canonical name) — still accepted, emits a
# one-time deprecation notice
- name: grant the notification prompt
  alertHandling: { instruction: "tap Allow" }
  steps: [ ... ]

# deprecated dismissAlerts alias (the original pre-BE-0317 name) — still accepted, emits a
# one-time deprecation notice
- name: grant the notification prompt
  dismissAlerts: { instruction: "tap Allow" }
  steps: [ ... ]

# the bare-boolean form is unchanged; systemAlertHandling: false turns the guard off for one scenario
```

The CLI mirrors the rename. `bajutsu run --system-alert-handling` / `--no-system-alert-handling` is
the canonical flag. `--alert-handling` / `--no-alert-handling` and `--dismiss-alerts` /
`--no-dismiss-alerts` keep working as hidden, deprecated aliases.

- **Scenario schema.** Rename the `AlertHandling` model and the `Scenario.alert_handling` field to
  `SystemAlertHandling` and `system_alert_handling`. The YAML key `systemAlertHandling` becomes the
  canonical alias. `alertHandling` and `dismissAlerts` stay as additional accepted input aliases
  (Pydantic `AliasChoices`), so a scenario written in any of the three spellings parses. A dumped
  scenario emits the new `systemAlertHandling` key. The two on-disk forms (bare boolean, or
  `{ instruction: "..." }`) are unchanged.
- **Config-default surface.** The app-level default lives in the target config today. The code is in
  `bajutsu/config/schema.py`, surfaced through `bajutsu/config/effective.py` and
  `bajutsu/config/resolve.py`, under the `alertHandling` key. Rename the key to
  `systemAlertHandling` the same way. `alertHandling` and `dismissAlerts` stay as accepted aliases.
- **CLI flags.** Make `--system-alert-handling` / `--no-system-alert-handling` the canonical flag on
  all three commands that carry it today: `run`, `record`, and `crawl`. Keep `--alert-handling` /
  `--no-alert-handling` and `--dismiss-alerts` / `--no-dismiss-alerts` as hidden, deprecated aliases
  that map to the same option, so existing invocations and CI keep working unchanged.
  `--alert-instruction` (also on all three) already reads as alert-neutral, so it stays as is. Update
  the `run` capability's `claude_flag` (`bajutsu/capabilities.py`) to the canonical
  `--system-alert-handling` spelling.
- **Serve request flag.** The request-body flag reader in `bajutsu/serve/operations/dispatch.py`
  accepts the canonical `systemAlertHandling` JSON key first. It falls back to `alertHandling`, then
  to `dismissAlerts`, the same three-deep chain as the scenario and config aliases. A saved frontend
  state or a third-party `/api/run` client keeps working. The serve UI templates
  (`bajutsu/templates/serve.*.mjs`) send the canonical key.
- **Deprecation signal.** Emit a one-time deprecation notice when the `alertHandling` or
  `dismissAlerts` key, or the `--alert-handling` / `--dismiss-alerts` flag, is used. The notice points
  to the new name. The notice is a log line on the authoring and CLI path, and never appears on the
  deterministic `run` verdict path (prime directive 1). It never changes the run's outcome, because
  the aliases behave identically to the canonical name.
- **Docs.** Rename every documented mention across `docs/` and its `docs/ja/` mirror to
  `systemAlertHandling`. Add a short note that `alertHandling` and `dismissAlerts` are the accepted
  deprecated aliases. Renaming the `scenarios.md` section heading changes its slug, so the anchor
  links that point at it are updated in the same change. These links are `scenarios.md`'s own
  field-table `[below]` link, `cli.md`, and `recording.md`, in both languages.
- **Demos.** Update the showcase's own scenario files, config, and comments to the canonical
  spelling: `demos/showcase/scenarios/*.yaml`, `demos/showcase/showcase.config.yaml`, and the CI
  workflow comments in `.github/workflows/ios-e2e.yml`. The repository's own fixtures then dogfood
  the new name rather than the deprecated alias.
- **Tests.** Cover these cases: the canonical `systemAlertHandling` and both the `alertHandling` and
  `dismissAlerts` aliases parse to the same model; the config default works under any of the three
  keys; all three CLI flag spellings work on each of `run`, `record`, and `crawl`; a dump emits the
  new key; the serve request-flag fallback chain works; and the deprecation notice fires on either
  old spelling.

## Alternatives considered

- **Keep `alertHandling` and rely on the docs to draw the contrast.** `docs/scenarios.md` already
  places `systemAlertHandling` (then `alertHandling`) and `handleSystemAlert` next to each other. It
  explains the reactive-versus-proactive difference in prose. That helps a reader who reads the
  surrounding paragraph. It does nothing for a reader who sees the field name alone, in a field
  table, in an error message, or in a `grep` for "system alert" across the docs. Rejected as
  insufficient, for the reason BE-0317 used on the equivalent alternative.
- **Rename with no alias (a hard break).** This is simpler in the code. But it breaks every existing
  scenario, target config, and CI command line that names `alertHandling` / `--alert-handling`, and
  transitively anything still on the older `dismissAlerts` spelling. Rejected: the accuracy gain does
  not justify breaking users, and a three-deep alias chain makes the break unnecessary.
- **Pick a different name (`systemAlert`, `alertGuard`).** `systemAlert` drops "handling". It reads
  as a noun naming the alert itself, rather than the setting that handles it. That is closer to what
  a field *is* than what it *does*. `alertGuard` matches the "system-alert guard" phrasing used in
  prose elsewhere. But it does not pair grammatically with `handleSystemAlert` the way a
  `systemAlert…` / `handleSystemAlert` pair would. That pair would start with the same words in a
  different order, which reads as more confusable, not less. `systemAlertHandling` was chosen because
  it is `alertHandling` with the one word inserted that the sibling step's name already establishes
  as the vocabulary for this class of prompt.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Scenario schema — `SystemAlertHandling` / `systemAlertHandling`, `alertHandling` and
      `dismissAlerts` kept as input aliases.
- [x] Config-default surface — `systemAlertHandling` key with `alertHandling` / `dismissAlerts`
      aliases.
- [x] CLI flags — `--system-alert-handling` canonical on `run`/`record`/`crawl`, `--alert-handling`
      and `--dismiss-alerts` hidden deprecated aliases; capability `claude_flag`.
- [x] Serve request flag — `systemAlertHandling` canonical JSON key, `alertHandling` /
      `dismissAlerts` fallback chain; UI templates send the canonical key.
- [x] Deprecation signal on either old key / flag (authoring / CLI path only).
- [x] Docs — rename every mention across `docs/` + `docs/ja/`, fix the anchor links the heading-slug
      change breaks, note both aliases, keep the `handleSystemAlert` contrast.
- [x] Demos — showcase scenarios/config/CI comments use the canonical spelling.
- [x] Tests — all three spellings parse, config default, all three CLI flags, dump emits new key,
      serve fallback chain, deprecation notice.

## References

- [BE-0317 — Rename the dismissAlerts guard to alertHandling to match its grant-or-dismiss behavior](../BE-0317-rename-dismiss-alerts-to-alert-handling/BE-0317-rename-dismiss-alerts-to-alert-handling.md) —
  the precedent this item follows one step further: same alias-preserving rename pattern, same field.
- [BE-0316 — Explicit mid-flow step for iOS permission-prompt alerts](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) —
  the sibling `handleSystemAlert` step whose name motivates this rename; the two are told apart by
  part of speech (verb action versus noun setting) once both name "system alert" explicitly.
- `bajutsu/scenario/models/scenario.py` (`SystemAlertHandling`), `bajutsu/config/schema.py`,
  `bajutsu/config/effective.py`, `bajutsu/config/resolve.py`, `bajutsu/cli/_shared.py`,
  `bajutsu/cli/commands/run.py`, `bajutsu/cli/commands/record.py`, `bajutsu/cli/commands/crawl.py`,
  `bajutsu/capabilities.py`, `bajutsu/serve/operations/dispatch.py`,
  `bajutsu/templates/serve.*.mjs` — the surfaces the rename touches.
