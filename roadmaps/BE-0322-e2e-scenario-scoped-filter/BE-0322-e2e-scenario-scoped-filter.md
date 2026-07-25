**English** · [日本語](BE-0322-e2e-scenario-scoped-filter-ja.md)

# BE-0322 — Scenario-scoped E2E filtering (fire only the affected on-device jobs)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0322](BE-0322-e2e-scenario-scoped-filter.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0322") |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Fire only the on-device end-to-end (E2E) jobs a change can actually affect. Each E2E lane runs a set
of feature and dimension jobs — iOS on a Simulator, Android on a hardware-accelerated emulator, and
web on a headless browser — and some of those jobs declare the scenarios they exercise. Today any change a lane deems relevant fires every job in that
lane, even jobs whose scenarios the change never touched. This item narrows that fan-out for the one
case it can prove safe: when a change is confined to scenario files, only the jobs that declare a
changed scenario run. Every other case still fires the whole lane, unchanged — a change to shared
driver or runner code, a job that declares no scenario, and a lane that does not key its jobs on
scenarios at all. The decision is a deterministic function of the `git` diff and the scenarios each
job already declares, with no large language model (LLM) in the path and no bearing on any run's
pass/fail verdict. It over-selects toward the full fleet rather than under-selecting, so a failure is
always caught before merge, never deferred past it.

## Motivation

The iOS E2E lane runs ten on-device jobs, each booting a Simulator on a macOS runner billed at ten
times the Linux rate; the Android lane runs six under a hardware-accelerated emulator. A required
aggregator check guards each lane (`E2E (iOS)`, `E2E (android)`, `E2E (web)`), and a required check that
never reports blocks the merge — so a lane cannot be gated at the workflow trigger. Every lane
instead triggers on every pull request, and [`scripts/e2e_changes.py`](../../scripts/e2e_changes.py)
decides, per lane, whether the metered jobs run at all. The verdict it emits today is a single
lane-wide boolean: one relevant path fires the lane's entire fleet.

That boolean is too coarse for a change confined to a single scenario. A scenario file describes one
scenario and is independent of the others, so editing `gestures_multitouch.yaml` cannot change what
`search.yaml` exercises — yet on the iOS lane a change to it fires all ten jobs, including those that
never load it. The iOS jobs are organized by feature and dimension rather than one per scenario: the
`actuation` job runs four scenarios in sequence, `smoke.yaml` is reused across two jobs, and the
`codegen`, `conformance`, and `visual` jobs load no single scenario file. A scenario-only change
still pays every job's Simulator-boot and run cost, most of it for jobs the change provably did not
reach.

The information needed to narrow that fan-out already lives in the workflow. Each scenario-keyed job
names the scenarios it runs in its `scenarios:` input, so the map from a changed scenario file to the
jobs that load it is a lookup over what the workflow already declares, not an inference. Reversing the
lane-wide boolean into a per-job decision reads that declaration and needs no new source of truth.

## Detailed design

Proposal altitude. The change lives entirely in the continuous-integration (CI) filter — a
`git`-diff classifier in [`scripts/e2e_changes.py`](../../scripts/e2e_changes.py) and the `if:`
guards in the E2E workflow files. It touches no device, calls no LLM, and moves no pass/fail verdict:
the deterministic runner still decides every scenario's result (prime directive 1), and the per-app
scenario files stay the only place per-app knowledge lives (prime directive 3).

- **Change classification (deterministic).** Split a pull request's changed files, already gathered
  by the merge-base (three-dot) diff the filter uses today, into two classes. A *shared-code* change
  matches the run / codegen / record surface, a driver module, the application source, the conformance
  harness, or the workflow file — the paths `e2e_changes.py` already enumerates as relevant. A
  *scenario-only* change touches nothing outside a lane's scenario files. The distinction is a
  partition of the paths the filter already recognizes, so it adds a classifier over the existing
  positive list rather than a second list to maintain.

- **Job-to-scenario map.** Build the map from each job to the scenarios it declares by reading the
  `scenarios:` inputs already present in a lane's workflow file. A job that declares one or more
  scenarios is scenario-keyed; a job that declares none — the `codegen`, `conformance`, and `visual`
  dimension jobs — is not. The map is a lookup over the workflow's own declarations, so it needs no
  second source to maintain and cannot drift from what each job actually runs.

- **Affected-job selection.** On a scenario-only change, fire a scenario-keyed job when the change
  edited any scenario that job declares. A scenario reused across jobs — `smoke.yaml`, loaded by two
  iOS jobs — fires both, which is correct, because each of the two exercises it. Emit the
  affected-job set alongside the relevant verdict to the workflow's `GITHUB_OUTPUT` for the jobs to
  read. On a change that reaches shared code, signal the whole fleet as today.

- **Workflow wiring.** Guard each scenario-keyed job on "a shared-code change occurred, or this job is
  in the affected set". A shared-code change therefore fires every job exactly as it does today; a
  scenario-only change fires only the jobs that load a changed scenario. Each on-device job builds its
  app and runner independently (there is no shared build-once job), so a narrowed run simply skips the
  build and Simulator boot of every job it drops.

- **Aggregator integrity.** The lane's required aggregator check (`E2E (iOS)` and its Android and web
  siblings) must report success when a narrowed run skips most jobs. A skipped job is not a failed
  job, so the aggregator gates on "no job failed" — evaluated with `if: always()` so the aggregator
  itself always runs and always reports — rather than on "every job succeeded". This keeps a required
  check green on a narrowed run without letting a genuine job failure slip through.

