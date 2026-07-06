**English** · [日本語](BE-XXXX-run-behavior-flags-to-config-ja.md)

# BE-XXXX — Move run-wide test-behavior override flags into config

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-run-behavior-flags-to-config.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

Establish a policy — *test-behavior control lives in checked-in config, not in
invocation-time CLI flags* — and apply it to the `bajutsu run` flags that currently override,
for the whole run, a setting that governs what happens during the run:
`--dismiss-alerts` / `--no-dismiss-alerts`, `--alert-instruction`, `--erase` / `--no-erase`,
and `--network` / `--no-network`. Each moves into `targets.<name>` config (keeping the
per-scenario forms that already exist), and the flags are removed. The CLI keeps only
selection, observation, output, and config-sourcing flags.

## Motivation

`bajutsu run` today mixes two kinds of flag. Some *select or observe* a run — which target
and scenarios to run, whether to watch the browser, where to write output. Others *change
what the run does*: `--erase` / `--no-erase` overrides every scenario's
`preconditions.erase`; `--dismiss-alerts` / `--no-dismiss-alerts` and `--alert-instruction`
override the system-alert guard; `--network` / `--no-network` toggles collecting the app's
network exchanges (the data `request` assertions read). The second kind sits uneasily as a
flag:

- **A run-wide toggle undermines reproducibility.** When behavior can be flipped by a
  transient flag, "the same test" gives different results depending on the flags someone
  passed. Prime directive #2 (determinism first) points the other way: a run should be
  determined by its committed config and scenarios, not by invocation-time flags.
- **Test behavior is often an app property.** The alert button to tap ("Allow", "許可"),
  whether to erase state, whether network collection is even possible on this target — these
  are properties of the app under test. Prime directive #3 (app-agnostic — per-app
  differences live in config) puts them in `targets.<name>`.
- **Fewer flags is a thinner surface.** Each run flag must also be mirrored into serve
  (BE-0134) and documented; removing four of them shrinks that surface.

The line this draws: *test-behavior* control moves to config; *selection* (`--target`,
`--scenario`, `--tag`, `--exclude`, `--backend`, `--udid`, `--workers`, `--browsers`),
*observation/presentation* (`--headed`, `--browser`, `--progress`, `--log-predicate`,
`--log-subsystem`), *output* (`--zip`, `--runs-dir`, `--evidence-store`), and *config
sourcing* (`--config`, `--config-offline`, `--require-pinned-config`, the directory
overrides) stay flags — they pick what runs, how you watch it, where output goes, or how
config itself is loaded, none of which is the run's behavior. `--headed`, notably, stays: it
is a debugging affordance layered over the `headless` config field, not test behavior.

Removing the four flags loses nothing essential. The "force the guard off for a Claude-free
CI run" case does not need `--no-dismiss-alerts`: the guard already no-ops without an AI
credential. A genuinely one-off change is made by editing the target (or scenario) — the same
checked-in state everything else about the run comes from. None of this touches prime
directive #1: the alert guard still reaches the AI provider only as a `BlockedHandler`, never
on the `run`/CI verdict path.

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
2. **Precedence / effective resolution.** With no CLI layer, resolve most-specific-wins:
   `per-scenario > target config > built-in default`. A scenario that does not set the value
   inherits the target's; one that sets it keeps its own. Apply this where the run prepares
   scenarios and builds the guard, in [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py).
   (`network` has no per-scenario boolean today; if one is wanted it can be added, otherwise
   it resolves `target config > built-in default`.)
3. **Remove the flags and helpers.** Drop `--dismiss-alerts` / `--no-dismiss-alerts`,
   `--alert-instruction`, `--erase` / `--no-erase`, and `--network` / `--no-network` from
   `bajutsu run`; delete the run-wide override helpers `_apply_dismiss_alerts` and
   `_apply_erase`; fold the alert instruction into the scenario → target → built-in
   `default_instruction` chain in `_alert_guard_factory`. This is a breaking CLI change.
