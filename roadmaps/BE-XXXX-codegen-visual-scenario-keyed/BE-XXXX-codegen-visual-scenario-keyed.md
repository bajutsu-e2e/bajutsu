**English** · [日本語](BE-XXXX-codegen-visual-scenario-keyed-ja.md)

# BE-XXXX — Scenario-key the iOS codegen and visual E2E jobs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-codegen-visual-scenario-keyed.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
| Related | [BE-0322](../BE-0322-e2e-scenario-scoped-filter/BE-0322-e2e-scenario-scoped-filter.md) |
<!-- /BE-METADATA -->

## Introduction

Extend the scenario-scoped end-to-end (E2E) narrowing that BE-0322 shipped to the two iOS jobs it
deliberately left out: `codegen` and `visual`. BE-0322 fires only the on-device jobs a change can
affect — a change confined to scenario files runs just the jobs that declare a changed scenario,
rather than the whole macOS lane. It applies that narrowing only to the feature jobs whose scenarios
it can read from the workflow, and leaves the `codegen` and `visual` dimension jobs firing on every
relevant change, because each declares the scenarios it runs in a `Makefile` target rather than in
the workflow input the narrowing reads. This item brings the two jobs under the same narrowing
without weakening the property that made BE-0322 safe: the job-to-scenario map stays derived from
what each job actually runs, machine-checked so it can never claim a job runs a scenario it does
not. The result is that a scenario-only change to a scenario neither job exercises stops booting two
ten-times-metered macOS Simulators for nothing, while every change either job could break still runs
it.

## Motivation

The iOS E2E lane runs its on-device jobs on macOS runners billed at ten times the Linux rate, so
every job a change fires without cause is a direct cost. A required aggregator check (`E2E (iOS)`)
guards the lane, and a required check that never reports blocks the merge, so the lane cannot be
gated at the workflow trigger; it triggers on every pull request, and
[`scripts/e2e_changes.py`](../../scripts/e2e_changes.py) decides which jobs actually run. BE-0322
sharpened that decision from a single lane-wide boolean into three outputs the jobs read: `relevant`
(run any metered job at all), `shared` (a change to driver or runner code that can affect any
scenario, so fire the whole lane), and `affected` (the JSON array of scenario-keyed jobs a
scenario-only change reached). A feature job such as `run` runs when `relevant` and (`shared` or the
job is named in `affected`); a scenario-only change to none of a job's scenarios narrows it out.

The `codegen` and `visual` jobs never reach that narrowing. Each runs when `relevant` alone is true,
so any relevant change fires both — including a scenario-only change to a scenario neither one
exercises. The `codegen` job builds and runs native XCUITest from `components.yaml` (`make -C
demos/showcase ui-test`) and from four coverage scenarios — `text_editing.yaml`, `gestures.yaml`,
`gestures_multitouch.yaml`, and `codegen_extra.yaml` (`make ui-test-coverage`). The `visual` job runs
the committed pixel visual regression test (VRT) over `visual/visual_ios.yaml` (`make e2e-visual`).
An edit to, say, `smoke.yaml` — a scenario neither job runs — still boots a Simulator for each and
rebuilds native tests, work the change cannot have affected.

The reason BE-0322 stopped short of these two jobs is the same property that keeps its narrowing
safe. Its map is read from the `scenarios:` inputs each job already passes to the shared
`bajutsu-e2e` action, so the map is the job's own declaration of what it runs and cannot drift from
it. The `codegen` and `visual` jobs declare their scenarios in `demos/showcase/Makefile` targets
instead, which the workflow-only reader
[`job_scenario_map`](../../scripts/e2e_changes.py) never sees — so to BE-0322 they are dimension jobs
that declare nothing, and a job that declares nothing must fire on every relevant change, the safe
over-selection. Narrowing them is worthwhile only if the map can be tied to the `Makefile` ground
truth as tightly as it is tied to the action inputs today: a hand-written scenario list that a
`Makefile` edit could silently outdate would narrow a job out of a change it actually runs — a
skipped job a change could break, the fail-closed violation BE-0322's design exists to prevent.

## Detailed design

The work is to attribute the `codegen` and `visual` scenarios to their jobs from the one place those
scenarios are named — the `Makefile` targets — and to machine-check that attribution so it cannot
drift, before wiring the two jobs onto the scenario-keyed condition the feature jobs already use. The
breakdown below is one unit per box in *Progress*.

- **Ground truth from the `Makefile` (Unit 1).** Add a reader that extracts, per dimension job, the
  scenario files its `Makefile` target actually codegens or runs: `components.yaml` plus the four
  coverage scenarios for `codegen`, and `visual/visual_ios.yaml` for `visual`. The reader parses the
  committed target recipes, so the set follows the recipe rather than a second copy of it.