- **Fallback to the whole fleet.** Anything not attributable to a specific scenario falls back to
  firing the entire lane. Four cases fall back. A change to shared code fires every job, because
  shared driver or runner code can affect any scenario. A dimension job that declares no scenario —
  `codegen`, `conformance`, `visual` — exercises the driver or harness rather than one scenario, so it
  runs on any scenario-only change. A lane whose jobs are not keyed on scenarios at all — the Android
  lane, whose jobs select their work by dimension rather than by a `scenarios:` input — keeps today's
  whole-fleet behavior until its jobs declare scenarios. A shared scenario fragment (a `setup` file
  several scenarios include) is likewise not attributable to one job and fires the whole lane.
  Over-selection toward the full fleet is the safe direction: it wastes jobs but never skips one a
  change could have broken.

- **Scope of the saving.** The narrowing applies where jobs are scenario-keyed — today the iOS lane's
  seven scenario-declaring jobs. A scenario-only iOS change fires only the scenario-keyed jobs that
  load a changed scenario, rather than all seven of them; the `codegen`, `conformance`, and `visual`
  jobs and the whole Android lane still run, safely, as they do today. The web lane's jobs, like Android's, declare no scenario,
  so it too has no scenario-keyed fan-out to narrow. The win is bounded but exact: it removes the
  clearly-wasted jobs and touches nothing else.

- **Relationship to app-source step selection.** A companion proposal,
  [BE-0321](../BE-0321-test-impact-analysis/BE-0321-test-impact-analysis.md), selects affected scenario
  *steps* from a change to the *application under test*, by matching the stable identifiers a diff
  touches against the identifiers each step references. BE-0321 operates on the application's own source
  and reports which steps a developer should review. This item operates on Bajutsu's CI inputs — the
  scenario files each job declares and the shared driver and runner code — and decides which on-device
  *jobs* to run. The two are complementary layers on the same goal of running less: one reads the
  application, the other reads the suite and its harness.

- **Future tiers (named, out of scope).** Scenario-scoped filtering is the coarsest useful cut: it
  narrows only scenario-file changes on scenario-keyed jobs, and still fires the whole lane on a
  shared-code change, a dimension job, or a non-keyed lane. Two deeper tiers could narrow those cases
  too — symbol-level selection (which driver method a diff's hunks touch, cross-referenced against the
  methods each scenario exercises) and coverage-based test impact analysis (which production lines each
  scenario executes, cross-referenced against the lines a diff changes). Both need a map that can go
  stale and therefore a conservative fallback to the full fleet when the map cannot attribute a change.
  They are heavier, distinct capabilities; this item ships the file-level cut that stands on its own
  and leaves the deeper tiers as a later step.

## Alternatives considered

- **Tier shared-code changes to a smoke fast lane.** Firing only the smoke scenario on a shared-code
  change at pull-request time, and deferring the rest of the fleet to a merge-to-main run, would cut
  more than scenario-scoped filtering does. It is rejected as the default because it moves failure
  detection from the pull request to after the merge: a driver change that breaks only the gestures
  scenario would pass a smoke-only pull request, merge, and turn `main` red — where an on-device lane
  is already prone to boot flakiness, so a red `main` is costly to triage and forces a revert. The
  scenario-scoped cut keeps every failure inside the pull request that caused it (fail fast); tiering
  trades that away, so it stays out of scope.

- **Gate each job at the workflow trigger with `paths:`.** Listing each job's scenario paths under a
  per-job trigger would need no filter script. It cannot work here: the lane's aggregator is a required
  check, and a required check that never reports blocks the merge. Every lane must trigger on every
  pull request and decide relevance inside the run, which is what the filter script exists to do.

- **Coverage-based test impact analysis now.** Recording which production lines each scenario executes,
  then firing only the jobs whose covered lines a diff changed, would narrow the cases scenario-scoped
  filtering cannot — shared-code changes, dimension jobs, and the Android lane. It requires building and
  maintaining a coverage map and a fallback for changes the map cannot attribute, a heavier capability
  noted above as a future tier. This item ships the file-level cut first.

- **Do nothing (status quo).** Acceptable: the lane-wide boolean is correct and safe, only wasteful.
  The waste is a scenario-only pull request paying the full fleet's Simulator-boot and run cost across
  ten iOS jobs, on a metered macOS runner, on every push — most of it for the scenario-keyed jobs
  whose scenarios the change did not reach, which the workflow's own `scenarios:` declarations already
  have the information to skip.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Change classification — partition a diff's changed files into shared-code versus scenario-only in `scripts/e2e_changes.py`.
- [ ] Job-to-scenario map and affected-job selection — read each job's declared `scenarios:`, and emit the affected-job set alongside the relevant boolean to `GITHUB_OUTPUT`.
- [ ] Workflow wiring — guard each scenario-keyed job on "shared change or in the affected set", and keep the required aggregator green when jobs skip (`if: always()` + "no job failed").
- [ ] Fallback and documentation — fall back to the whole fleet for dimension jobs, non-keyed lanes (Android), and shared fragments, and document the safety model and the bounded scope.

## References

[BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) (the static scenario analysis this
job selection is adjacent to), [BE-0321](../BE-0321-test-impact-analysis/BE-0321-test-impact-analysis.md)
(the companion app-source step selection this complements),
[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
(the contributor-workflow guardrails this speeds up), [`scripts/e2e_changes.py`](../../scripts/e2e_changes.py)
(the filter this extends), [CLAUDE.md](../../CLAUDE.md) (the CI and parallel-work contract),
[docs/ai-development.md](../../docs/ai-development.md) (the contributor workflow this reduces latency in).
