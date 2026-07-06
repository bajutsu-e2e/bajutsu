**English** · [日本語](BE-XXXX-dismiss-alerts-target-config-ja.md)

# BE-XXXX — Set dismissAlerts per target in config

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

Add a per-target `dismissAlerts` field to `targets.<name>` config so the system-alert
guard's default — whether it is on, and which button it taps — can be set once per app,
filling the missing middle layer between the per-scenario `dismissAlerts` and the run-wide
CLI `--dismiss-alerts` override.

## Motivation

The system-alert guard (the vision-based dismissal of OS prompts idb cannot see or tap) is
configurable today through exactly two layers:

- **Per scenario** — the scenario YAML's `dismissAlerts`, in its rich form
  (`false`, or `{ enabled, instruction }`).
- **Run-wide** — the CLI `--dismiss-alerts` / `--no-dismiss-alerts` (a boolean that
  overrides every scenario) and `--alert-instruction` (the default button label).

The **config layer is missing entirely**: neither `targets.<name>` nor `Defaults` carries a
`dismissAlerts`. That is the wrong shape for the data:

- **Alert behavior is an app property, not a scenario property.** An app that always shows
  an App Tracking Transparency prompt, a notifications prompt, or a "Save Password?" dialog
  wants the same handling — the same button label ("Allow", "許可", "OK") — across *every*
  scenario. Under the current design that means either repeating
  `dismissAlerts: { instruction: "Allow" }` in every scenario file, or remembering to pass
  `--alert-instruction "Allow"` on every invocation. This is precisely the per-app
  duplication prime directive #3 ("app-agnostic — per-app differences live in config") says
  belongs in `targets.<name>`.
- **It is asymmetric with the existing flag/config story.** `--headed` layers over the
  `TargetConfig.headless` config field, so the browser-visibility default lives in config
  and the flag overrides it for one run. `--dismiss-alerts` has no such config counterpart.
  Closing the gap makes the two behave consistently.

This is a pure configuration-sourcing change. The guard still reaches the AI provider only
as a `BlockedHandler` for interstitial prompts, never on the `run`/CI verdict path, so
prime directive #1 is untouched; the credential requirement is unchanged.

## Detailed design

1. **Config schema.** Add `dismiss_alerts` (alias `dismissAlerts`) to `TargetConfig` in
   [`bajutsu/config.py`](../../bajutsu/config.py), accepting the same on-disk shape as the
   scenario field: the bare-boolean shorthand (`dismissAlerts: false`) coerced to
   `{ enabled: <bool> }`, or the object form `{ enabled, instruction }`. Decide whether to
   reuse the scenario `DismissAlerts` model
   ([`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)) or
   mirror a small config-side model, avoiding a config→scenario import cycle; whichever is
   chosen, the two must stay shape-compatible.
2. **Precedence / effective resolution.** When the run prepares scenarios and builds the
   guard, resolve in most-specific-wins order:
   `CLI > per-scenario > target config > built-in default (on, default instruction)`.
   Concretely, a scenario with no `dismissAlerts` inherits the target's; a scenario that
   sets it keeps its own; the CLI `--dismiss-alerts` / `--no-dismiss-alerts` still overrides
   the `enabled` bit for the whole run. Layer this next to `_apply_dismiss_alerts` /
   `_alert_guard_factory` in [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)
   so the ordering is explicit and reads like the `--headed` / `headless` precedent in
   [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py) (`_with_headed`).
3. **Instruction default.** The target's `instruction` becomes the per-app default button
   label, slotting into the existing `default_instruction` chain in `_alert_guard_factory`
   below a per-scenario instruction and below the CLI `--alert-instruction`.
4. **Docs.** Document the new field in the config reference (English + `docs/ja/`), and
   update [`DESIGN.md`](../../DESIGN.md) / [`docs/architecture.md`](../../docs/architecture.md)
   if they describe where the guard's setting is sourced. Note that the credential
   requirement (AI provider key, per BE-0047 / BE-0053) is unchanged.
5. **Tests.** Config parsing of both on-disk forms (bare bool + object); precedence tests
   proving `CLI > scenario > target > default` for both the `enabled` bit and the
   `instruction`.

## Alternatives considered

- **A global `Defaults.dismissAlerts` instead of / in addition to per-target.** Rejected as
  the primary home: alert button labels are app- and locale-specific, so the natural key is
  the target, not a cross-app default. A `Defaults` field could be added later if a genuine
  all-apps default emerges, but it is not needed to close this gap and would add a fourth
  precedence layer for little benefit.
- **Leave it CLI-only.** Rejected: it forces per-invocation flags or per-scenario
  duplication for what is inherently an app property — the exact duplication config exists to
  remove.
- **A new general precedence engine for run options.** Overkill; this reuses the established
  `--headed` / `headless` layering pattern rather than inventing machinery.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add `dismissAlerts` to `TargetConfig` (schema + bare-bool coercion).
- [ ] Resolve precedence `CLI > scenario > target > default` in the run path.
- [ ] Fold the target `instruction` into the `default_instruction` chain.
- [ ] Document the field (English + Japanese); update DESIGN.md / architecture.md if affected.
- [ ] Tests for parsing and precedence.

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
