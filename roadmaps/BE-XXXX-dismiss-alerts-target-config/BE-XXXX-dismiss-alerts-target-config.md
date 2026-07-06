**English** · [日本語](BE-XXXX-dismiss-alerts-target-config-ja.md)

# BE-XXXX — Move dismissAlerts control into config; drop the CLI flags

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-dismiss-alerts-target-config.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

Make config the single source of the system-alert guard's setting: add a per-target
`dismissAlerts` field to `targets.<name>`, and **remove the run-wide CLI flags**
(`--dismiss-alerts` / `--no-dismiss-alerts` and `--alert-instruction`). After this, the
guard is controlled only by checked-in config and per-scenario `dismissAlerts` — a thinner
CLI and a run whose alert behavior is fully determined by committed files.

## Motivation

The system-alert guard (the vision-based dismissal of OS prompts idb cannot see or tap) is
configurable today through exactly two layers:

- **Per scenario** — the scenario YAML's `dismissAlerts`, in its rich form
  (`false`, or `{ enabled, instruction }`).
- **Run-wide** — the CLI `--dismiss-alerts` / `--no-dismiss-alerts` (a boolean that
  overrides every scenario) and `--alert-instruction` (the default button label).

Two problems, pulling the same way — toward config, away from the CLI:

- **The config layer is missing, and alert behavior is an app property.** Neither
  `targets.<name>` nor `Defaults` carries a `dismissAlerts`. But an app that always shows an
  App Tracking Transparency prompt, a notifications prompt, or a "Save Password?" dialog
  wants the same handling — the same button label ("Allow", "許可", "OK") — across *every*
  scenario. That default belongs in `targets.<name>`, exactly as prime directive #3
  ("app-agnostic — per-app differences live in config") prescribes.
- **A run-wide CLI toggle undermines reproducibility.** When behavior can be flipped by a
  transient flag, "the same test" gives different results depending on the flags someone
  passed. Prime directive #2 (determinism first) points the other way: a run should be
  determined by its committed config and scenarios, not by invocation-time flags. The CLI
  should stay thin, and *test-behavior* control (what happens during the run) should live in
  config — distinct from *debugging/presentation* affordances like `--headed` (how you watch
  a run), which may reasonably remain flags. `dismissAlerts` is squarely test behavior, so it
  belongs in config.

Removing the flags loses nothing essential. The "force the guard off for a Claude-free CI
run" case does not need `--no-dismiss-alerts`: the guard already no-ops when no AI credential
is present, so withholding the key achieves it. A genuinely one-off change is made by editing
the target (or scenario) — the same checked-in state everything else about the run comes from.

This is a configuration-sourcing change plus a CLI-surface reduction. The guard still reaches
the AI provider only as a `BlockedHandler` for interstitial prompts, never on the `run`/CI
verdict path, so prime directive #1 is untouched; the credential requirement is unchanged.

## Detailed design

1. **Config schema.** Add `dismiss_alerts` (alias `dismissAlerts`) to `TargetConfig` in
   [`bajutsu/config.py`](../../bajutsu/config.py), accepting the same on-disk shape as the
   scenario field: the bare-boolean shorthand (`dismissAlerts: false`) coerced to
   `{ enabled: <bool> }`, or the object form `{ enabled, instruction }`. Decide whether to
   reuse the scenario `DismissAlerts` model
   ([`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)) or
   mirror a small config-side model, avoiding a config→scenario import cycle; whichever is
   chosen, the two must stay shape-compatible.
2. **Precedence / effective resolution.** With no CLI layer, resolve in most-specific-wins
   order: `per-scenario > target config > built-in default (on, default instruction)`. A
   scenario with no `dismissAlerts` inherits the target's; a scenario that sets it keeps its
   own. Apply this where the run prepares scenarios and builds the guard, near
   `_alert_guard_factory` in [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py).
3. **Remove the CLI flags.** Drop `--dismiss-alerts` / `--no-dismiss-alerts` and
   `--alert-instruction` from `bajutsu run`, and delete `_apply_dismiss_alerts` (the run-wide
   override helper). Fold the instruction resolution into the scenario → target → built-in
   `default_instruction` chain in `_alert_guard_factory`. This is a breaking CLI change.
4. **serve / flag-mirror.** If serve surfaces these flags (it mirrors run flags, BE-0134),
   remove them there too so the two surfaces stay in step.
5. **Docs & migration note.** Document the new config field (English + `docs/ja/`) and the
   flag removal (what to write in `targets.<name>` / a scenario instead). Update
   [`DESIGN.md`](../../DESIGN.md) / [`docs/architecture.md`](../../docs/architecture.md)
   where they describe the guard's setting source. The credential requirement (AI provider
   key, per BE-0047 / BE-0053) is unchanged.
6. **Tests.** Config parsing of both on-disk forms (bare bool + object); precedence tests
   proving `scenario > target > default` for both `enabled` and `instruction`; remove or
   retarget the tests that exercised the deleted CLI flags.

## Alternatives considered

- **Keep the CLI flags as run-scoped overrides alongside config** (the `--headed` /
  `headless` pattern: config holds the default, a flag overrides it for one run). Rejected: a
  transient flag that changes run behavior works against reproducibility (prime directive #2)
  and grows both the CLI surface and the serve flag-mirror (BE-0134). Unlike `--headed`, which
  is a debugging/presentation affordance, `dismissAlerts` is test behavior, so its control
  belongs in checked-in config, not an invocation-time flag.
- **Deprecate the flags first, remove them later.** Considered; chose immediate removal for a
  single, unambiguous surface rather than carrying a deprecated flag whose only role is to
  duplicate config. The migration is a one-line move from `--alert-instruction "Allow"` (or
  `--no-dismiss-alerts`) to a `dismissAlerts` entry in `targets.<name>`.
- **A global `Defaults.dismissAlerts` instead of / in addition to per-target.** Rejected as
  the primary home: alert button labels are app- and locale-specific, so the natural key is
  the target, not a cross-app default. A `Defaults` field could be added later if a genuine
  all-apps default emerges, but it is not needed here and would add another precedence layer.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add `dismissAlerts` to `TargetConfig` (schema + bare-bool coercion).
- [ ] Resolve precedence `scenario > target > default` in the run path.
- [ ] Remove `--dismiss-alerts` / `--no-dismiss-alerts` / `--alert-instruction` and `_apply_dismiss_alerts`; fold instruction into the default chain.
- [ ] Remove the mirrored flags from serve if present (BE-0134).
- [ ] Document the field + flag removal (English + Japanese); update DESIGN.md / architecture.md if affected.
- [ ] Tests for parsing and precedence; drop/retarget the CLI-flag tests.

## References

- CLI flag & guard: [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)
  (`_apply_dismiss_alerts`, `_alert_guard_factory`).
- Scenario model: [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)
  (`DismissAlerts`).
- Config model: [`bajutsu/config.py`](../../bajutsu/config.py) (`TargetConfig`, `Defaults`).
- Flag/config precedent: [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)
  (`_with_headed`, over `TargetConfig.headless`).
- Related: BE-0134 (serve ↔ CLI flag-mirror drift), BE-0047 (AI data sovereignty /
  redaction), BE-0104 / BE-0053 (vendor-neutral AI provider).
