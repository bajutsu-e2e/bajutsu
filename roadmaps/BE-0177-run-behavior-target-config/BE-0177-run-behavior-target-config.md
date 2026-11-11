**English** · [日本語](BE-0177-run-behavior-target-config-ja.md)

# BE-0177 — Per-target config defaults for run-behavior settings

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0177](BE-0177-run-behavior-target-config.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0177") |
| Implementing PR | [#715](https://github.com/bajutsu-e2e/bajutsu/pull/715) |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

Bajutsu will add a per-target config layer for run-behavior settings. Today these settings can be
set only per scenario, or overridden run-wide by a CLI flag. They are the system-alert guard
(`dismissAlerts` plus its instruction), `erase`, and `network` collection. Each gains a
`targets.<name>` field that supplies the app-level default.

The existing per-scenario forms and the CLI flags both stay. Each setting resolves as
`CLI flag > per-scenario > target config > built-in default`. The codebase already layers `--headed`
/ `headless` for browser visibility the same way. The change is a non-breaking addition of the
missing config layer.

## Motivation

Several `bajutsu run` settings govern what happens during a run. They are the alert guard
(`--dismiss-alerts` / `--no-dismiss-alerts`, `--alert-instruction`), state erasure (`--erase` /
`--no-erase`), and network collection (`--network` / `--no-network`). A scenario can set them, or a
flag can override them for the whole run, but they have **no per-app default in config**. Neither
`targets.<name>` nor `Defaults` carries them.

- **These defaults are app properties.** Three questions have one answer per app, the same across
  every scenario: which alert button to tap ("Allow", "許可"), whether to erase state before a run,
  and whether network collection is even possible on this target. Prime directive #3 (app-agnostic,
  per-app differences live in config) puts them in `targets.<name>`. Without that layer, you repeat
  the setting in every scenario or pass the flag on every invocation.
- **It closes an asymmetry.** `--headed` and `--browser` already layer over the
  `TargetConfig.headless` / `browser` config fields. Config holds the app default, and the flag
  overrides it for one run. `--dismiss-alerts` / `--erase` / `--network` have no such config
  counterpart. Adding one makes the whole run-flag surface consistent.

The CLI flags stay, deliberately. They remain the run-scoped override for a one-off CI run, for
debugging, and for the serve Web UI's Replay/Record/Crawl checkboxes, which map to these flags.
Keeping both is the established `--headed` model. The change is non-breaking, and the serve Web UI
keeps working unchanged (see *serve Web UI* below).

Reproducibility (prime directive #2) is preserved. A run is deterministic given its inputs: config,
scenarios, and the flags passed. Serve and CI record all three. Prime directive #1 holds. The alert
guard still reaches the AI provider only as a `BlockedHandler`, never on the `run`/CI verdict path.

## Detailed design

1. **Config schema.** Add three fields to `TargetConfig` in
   [`bajutsu/config.py`](../../bajutsu/config.py):
   - `dismissAlerts` has the same on-disk shape as the scenario field. It takes the bare-boolean
     shorthand (`false` → `{ enabled: false }`) or the object form `{ enabled, instruction }`. Reuse
     the scenario `DismissAlerts` model
     ([`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)), or mirror
     a shape-compatible config-side model that avoids a config→scenario import cycle.
   - `erase` (bool) is the per-target default for `preconditions.erase`.
   - `network` (bool) says whether to collect the app's network exchanges. The built-in default is
     on, matching today's flag default.
2. **Precedence / effective resolution.** Resolve most-specific-wins:
   `CLI flag > per-scenario > target config > built-in default`. The target config supplies the
   default that a scenario inherits when it sets nothing. A scenario that sets the value keeps its
   own. The CLI flag still overrides for the whole run.

   Layer this where the run prepares scenarios and builds the guard, next to `_apply_dismiss_alerts`
   / `_apply_erase` / `_alert_guard_factory` in
   [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py). It then reads like the
   `--headed` / `headless` precedent in [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)
   (`_with_headed`). `network` has no per-scenario boolean today, so it resolves
   `CLI flag > target config > built-in default`.
3. **Instruction default.** The target's `instruction` slots into the existing `default_instruction`
   chain in `_alert_guard_factory`. It sits below a per-scenario instruction and below the CLI
   `--alert-instruction`.
4. **serve Web UI.** No removal is needed. The flags and the Replay/Record/Crawl checkboxes for
   erase (`#erasedev`) and dismiss-alerts (`#nodismiss`) keep working.
   [`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py) mirrors the flags, and
   [`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) /
   [`serve.js`](../../bajutsu/templates/serve.js) hold the controls.

   The target config default now applies *beneath* an unset checkbox. A serve run of a bundle whose
   target sets `dismissAlerts` gets that default without the user toggling anything. Verify that the
   layering holds through serve. Nothing needs deleting.
5. **Docs.** Document the new config fields (English + `docs/ja/`) and the full precedence order.
   Update [`DESIGN.md`](../../DESIGN.md) and [`docs/architecture.md`](../../docs/architecture.md)
   where they describe where these settings are sourced. The AI-provider credential rule (BE-0047 /
   BE-0053) stays as it is.
6. **Tests.** Test config parsing of each new field, including `dismissAlerts`' two on-disk forms.
   Add precedence tests that prove `CLI > scenario > target > default` for each setting.

## Alternatives considered

- **Move control fully into config and remove the CLI flags** (an earlier framing of this item).
  Rejected, for three reasons:
  - It is a breaking CLI change.
  - It kills the serve Web UI's erase / dismiss-alerts checkboxes. Serve opens config *read-only*,
    so a serve user would lose the ability to set these at all until a separate config-editing UI is
    built.
  - It makes these settings inconsistent with `--headed` / `--browser`, which keep both flag and
    config.

  The reproducibility gain is marginal. A run is already deterministic given its recorded inputs
  (config + scenarios + flags). A config default plus an explicit flag override is a clean,
  well-understood model, and it is the one `--headed` uses.
- **A global `Defaults.dismissAlerts` / `erase` / `network` instead of per-target.** Rejected as the
  primary home. These settings are app-specific, so the natural key is the target. A `Defaults`
  field could be added later if a genuine all-apps default emerges.
- **Scope this to `--dismiss-alerts` alone.** Rejected. `--erase` has the same "override every
  scenario's X" shape, and `--network` is the same category. Adding the config layer for all three
  at once is one coherent change, not three near-identical ones.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `dismissAlerts`, `erase`, `network` to `TargetConfig` and resolve them onto `Effective`
      (schema + `dismissAlerts` coercion).
- [x] Resolve precedence `CLI > scenario > target > default` for each in the run path (`erase` via
      `_filter_scenarios`, guard `enabled` in `_alert_guard_factory`, `network` in `run`). Made
      `Preconditions.erase` and `--network` `bool | None` so "unset" falls through.
- [x] Fold the target `instruction` into the `default_instruction` chain.
- [x] Verify the layering holds through serve (flags + checkboxes unchanged; config default applies
      beneath — no serve code change needed).
- [x] Document the config fields + precedence (English + Japanese `configuration.md` / `cli.md`);
      DESIGN.md / architecture.md unaffected.
- [x] Tests for parsing and precedence.

**Log**

- [#715](https://github.com/bajutsu-e2e/bajutsu/pull/715) — implemented the per-target config layer
  for `dismissAlerts` / `erase` / `network` with `flag > scenario > target > default` precedence,
  plus config-parse and precedence tests and the bilingual doc updates.

## References

- Run flags & guard: [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)
  (`_apply_dismiss_alerts`, `_apply_erase`, `_alert_guard_factory`).
- Scenario models:
  [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)
  (`DismissAlerts`, `Preconditions`).
- Config model: [`bajutsu/config.py`](../../bajutsu/config.py) (`TargetConfig`, `Defaults`).
- Flag/config precedent: [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)
  (`_with_headed`, over `TargetConfig.headless`) — the same layering this item adds.
- serve surfacing: [`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py) (flag
  mirror), [`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) /
  [`serve.js`](../../bajutsu/templates/serve.js) (the erase / dismiss-alerts checkboxes).
- Related: BE-0134 (serve ↔ CLI flag-mirror), BE-0047 (AI data sovereignty / redaction),
  BE-0104 / BE-0053 (vendor-neutral AI provider).
