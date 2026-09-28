**English** · [日本語](BE-0444-multi-target-evidence-per-target-folders-ja.md)

# BE-0444 — Nest every declared target's evidence under its own folder

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0444](BE-0444-multi-target-evidence-per-target-folders.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0444") |
| Implementing PR | [#2073](https://github.com/bajutsu-e2e/bajutsu/pull/2073) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

A scenario that declares two or more [targets](../../docs/glossary.md#target-app-device)
([BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md))
gets every declared target's own evidence nested under a folder named after that target, inside the
scenario's own evidence directory: `runs/<runId>/<sid>/<target>/…` in place of today's inconsistent
mix, where the *first* declared target ("the primary") writes straight into `<sid>/`, every other
declared target already writes into its own `<sid>/<target>/`, and every declared target's per-step
screenshots and element dumps land in the same flat `<sid>/<stepId>/` folders regardless of which one
produced them. A scenario that declares zero or one target — every scenario in this repository's own
test suite and demos today — keeps its current, unchanged layout.

## Motivation

[BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)
let one scenario interleave steps across several targets — an iOS target and a web target, say —
in a single `bajutsu run` invocation, and gave each target's own scenario-wide recording (video,
device log, application trace) a folder named after that target, so a second target's own
`scenario.mp4` never collided with the first's. That folder convention, though, only ever applies to
every declared target *except* the first one. `_ScenarioRunner._run_on_lease`'s own per-target loop
(`bajutsu/common/runner/pipeline.py`) already writes a non-primary target's recordings and network
capture under `<sid>/<name>/`, but the primary's own recordings and network capture still land
straight under `<sid>/`, alongside `manifest.json`, `junit.xml`, and every other run-level file. The
same run-loop code (`bajutsu/common/orchestrator/loop/_functions.py`) that starts and finishes each
target's own scenario-wide interval recording repeats the same asymmetry: it namespaces every *extra*
target's `scenario.mp4` under its own name, but leaves the primary's flat.

Per-step evidence never got the same convention in the first place. Every step's `before.png`,
`after.png`, and `elements.json` are named from the step's own id alone
(`bajutsu/common/orchestrator/loop/_step_runner.py`'s
`step_id = f"{self.cfg.sid}/{prefix}{step.name or f'step{idx}'}"`, where `prefix` is a `before`/`after`
hook's own phase label, BE-0392), with no target segment at all — a step's global run-order index keeps two different targets' step
folders from colliding by accident, but nothing about the directory layout tells a reader which
target a given `<sid>/<stepId>/` folder belongs to short of opening `manifest.json` and matching the
step's own recorded `target` field (`StepOutcome.target`, BE-0428) against it. A team investigating a
failed cross-target scenario — the exact case BE-0428 exists to support — has to reconstruct that
grouping by hand from the manifest, one step at a time, rather than simply opening the one target's
folder whose steps are failing.

This item removes both gaps at once, by nesting *every* declared target's evidence — the primary
included, at the step level as well as the scenario level — under one folder named after that target.
Once it ships, a scenario declaring `targets: [showcase-app, web]` produces
`runs/<runId>/<sid>/showcase-app/` and `runs/<runId>/<sid>/web/`, each holding that target's own
`scenario.mp4`, `network.json`, and every `<stepId>/` folder its own steps wrote — a reader can point
at either folder and see only that target's own recording, log, and per-step screenshots, with
nothing from the other target mixed in and no detour through the manifest needed to tell them apart.

## Detailed design

### When the new folder applies

The nesting this item adds only ever activates for a scenario declaring **two or more** entries in
`Scenario.targets` — the same condition `_ScenarioRunner._run_on_lease` already tests today, as
`others` (the non-primary leases dict, empty unless the scenario declares a second target) and
equivalently `len(target_runtimes or {}) >= 2` inside `run_scenario`
(`bajutsu/common/orchestrator/loop/_functions.py`). A scenario declaring zero targets (every legacy,
single-target scenario) or exactly one (a self-declaring scenario naming only its primary,
[BE-0436](../BE-0436-primary-target-default/BE-0436-primary-target-default.md)) keeps today's flat
`<sid>/…` layout untouched — there is only ever one target's evidence in either case, so a folder
named after it would add a path segment with nothing to disambiguate. Every reader and writer this
item changes tests the same condition, so a run's directory tree never mixes the two conventions
within one scenario's own evidence.

### Writers: nest every declared target's evidence, primary included

Four write paths change. Three of them, in `bajutsu/common/runner/pipeline.py` and
`bajutsu/common/orchestrator/loop/_functions.py`, already build a *non-primary* target's evidence
path as `f"{sid}/{name}"`; each extends the same treatment to the primary once the scenario declares
a second target, rather than leaving the primary's own path bare `sid`. The fourth, per-step evidence,
gains a target segment for the first time, for every declared target alike:

- `_ScenarioRunner._run_on_lease`'s `VisualContext` for the primary's own scenario-level `expect`
  assertions (`screenshot_path`/`prefix`, today built from bare `sid`).
- `_ScenarioRunner._run_on_lease`'s call to `_write_network` for the primary's own captured traffic
  (today's `prefix = sid if name == primary_name else f"{sid}/{name}"` collapses to always
  `f"{sid}/{name}"` once a second target is declared, `name` covering the primary too).
- `run_scenario`'s own `sink.start_scenario_intervals(sid, …)` / `finish_scenario_intervals(sid, …)`
  calls for the primary's scenario-wide video / device log / application trace.
- `_step_runner.py`'s `step_id` construction, which gains the *routed* step's own target name as a
  path segment ahead of the step name (`f"{self.cfg.sid}/{target}/{prefix}{step.name or f'step{idx}'}"`,
  keeping the existing `prefix` untouched) whenever the scenario's declared-target count is two or
  more — determined from `len(self.by_target)`, the same map `_run_steps` already builds with one
  entry per declared target (primary included, per its own BE-0428 comment). `self.target` (already
  tracked per `_StepRunner`, BE-0428/BE-0438) names the segment; a step nested inside a `web:` block
  still carries no target of its own and inherits the enclosing block's runner, so it lands under
  that runner's own target folder exactly as every other step it shares a runner with does.

`driver_trace.json` (the optional `--trace` diagnostic,
`bajutsu/common/runner/pipeline.py`'s `run_one`) and the crash-diagnostics directory
(`_write_crash_artifacts`, `_CRASH_DIAGNOSTICS_DIR`) are deliberately **not** touched — see *Not
doing* below.

### Readers: two call sites assumed a fixed, un-nested depth

Everywhere else that serves or scans recorded evidence — `bajutsu/common/report/`'s manifest, JUnit,
Common Test Report Format (CTRF), and HTML generation; `report.html.j2`'s asset links; serve's
general `/runs/{path}` artifact route; `RunArtifactWriter` itself — reads or writes each artifact's
already-composed relative name verbatim, so none of it assumes a fixed folder depth and none of it
needs to change for this item. Two call sites do assume a fixed depth and must change together with
the writers above:

- `bajutsu/serve/operations/reads.py`'s `_step_artifacts()` (the Author → Edit step picker,
  [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)) rebuilds a step's id from
  the scenario's own loaded YAML — `f"{sid}/{step.name or f'step{idx}'}"` — to look its recorded
  artifacts up by that key, rather than reading the id back off a recorded artifact name. It gains
  the same target segment the writer now produces, read from the step's own `resolved_target`
  property (`Step`, BE-0436) — the writer names its own segment from the live `_StepRunner`'s
  `self.target` instead, which this static, run-less reader has no equivalent of — once the
  scenario's own `targets` field names two or more entries. A multi-target run recorded before this
  item shipped still has its evidence at the old flat id, so a nested-key lookup that misses falls
  back to the flat one rather than assuming every stored run already matches the new convention —
  safe because the writer never produces a flat id for a multi-target run going forward, so the two
  keys never both name a real, different step. The Play/Replay step list this same function seeds
  stays a flat, run-less list unaffected by any of this (BE-0262).
- `bajutsu/analysis/coverage/_functions.py`'s `_evidence_files()` and
  `bajutsu/analysis/cli/coverage.py`'s `_element_lists()` (the `bajutsu coverage` command) glob a
  fixed depth under the run set's own directory — `*/*/<name>` (two segments) from
  `_evidence_files()`'s unfiltered branch, `*/<name>` (one segment) from its per-run-id branch — to
  find every `network.json` and `elements.json` the run set recorded. That fixed count already
  misses a non-primary target's own `network.json` today, one folder deeper than either branch
  reaches — a latent gap this item's own motivation would otherwise leave in place for the *primary*
  target too. It misses `elements.json` far more broadly: that file lives one folder deeper still, at
  `<sid>/<stepId>/elements.json`, so today's fixed count never finds it at all, for a single-target
  run as much as a multi-target one — a pre-existing defect this item did not introduce, distinct
  from the multi-target gap above, but one the same fix closes. `_evidence_files()` widens to match
  any depth (`**/<name>`) instead of a fixed count, which finds `network.json` for every declared
  target and `elements.json` for every step, for a single-target run and a multi-target one alike.
  `bajutsu/analysis/cli/coverage.py`'s own glob is retired rather than widened in parallel: a new
  `read_element_lists()`, beside `read_exchanges()` and `read_observed_ids()` in
  `bajutsu/analysis/coverage/_functions.py`, wraps `_evidence_files()` once for `elements.json` and
  is now the one place that enumerates and parses it, reused by `read_observed_ids()` and by the
  CLI's screens-visited dimension alike — closing the exact duplication that let the two copies go
  wrong in the same way in the first place.

### Not doing

- **`driver_trace.json` stays at `<sid>/driver_trace.json`, un-nested.** `run --trace` opens one
  trace context around the whole scenario, spanning every backend a multi-target scenario launches,
  not one target's own driver alone — there is no single target to attribute it to, so nesting it
  under any one target's folder would misname what the file actually covers.
- **The crash-diagnostics directory (`_write_crash_artifacts`, `_CRASH_DIAGNOSTICS_DIR`) is left
  exactly where it is today.** It already writes under a bare `<sid>/`, for whichever lease's backend
  crashed, primary or not — a pre-existing gap this item does not newly introduce, and closing it
  needs its own per-target attribution design (which lease crashed, threaded through the retry
  loop's own failure path) that is out of this item's scope.
- **No sanitization is added for a target name used as a path segment.** A non-primary target's
  folder name has been an unsanitized config key since BE-0428 shipped; this item only extends the
  same, already-accepted convention to the primary target and to per-step folders, and does not
  revisit whether a target name itself needs the kind of narrow sanitizer
  [BE-0417](../BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming.md) added
  for a scenario's own source-file stem.

### Work breakdown (MECE)

1. **Writers.** The four call sites above (`pipeline.py`'s primary `VisualContext` and
   `_write_network` prefix, `_functions.py`'s primary `start_scenario_intervals` /
   `finish_scenario_intervals` calls, `_step_runner.py`'s `step_id`), each gated on the scenario's
   declared-target count being two or more.
2. **Readers.** `bajutsu/serve/operations/reads.py`'s `_step_artifacts()`; the two coverage globs in
   `bajutsu/analysis/coverage/_functions.py` and `bajutsu/analysis/cli/coverage.py`.
3. **Docs.** [`docs/reporting.md`](../../docs/reporting.md)'s Output layout section gains the
   `<target>/` level for a scenario declaring two or more targets, and its `docs/ja/` mirror.
4. **Tests.** A multi-target scenario's evidence lands under `<sid>/<target>/…` for every target
   (primary and non-primary alike), at both the scenario level (video/deviceLog/network.json) and
   the step level (screenshots/elements.json); a zero- or one-declared-target scenario's evidence
   is byte-for-byte unchanged from today; the serve step-picker resolves a multi-target run's
   per-step artifacts through the new, target-qualified id, and falls back to the old flat id for a
   multi-target run recorded before this item shipped; the widened coverage globs find every
   declared target's own `network.json` in a multi-target run, and — a fixture built from a real
   `<sid>/<stepId>/elements.json` layout, unlike today's flat test fixture — now find `elements.json`
   at all, in a single-target run as much as a multi-target one.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Nest every target, including a scenario that declares only one | Rejected: a single declared target has nothing to disambiguate from, so the extra path segment would only lengthen every existing self-declaring scenario's evidence path for no reader benefit, and would change today's layout for scenarios this item's own motivation does not concern. |
| Leave the primary flat and only fix the per-step folders to carry a target segment for the *non-primary* targets alone | Rejected: it would still leave two different conventions live in the same scenario's evidence tree — a primary at `<sid>/<stepId>/…` beside a non-primary at `<sid>/<target>/<stepId>/…` — the exact inconsistency this item exists to remove, rather than one convention applied uniformly. |
| Keep the flat layout and rely solely on `manifest.json`'s already-recorded `StepOutcome.target` / `RunResult.target_devices` (BE-0428) for a reader to regroup evidence by target | Rejected: a reader (or a script) would still have to cross-reference the manifest against the run directory by hand for every step, which is exactly the friction this item's *Motivation* names; a folder a reader can simply open is cheaper than a lookup they have to perform themselves. |
| Retrofit every already-completed run's directory on disk (a migration) to the new layout | Rejected: an already-finished run's `manifest.json` already names every artifact by its recorded path, so a stored run continues to render and link correctly under the layout it was written with; rewriting historical run directories on disk would risk corrupting a run an investigation still depends on, for a cosmetic-only gain. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Writers: the primary target's scenario-level and per-step evidence nests under its own folder
      alongside every other declared target's, once a scenario declares two or more targets.
- [x] Readers: the serve step picker and the `bajutsu coverage` globs resolve a multi-target run's
      per-target evidence correctly.
- [x] Docs: `docs/reporting.md` and its `docs/ja/` mirror.
- [x] Tests: writer nesting (multi- and single/zero-target cases), the serve step picker, and the
      widened coverage globs.

Log:

- [#2073](https://github.com/bajutsu-e2e/bajutsu/pull/2073) — Units 1-4, completing the item. `pipeline.py`'s primary `VisualContext`/`_write_network` prefix,
  `_functions.py`'s primary `start_scenario_intervals`/`finish_scenario_intervals` calls, and
  `_step_runner.py`'s `step_id` construction all nest under the routed target's own folder once a
  scenario declares two or more targets. The serve step picker (`reads.py`) rebuilds the same
  target-qualified id from `Step.resolved_target`, falling back to the old flat id for a
  multi-target run recorded before this item shipped. `_evidence_files()` widened from a fixed
  segment count to `**`, which also closes a pre-existing gap: `elements.json` sits one folder
  deeper than the old count reached even for a single-target run. A self-review round found that
  fixing it in two duplicated call sites left them free to drift again, so `bajutsu coverage`'s own
  glob was retired in favor of a new `read_element_lists()` beside `read_exchanges()` /
  `read_observed_ids()`, reused by both the id-coverage and screens-visited dimensions. The same
  round also caught a real bug in the primary-nesting gate: `run_scenario` is a public function, and
  a caller passing `target_runtimes` without `primary_target` (it defaults to `""`) made the gate
  nest under a bare trailing slash (`<sid>//scenario.mp4`) instead of leaving the primary's own
  artifacts at `<sid>`; the gate now also requires `primary_target` to be non-empty.
  `docs/reporting.md` / `docs/ja/reporting.md`'s Output layout section gained the new `<target>/`
  level.

## References

- [BE-0428 — Multi-target scenario execution (steps interleaved across targets)](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) — introduced `Scenario.targets`, per-step `target`, and the non-primary target folder convention this item extends to the primary.
- [BE-0436 — A primary target: let a multi-target scenario's steps omit `target`](../BE-0436-primary-target-default/BE-0436-primary-target-default.md) — `Step.resolved_target`, read by the serve step-picker fix (the writer instead names its segment from the runner's own `self.target`).
- [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) — the Author → Edit step picker whose step-id reconstruction this item fixes.
- [BE-0262 — Live step-picking and target-scoped runs in the Author editor](../BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker.md) — the run-less step list this item's serve fix leaves unaffected.
- [BE-0417 — Name a scenario's evidence directory after its source file](../BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming.md) — the most recent prior change to this same directory convention, and the sanitization precedent this item does not extend to target names.
- [`docs/reporting.md`](../../docs/reporting.md) — the Output layout section this item updates.
- [`docs/glossary.md#target-app-device`](../../docs/glossary.md#target-app-device) — what a target is.
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py) · [`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py) · [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py) — the writers this item changes.
- [`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py) · [`bajutsu/analysis/coverage/_functions.py`](../../bajutsu/analysis/coverage/_functions.py) · [`bajutsu/analysis/cli/coverage.py`](../../bajutsu/analysis/cli/coverage.py) — the readers this item changes.