4. **serve Web UI.** serve builds the run argv through a generic flag-mirror
   ([`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py)) that introspects the
   CLI's option metadata, so once the flags are gone it stops emitting them automatically —
   but the Web UI also has *hand-coded* pieces that must be removed with them. The **Replay /
   Record / Crawl** tabs carry checkboxes for erase (`#erasedev`) and dismiss-alerts
   (`#nodismiss`) in [`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2)
   /  [`serve.js`](../../bajutsu/templates/serve.js), and
   [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py) reads
   `erase` / `dismissAlerts` / `network` / `alertInstruction` from the request body. Delete
   these so nothing dangles (`network` / `alertInstruction` have no UI control today). See the
   Web-UI-control trade-off in *Alternatives considered*.
5. **Docs & migration note.** Document the new config fields (English + `docs/ja/`) and the
   flag removal, with a flag→config migration mapping (e.g. `--alert-instruction "Allow"` →
   `dismissAlerts: { instruction: "Allow" }` in `targets.<name>`; `--no-erase` → `erase:
   false`). Update [`DESIGN.md`](../../DESIGN.md) /
   [`docs/architecture.md`](../../docs/architecture.md) where they describe where these
   settings are sourced. The AI-provider credential requirement (BE-0047 / BE-0053) is
   unchanged.
6. **Tests.** Config parsing of each new field (including `dismissAlerts`' two on-disk forms);
   precedence tests proving `scenario > target > default`; remove or retarget the tests that
   exercised the deleted CLI flags.

## Alternatives considered

- **Keep the flags as run-scoped overrides alongside config** (the `--headed` / `headless`
  pattern: config holds the default, a flag overrides it for one run). Rejected: a transient
  flag that changes run behavior works against reproducibility (prime directive #2) and grows
  both the CLI surface and the serve flag-mirror (BE-0134). Unlike `--headed`, which is a
  debugging/presentation affordance, these four are test behavior, so their control belongs
  in checked-in config.
- **Scope this to `--dismiss-alerts` alone** (the item's first framing). Rejected: `--erase`
  is the exact same "override every scenario's X" shape (its helper `_apply_erase` mirrors
  `_apply_dismiss_alerts`), and `--alert-instruction` / `--network` are the same category.
  Fixing them together states the policy once, rather than repeating the same move per flag.
- **Deprecate the flags first, remove them later.** Considered; chose immediate removal for a
  single, unambiguous surface. The migration is a one-line move into `targets.<name>`.
- **Also fold the selection / observation flags** (`--headed`, `--browser`, the directory
  overrides). Out of scope: those are not test behavior — they select what runs or how you
  observe it, and the ones with config counterparts already coexist with config by design.
- **Keep a per-run behavior toggle in the serve Web UI** (rather than removing the
  erase / dismiss-alerts checkboxes). Rejected: those checkboxes are exactly the transient
  per-run toggle this policy argues against, so the Web UI should read behavior from the
  target config too, not offer its own override. The consequence is real, though — serve
  opens config *read-only* today, so once the checkboxes are gone a serve user sets erase /
  dismiss-alerts in the target config the run uses, not in the UI. Making that config editable
  in the Web UI (to restore in-UI control) belongs to the "Surfacing CLI features in the serve
  Web UI" topic, not here; this item only removes the now-dead controls. `network` /
  `alertInstruction` have no Web UI control today, so they lose nothing.
- **A global `Defaults` field instead of per-target.** Rejected as the primary home: these
  settings are app-specific, so the natural key is the target. A `Defaults` field could be
  added later if a genuine all-apps default emerges.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add `dismissAlerts`, `erase`, `network` to `TargetConfig` (schema + `dismissAlerts` coercion).
- [ ] Resolve precedence `scenario > target > default` for each in the run path.
- [ ] Remove the four flags and `_apply_dismiss_alerts` / `_apply_erase`; fold instruction into the default chain.
- [ ] serve: delete the erase / dismiss-alerts checkboxes (Replay/Record/Crawl) and the request-body reads in `dispatch.py`; the flag-mirror drops the argv automatically (BE-0134).
- [ ] Document the config fields + flag removal with a migration mapping (English + Japanese); update DESIGN.md / architecture.md if affected.
- [ ] Tests for parsing and precedence; drop/retarget the CLI-flag tests.

## References

- Run flags & guard: [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)
  (`_apply_dismiss_alerts`, `_apply_erase`, `_alert_guard_factory`).
- Scenario models: [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)
  (`DismissAlerts`, `Preconditions`).
- Config model: [`bajutsu/config.py`](../../bajutsu/config.py) (`TargetConfig`, `Defaults`).
- Flag/config precedent: [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)
  (`_with_headed`, over `TargetConfig.headless`) — the pattern kept for observation flags.
- serve surfacing: [`bajutsu/serve/_cli_flags.py`](../../bajutsu/serve/_cli_flags.py) (generic
  flag-mirror), [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py)
  (request-body reads), [`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2)
  / [`serve.js`](../../bajutsu/templates/serve.js) (the erase / dismiss-alerts checkboxes).
- Related: BE-0134 (serve ↔ CLI flag-mirror drift), BE-0047 (AI data sovereignty /
  redaction), BE-0104 / BE-0053 (vendor-neutral AI provider).
