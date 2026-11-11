**English** · [日本語](BE-0322-e2e-scenario-scoped-filter-ja.md)

# BE-0322 — Scenario-scoped E2E filtering (fire only the affected on-device jobs)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0322](BE-0322-e2e-scenario-scoped-filter.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0322") |
| Implementing PR | [#1375](https://github.com/bajutsu-e2e/bajutsu/pull/1375) |
| Topic | Contributor workflow |
| Related | [BE-0338](../BE-0338-codegen-visual-scenario-keyed/BE-0338-codegen-visual-scenario-keyed.md) |
<!-- /BE-METADATA -->

## Introduction

Fire only the on-device end-to-end (E2E) jobs that a change can actually affect. Each E2E lane runs a
set of feature and dimension jobs: iOS on a Simulator, Android on a hardware-accelerated emulator,
and web on a headless browser. Some of those jobs declare the scenarios they exercise.

Today any change that a lane deems relevant fires every job in that lane. The fan-out includes jobs
whose scenarios the change never touched. This item narrows the fan-out for the one case it can
prove safe: a change confined to scenario files. In that case, only the jobs that declare a changed
scenario run.

Every other case still fires the whole lane, unchanged:

- a change to shared driver or runner code
- a job that declares no scenario
- a lane that does not key its jobs on scenarios at all

The decision is a deterministic function of the `git` diff and the scenarios that each job already
declares. No large language model (LLM) sits in the path, and the decision has no bearing on the
pass/fail verdict of any run. The decision over-selects toward the full fleet instead of
under-selecting. A failure is therefore always caught before merge, never deferred past the merge.

## Motivation

The iOS E2E lane runs ten on-device jobs. Each job boots a Simulator on a macOS runner, which bills
at ten times the Linux rate. The Android lane runs six jobs under a hardware-accelerated emulator.

A required aggregator check guards each lane (`E2E (iOS)`, `E2E (android)`, `E2E (web)`). A required
check that never reports blocks the merge, so a lane cannot be gated at the workflow trigger. Every
lane instead triggers on every pull request. [`scripts/e2e_changes.py`](../../scripts/e2e_changes.py)
decides, per lane, whether the metered jobs run at all. The verdict that the script emits today is a
single lane-wide boolean. One relevant path fires the entire fleet of the lane.

That boolean is too coarse for a change confined to a single scenario. A scenario file describes one
scenario and is independent of the others. Editing `gestures_multitouch.yaml` cannot change what
`search.yaml` exercises. Yet on the iOS lane, a change to `gestures_multitouch.yaml` fires all ten
jobs, including the jobs that never load the file.

The iOS jobs are organized by feature and dimension, not one per scenario:

- The `actuation` job runs four scenarios in sequence.
- `smoke.yaml` is reused across two jobs.
- The `codegen`, `conformance`, and `visual` jobs load no single scenario file.

A scenario-only change still pays the Simulator-boot and run cost of every job. Most of that cost
goes to jobs that the change provably did not reach.

The workflow already holds the information needed to narrow the fan-out. Each scenario-keyed job
names the scenarios it runs in its `scenarios:` input. The map from a changed scenario file to the
jobs that load the file is therefore a lookup over what the workflow already declares, not an
inference. Reversing the lane-wide boolean into a per-job decision reads that declaration and needs
no new source of truth.

## Detailed design

Proposal altitude. The change lives entirely in the continuous-integration (CI) filter: a
`git`-diff classifier in [`scripts/e2e_changes.py`](../../scripts/e2e_changes.py) and the `if:`
guards in the E2E workflow files. The change touches no device, calls no LLM, and moves no pass/fail
verdict. The deterministic runner still decides the result of every scenario (prime directive 1). The
per-app scenario files stay the only place where per-app knowledge lives (prime directive 3).

- **Change classification (deterministic).** Split the changed files of a pull request into two
  classes. The merge-base (three-dot) diff that the filter uses today already gathers the files.
  - A *shared-code* change matches the run / codegen / record surface, a driver module, the
    application source, the conformance harness, or the workflow file. These are the paths that
    `e2e_changes.py` already enumerates as relevant.
  - A *scenario-only* change touches nothing outside the scenario files of a lane.

  The distinction is a partition of the paths that the filter already recognizes. The partition adds
  a classifier over the existing positive list, instead of a second list to maintain.

- **Job-to-scenario map.** Build the map from each job to the scenarios it declares by reading the
  `scenarios:` inputs already present in the workflow file of a lane. A job that declares one or more
  scenarios is scenario-keyed. A job that declares none is not. The `codegen`, `conformance`, and
  `visual` dimension jobs declare none. The map is a lookup over the workflow's own declarations. The
  map needs no second source to maintain and cannot drift from what each job actually runs.

- **Affected-job selection.** On a scenario-only change, fire a scenario-keyed job when the change
  edited any scenario that the job declares. A scenario reused across jobs fires every job that loads
  the scenario. The scenario `smoke.yaml`, loaded by two iOS jobs, fires both jobs. Firing both is
  correct, because each of the two exercises the scenario. Emit the affected-job set to the
  workflow's `GITHUB_OUTPUT`, alongside the relevant verdict, for the jobs to read. On a change that
  reaches shared code, signal the whole fleet as today.

- **Workflow wiring.** Guard each scenario-keyed job on "a shared-code change occurred, or this job is
  in the affected set". A shared-code change fires every job exactly as it does today. A scenario-only change fires only the jobs that load a changed scenario. Each on-device job builds its app
  and runner independently, and no shared build-once job exists. A narrowed run therefore skips the
  build and the Simulator boot of every job that the run drops.

- **Aggregator integrity.** The required aggregator check of the lane (`E2E (iOS)` and the Android
  and web siblings) must report success when a narrowed run skips most jobs. A skipped job is not a
  failed job. The aggregator therefore gates on "no job failed", not on "every job succeeded". The
  aggregator evaluates with `if: always()`, so the aggregator itself always runs and always reports.
  This keeps a required check green on a narrowed run without letting a genuine job failure slip
  through.

- **Fallback to the whole fleet.** Anything not attributable to a specific scenario falls back to
  firing the entire lane. Four cases fall back:
  - A change to shared code fires every job, because shared driver or runner code can affect any
    scenario.
  - A dimension job that declares no scenario (`codegen`, `conformance`, `visual`) exercises the
    driver or harness instead of one scenario. The job therefore runs on any scenario-only change.
  - A lane whose jobs are not keyed on scenarios at all keeps today's whole-fleet behavior until its
    jobs declare scenarios. The Android lane is such a lane: its jobs select their work by dimension,
    not by a `scenarios:` input.
  - A shared scenario fragment (a `setup` file that several scenarios include) is not attributable to
    one job, and fires the whole lane.

  Over-selection toward the full fleet is the safe direction. The direction wastes jobs but never
  skips a job that a change could have broken.

- **Scope of the saving.** The narrowing applies where jobs are scenario-keyed. Today that is the
  seven scenario-declaring jobs of the iOS lane. A scenario-only iOS change fires the scenario-keyed
  jobs that load a changed scenario, not all seven. The `codegen`, `conformance`, and `visual` jobs
  and the whole Android lane still run, safely, as they do today. The jobs of the web lane, like those
  of Android, declare no scenario. The web lane has no scenario-keyed fan-out to narrow either. The
  win is bounded but exact: the win removes the clearly wasted jobs and touches nothing else.

- **Relationship to app-source step selection.** A companion proposal,
  [BE-0321](../BE-0321-test-impact-analysis/BE-0321-test-impact-analysis.md), selects affected
  scenario *steps* from a change to the *application under test*. BE-0321 matches the stable
  identifiers that a diff touches against the identifiers that each step references. BE-0321 operates
  on the application's own source and reports which steps a developer should review.

  This item operates on the CI inputs of Bajutsu: the scenario files that each job declares, and the
  shared driver and runner code. This item decides which on-device *jobs* to run. The two proposals
  are complementary layers on the same goal of running less. One reads the application. The other
  reads the suite and its harness.

- **Future tiers (named, out of scope).** Scenario-scoped filtering is the coarsest useful cut. The
  cut narrows scenario-file changes on scenario-keyed jobs. The cut still fires the whole lane on a
  shared-code change, a dimension job, or a non-keyed lane. Two deeper tiers could narrow those cases
  too:
  - Symbol-level selection: which driver method the hunks of a diff touch, cross-referenced against
    the methods that each scenario exercises.
  - Coverage-based test impact analysis: which production lines each scenario executes,
    cross-referenced against the lines that a diff changes.

  Both tiers need a map that can go stale. Both therefore need a conservative fallback to the full
  fleet when the map cannot attribute a change. The tiers are heavier, distinct capabilities. This
  item ships the file-level cut, which stands on its own, and leaves the deeper tiers as a later step.

## Alternatives considered

- **Tier shared-code changes to a smoke fast lane.** On a shared-code change, fire the smoke scenario
  alone at pull-request time, and defer the rest of the fleet to a merge-to-main run. The approach
  would cut more than scenario-scoped filtering does. Rejected as the default, because the approach
  moves failure detection from the pull request to after the merge. A driver change that breaks only
  the gestures scenario would pass a smoke-only pull request, merge, and turn `main` red. An
  on-device lane is already prone to boot flakiness, so a red `main` is costly to triage and forces a
  revert. The scenario-scoped cut keeps every failure inside the pull request that caused the failure
  (fail fast). Tiering trades that property away, so tiering stays out of scope.

- **Gate each job at the workflow trigger with `paths:`.** Listing the scenario paths of each job
  under a per-job trigger would need no filter script. The approach cannot work here. The aggregator
  of the lane is a required check, and a required check that never reports blocks the merge. Every
  lane must trigger on every pull request and decide relevance inside the run. Deciding relevance
  inside the run is what the filter script exists to do.

- **Coverage-based test impact analysis now.** Record which production lines each scenario executes,
  then fire the jobs whose covered lines a diff changed. The approach would narrow the cases that
  scenario-scoped filtering cannot: shared-code changes, dimension jobs, and the Android lane. The
  approach requires building and maintaining a coverage map, plus a fallback for changes that the map
  cannot attribute. The Detailed design above notes this capability as a heavier future tier. This
  item ships the file-level cut first.

- **Do nothing (status quo).** Acceptable. The lane-wide boolean is correct and safe, and merely
  wasteful. A scenario-only pull request pays the Simulator-boot and run cost of the full fleet
  across ten iOS jobs, on a metered macOS runner, on every push. Most of that cost goes to
  scenario-keyed jobs whose scenarios the change did not reach. The workflow's own `scenarios:`
  declarations already hold the information to skip those jobs.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Change classification — partition a diff's changed files into shared-code versus scenario-only in `scripts/e2e_changes.py`.
- [x] Job-to-scenario map and affected-job selection — read each job's declared `scenarios:`, and emit the affected-job set alongside the relevant boolean to `GITHUB_OUTPUT`.
- [x] Workflow wiring — guard each scenario-keyed job on "shared change or in the affected set", and keep the required aggregator green when jobs skip (`if: always()` + "no job failed").
- [x] Fallback and documentation — fall back to the whole fleet for dimension jobs, non-keyed lanes (Android), and shared fragments, and document the safety model and the bounded scope.

The narrowing landed against the post-consolidation iOS lane, so the scenario-keyed jobs are `run`,
`actuation`, `golden`, and `bundled-runner` (`smoke.yaml` is declared by both `run` and
`bundled-runner`, so a change to it fires both). A guard↔map parity test pins each job's `if:`
guard against the `scenarios:` the workflow declares, so a future job rename or scenario move fails
a test rather than silently mis-narrowing the lane.

## References

- [BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md): the static scenario analysis
  that this job selection is adjacent to.
- [BE-0321](../BE-0321-test-impact-analysis/BE-0321-test-impact-analysis.md): the companion
  app-source step selection that this item complements.
- [BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md):
  the contributor-workflow guardrails that this item speeds up.
- [`scripts/e2e_changes.py`](../../scripts/e2e_changes.py): the filter that this item extends.
- [CLAUDE.md](../../CLAUDE.md): the CI and parallel-work contract.
- [docs/ai-development.md](../../docs/ai-development.md): the contributor workflow in which this item
  reduces latency.
