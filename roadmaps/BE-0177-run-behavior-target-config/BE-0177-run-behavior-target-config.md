**English** · [日本語](BE-0177-run-behavior-target-config-ja.md)

# BE-0177 — Per-target config defaults for run-behavior settings

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0177](BE-0177-run-behavior-target-config.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0177") |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

Add a per-target config layer for the run-behavior settings that today can only be set
per-scenario or overridden run-wide by a CLI flag: the system-alert guard
(`dismissAlerts` + its instruction), `erase`, and `network` collection. Each gains a
`targets.<name>` field supplying the app-level default. The existing per-scenario forms and
the CLI flags both stay — the setting resolves `CLI flag > per-scenario > target config >
built-in default`, exactly the `--headed` / `headless` layering the codebase already uses for
browser visibility. This is a non-breaking addition of the missing config layer.

## Motivation

Several `bajutsu run` settings that govern what happens during a run — the alert guard
(`--dismiss-alerts` / `--no-dismiss-alerts`, `--alert-instruction`), state erasure
(`--erase` / `--no-erase`), and network collection (`--network` / `--no-network`) — can be
set per scenario or overridden for the whole run by a flag, but have **no per-app default in
config**. Neither `targets.<name>` nor `Defaults` carries them.

- **These defaults are app properties.** The alert button to tap ("Allow", "許可"), whether
  to erase state before a run, whether network collection is even possible on this target —
  these are properties of the app under test, the same across every scenario. Prime directive
  #3 (app-agnostic — per-app differences live in config) puts them in `targets.<name>`.
  Without that layer, you repeat the setting in every scenario, or pass the flag on every
  invocation.
- **It closes an asymmetry.** `--headed` and `--browser` already layer over the
  `TargetConfig.headless` / `browser` config fields: config holds the app default, the flag
  overrides it for one run. `--dismiss-alerts` / `--erase` / `--network` have no such config
  counterpart. Adding it makes the whole run-flag surface consistent.

The CLI flags stay, deliberately. They remain the run-scoped override — for a one-off CI
run, for debugging, and for the serve Web UI's Replay/Record/Crawl checkboxes, which map to
these flags. Keeping both is the established `--headed` model, so the change is non-breaking
and the serve Web UI keeps working unchanged (see *serve Web UI* below). Reproducibility
(prime directive #2) is preserved: a run is deterministic given its inputs — config,
scenarios, and the flags passed — which serve and CI record. Prime directive #1 is untouched:
the alert guard still reaches the AI provider only as a `BlockedHandler`, never on the
`run`/CI verdict path.

## Detailed design

1. **Config schema.** Add to `TargetConfig` in
   [`bajutsu/config.py`](../../bajutsu/config.py):
   - `dismissAlerts` — the same on-disk shape as the scenario field: bare-boolean shorthand
     (`false` → `{ enabled: false }`) or the object form `{ enabled, instruction }`. Reuse
     the scenario `DismissAlerts` model
     ([`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)) or
     mirror a shape-compatible config-side model, avoiding a config→scenario import cycle.
   - `erase` (bool) — the per-target default for `preconditions.erase`.
   - `network` (bool) — whether to collect the app's network exchanges (built-in default on,
     matching today's flag default).
2. **Precedence / effective resolution.** Resolve most-specific-wins:
   `CLI flag > per-scenario > target config > built-in default`. The target config supplies
   the default a scenario inherits when it sets nothing; a scenario that sets the value keeps
   its own; the CLI flag still overrides for the whole run. Layer this where the run prepares
   scenarios and builds the guard, next to `_apply_dismiss_alerts` / `_apply_erase` /
   `_alert_guard_factory` in [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py),
   so it reads like the `--headed` / `headless` precedent in
   [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py) (`_with_headed`). (`network` has no
   per-scenario boolean today; it resolves `CLI flag > target config > built-in default`.)
3. **Instruction default.** The target's `instruction` slots into the existing
   `default_instruction` chain in `_alert_guard_factory`, below a per-scenario instruction and
   below the CLI `--alert-instruction`.
4. **serve Web UI.** No removal needed: the flags and the Replay/Record/Crawl checkboxes for
   erase (`#erasedev`) and dismiss-alerts (`#nodismiss`) keep working
   ([`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py) mirrors the flags;
   [`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) /
   [`serve.js`](../../bajutsu/templates/serve.js) hold the controls). The target config default
   now applies *beneath* an unset checkbox, so a serve run of a bundle whose target sets
   `dismissAlerts` gets that default without the user toggling anything. Verify the layering
   holds through serve; nothing to delete.
5. **Docs.** Document the new config fields (English + `docs/ja/`) and the full precedence
   order. Update [`DESIGN.md`](../../DESIGN.md) /
   [`docs/architecture.md`](../../docs/architecture.md) where they describe where these
   settings are sourced. The AI-provider credential requirement (BE-0047 / BE-0053) is
   unchanged.
6. **Tests.** Config parsing of each new field (including `dismissAlerts`' two on-disk forms);
   precedence tests proving `CLI > scenario > target > default` for each setting.

## Alternatives considered

- **Move control fully into config and remove the CLI flags** (an earlier framing of this
  item). Rejected: it is a breaking CLI change, and it kills the serve Web UI's erase /
  dismiss-alerts checkboxes — serve opens config *read-only*, so a serve user would lose the
  ability to set these at all until a separate config-editing UI is built. It also makes these
  settings inconsistent with `--headed` / `--browser`, which keep both flag and config. The
  reproducibility gain is marginal: a run is already deterministic given its recorded inputs
  (config + scenarios + flags), so a config default plus an explicit flag override is a clean,
  well-understood model — the same one `--headed` uses.
- **A global `Defaults.dismissAlerts` / `erase` / `network` instead of per-target.** Rejected
  as the primary home: these are app-specific, so the natural key is the target. A `Defaults`
  field could be added later if a genuine all-apps default emerges.
- **Scope this to `--dismiss-alerts` alone.** `--erase` is the same "override every scenario's
  X" shape and `--network` is the same category; adding the config layer for all three at once
  is one coherent change rather than three near-identical ones.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add `dismissAlerts`, `erase`, `network` to `TargetConfig` (schema + `dismissAlerts` coercion).
- [ ] Resolve precedence `CLI > scenario > target > default` for each in the run path.
- [ ] Fold the target `instruction` into the `default_instruction` chain.
- [ ] Verify the layering holds through serve (flags + checkboxes unchanged; config default applies beneath).
- [ ] Document the config fields + precedence (English + Japanese); update DESIGN.md / architecture.md if affected.
- [ ] Tests for parsing and precedence.

## References

- Run flags & guard: [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)
  (`_apply_dismiss_alerts`, `_apply_erase`, `_alert_guard_factory`).
- Scenario models: [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)
  (`DismissAlerts`, `Preconditions`).
- Config model: [`bajutsu/config.py`](../../bajutsu/config.py) (`TargetConfig`, `Defaults`).
- Flag/config precedent: [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)
  (`_with_headed`, over `TargetConfig.headless`) — the same layering this item adds.
- serve surfacing: [`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py) (flag
  mirror), [`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) /
  [`serve.js`](../../bajutsu/templates/serve.js) (the erase / dismiss-alerts checkboxes).
- Related: BE-0134 (serve ↔ CLI flag-mirror), BE-0047 (AI data sovereignty / redaction),
  BE-0104 / BE-0053 (vendor-neutral AI provider).