- **Drift guard (Unit 2).** Add a test to the `e2e_changes` suite that asserts the scenario set the
  workflow attributes to each of `codegen` and `visual` equals the set its `Makefile` target runs
  (Unit 1). This test is the linchpin that lets the narrowing hold BE-0322's invariant: the day a
  `Makefile` edit adds or drops a scenario from either target, the test fails `make check` unless the
  attribution moves with it. Without it, the attribution is exactly the drift BE-0322 forbids; with
  it, the attribution is as ground-truth-bound as the action-input map.

- **Attribute the scenarios (Unit 3).** Teach `e2e_changes.py` to fold the `Makefile`-declared
  scenarios of `codegen` and `visual` into the same job-to-scenario map the `affected` computation
  already consumes, so a change to one of their scenarios names the job in `affected`. Update the
  module docstring, which today records `codegen` / `conformance` / `visual` as dimension jobs that
  run on any relevant change, to state the new rule for the two now-keyed jobs.

- **Wire the jobs (Unit 4).** Change the `if:` of `codegen` and `visual` in
  [`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) from `needs.changes.outputs.relevant == 'true'`
  to the scenario-keyed form the feature jobs use — `relevant` and (`shared` or `contains(fromJSON(…
  affected), '<job>')`) — so each runs on a shared-code change or a change to one of its own
  scenarios, and narrows out otherwise.

- **Documentation (Unit 5).** Update the bilingual CI documentation that describes which jobs a
  change fires, and cross-link this item and BE-0322 both ways.

`conformance` stays a dimension job: it drives the whole driver-conformance harness rather than a
scenario subset, so it declares no scenarios to key on and must keep firing on every relevant change.
This item is iOS-only — the Android and web lanes key no jobs on scenarios, so their `codegen` and
`visual` equivalents already fall back to the whole-lane behavior BE-0322 defines for a lane with no
scenario-keyed jobs.

## Alternatives considered

**Declare the scenarios in the workflow without a drift guard.** Adding a plain `scenarios:` list to
each job and reading it as today would key the jobs with far less code, and no `Makefile` parser. It
is rejected because it reintroduces the drift BE-0322's design removed: the list and the `Makefile`
target would be two independent copies of the same fact, and a `Makefile` edit that changed one
target's scenarios would silently outdate the list, narrowing the job out of a change it runs. The
drift guard (Unit 2) is the whole reason the narrowing is safe to add.

**Thread the scenario list from the workflow into the `Makefile` target.** Passing the scenarios as
a `make` variable (`make ui-test SCENARIOS=…`) would make the workflow declaration the real input the
target runs, matching the feature jobs, where the declaration backs a live action input rather than
being checked against one. It is the cleaner end state, but it requires reworking the `ui-test`,
`ui-test-coverage`, and `e2e-visual` recipes — which today name their scenarios inline — and those
recipes are also run locally, so the change carries more surface and risk than the reader-plus-guard
approach. Recorded as the preferred follow-up once the narrowing itself is in place.

**Leave `codegen` and `visual` as dimension jobs.** The status quo fires both on every relevant
change. It is rejected because the narrowing is provably safe once the drift guard binds the
attribution to the `Makefile`, and the two jobs are among the lane's most expensive — each boots a
Simulator and builds native tests — so the wasted runs are worth reclaiming.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `Makefile` ground-truth reader for the `codegen` and `visual` scenario sets.
- [ ] Unit 2 — drift-guard test tying the attribution to the `Makefile` targets.
- [ ] Unit 3 — attribute the two jobs' scenarios in `e2e_changes.py`; update its docstring.
- [ ] Unit 4 — wire the `codegen` and `visual` `if:` onto the scenario-keyed condition.
- [ ] Unit 5 — bilingual CI documentation and reciprocal BE-0322 cross-links.

## References

- [BE-0322 — Scenario-scoped E2E filtering](../BE-0322-e2e-scenario-scoped-filter/BE-0322-e2e-scenario-scoped-filter.md)
  — the narrowing this item extends, and the source of the `relevant` / `shared` / `affected`
  outputs and the no-drift invariant.
- [`scripts/e2e_changes.py`](../../scripts/e2e_changes.py) — the `changes`-job decision module,
  including `job_scenario_map` and the `affected` computation.
- [`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) — the iOS lane, whose
  `codegen` and `visual` jobs this item keys on scenarios.
- [`demos/showcase/Makefile`](../../demos/showcase/Makefile) — the `ui-test`, `ui-test-coverage`, and
  `e2e-visual` targets that name the scenarios each job runs.
