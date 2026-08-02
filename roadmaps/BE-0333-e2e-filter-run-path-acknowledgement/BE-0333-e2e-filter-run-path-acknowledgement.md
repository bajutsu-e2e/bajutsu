**English** · [日本語](BE-0333-e2e-filter-run-path-acknowledgement-ja.md)

# BE-0333 — Force every run-path file to be either gated or explicitly excluded in the E2E relevance filter

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0333](BE-0333-e2e-filter-run-path-acknowledgement.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0333") |
| Implementing PR | [#1446](https://github.com/bajutsu-e2e/bajutsu/pull/1446) |
| Topic | CI / build infrastructure |
<!-- /BE-METADATA -->

## Introduction

[`scripts/e2e_changes.py`](../../scripts/e2e_changes.py) decides, per lane, whether a pull request runs
the three on-device end-to-end (E2E) lanes: iOS on the Simulator, Android on an emulator, and web in a
real browser. Each lane carries a required status check that a `paths:` trigger filter cannot gate,
because a required check skipped that way stays pending and blocks the merge forever. Every lane
therefore triggers on every pull request, and this filter, a hand-written positive list of paths,
decides whether the metered jobs actually run. A path the list does not mention fires nothing, and the
always-reporting aggregator then passes **without having exercised the change at all**.

That default has produced three separate misses, each found and fixed on its own. This item proposes
the structural end of the class: **invert the default for `bajutsu/`, so an unrecognized file fires
every lane instead of none**, and add a static cross-check that a file classified as periphery has not
quietly joined the run path. A file whose classification nobody has decided then fails `make check`,
rather than defaulting to "not gated" and surfacing months later as a mysteriously green required
check.

## Motivation

The positive list is a hand-maintained duplicate of a fact the code already knows, namely which files
the run path imports. A duplicate drifts, and because the default is "not gated", it drifts silently.

Three misses are on record, in two distinct failure modes:

| Miss | Cause | How long it went unnoticed |
|---|---|---|
| `bajutsu/config` | split into a package by BE-0252, so the by-name entry anchored with `\.py$` stopped matching | roughly two weeks |
| `bajutsu/platform_lifecycle` | the same package split | until PR [#1403](https://github.com/bajutsu-e2e/bajutsu/pull/1403) changed the XCUITest cold spawn and the whole macOS fleet skipped |
| the BE-0304 doctor gate | `bajutsu/doctor.py`, `cli/commands/doctor.py`, and `preflight.py` were never listed, though all three lanes run `bajutsu doctor --environment-only` | since the gate landed |

Guards added alongside those fixes close three narrow classes: a by-name module becoming a package, a
renamed or deleted path, and a new file under either per-backend directory (`bajutsu/drivers/`,
`bajutsu/platform_lifecycle/environments/`). Two classes remain open. A **new** top-level
`bajutsu/*.py` module or command-line interface (CLI) command on the run path still defaults to firing
nothing. And a measured walk of the run path's import closure finds 54 files that fire no lane today,
among them `bajutsu/report/`, which every run writes through `runner/pipeline.py`, along with
`deprecations.py`, `object_store.py`, `record_capture.py`, `requirements.py`, and `trace.py`.

The obvious fix, deriving the list from the import closure automatically, does not work, and measuring
it is what rules it out. The closure of the run path reaches `bajutsu/ai/`, `bajutsu/analytics/`, and
`bajutsu/github/`, because `record.py` imports the agent factory and `runner/pipeline.py` imports the
report writer, which imports the analytics ledger. Gating on the raw closure would fire all three
metered lanes on a change confined to the served web user interface, the exact over-trigger PR
[#936](https://github.com/bajutsu-e2e/bajutsu/pull/936) fixed. An import edge records that one module
can reach another, not that its behavior can change what a run does, and only a person can draw that
line. This item therefore keeps the human judgment and changes which way an undecided file falls.

## Detailed design

Four units, each landable on its own.

### Unit 1 — Invert the default for `bajutsu/`

Sweep `bajutsu/` in the shared core, minus an explicit list of periphery prefixes the E2E never
exercises: the served web user interface and its templates, the analytics and analysis stacks, the
Model Context Protocol (MCP) server, the AI provider adapters, the GitHub integration, and the crawl
and agent modules the run does not import. PR
[#1409](https://github.com/bajutsu-e2e/bajutsu/pull/1409) already applied this shape to the two
per-backend directories; Unit 1 widens it to the package root.

The property that matters is the direction of the default. Naming a prefix in the exclusion list
narrows a *known* file, and never decides whether a *new* file is seen at all. A module added anywhere
under `bajutsu/` fires all three lanes until somebody classifies it, so the cost of forgetting becomes
a wasted job rather than an unexercised required check.

The exclusion list needs keying with care. A prefix such as `bajutsu/serve/` is safe to exclude
wholesale, because the web lane claims it separately for the serve-UI dogfood. A mixed package is not:
`bajutsu/report/` holds the manifest writer every run invokes next to the HTML rendering no lane needs,
so it is excluded file by file or not at all.

### Unit 2 — A static cross-check against the run path's import closure

Unit 1 makes a new file over-fire, but it cannot catch the reverse error, which is a module already on
the exclusion list that the run path *starts* importing. Add a test that walks the import closure
statically, with `ast`, and fails when a file in the closure is neither matched by the filter nor
covered by an exclusion entry.

The walk must parse rather than import. The `changes` job runs a bare `python3` with no dependencies
installed, and the test belongs inside the fast `make check` gate, which needs no Simulator. Static
analysis on the import graph is the technique `lint-imports` already applies to the layer contract, so
Unit 2 introduces no new class of tooling and puts no large language model anywhere near the decision.

### Unit 3 — Retire the ad-hoc parity tests this subsumes

Two tests currently encode one-off exclusion decisions in prose:
`test_agent_factory_is_not_relevant_by_parity`, and `test_untouched_subpackage_is_not_relevant`, which
pins `bajutsu/report/` as not relevant. Restate both as entries in the Unit 1 exclusion list, each
carrying its reason, so every deliberate exclusion lives in one place and reads as a decision rather
than as an assertion about the past.

### Unit 4 — Measure the cost of the new default

Inverting the default trades unexercised required checks for wasted metered jobs, and the trade holds
only while the waste stays small. Sample the merged pull requests of a recent period, classify each by
which lanes the new filter fires against which the old one fired, and report the difference. Should a
periphery prefix churn often enough to matter, that is the signal to key its exclusion more finely.

## Alternatives considered

- **Derive the allow-list from the import closure.** Rejected on measurement rather than on principle:
  the closure reaches the AI, analytics, and GitHub stacks, so gating on it reinstates the over-trigger
  PR #936 fixed. Recorded here because it is the first idea any reader will have.
- **Compute the closure inside `e2e_changes.py` at CI time.** Rejected. The `changes` job deliberately
  runs a bare `python3` with no dependencies, which is why the module's own scenario scanner is a line
  scan rather than a YAML parse, and moving the classification out of review would discard the human
  judgment Unit 1 keeps.
- **Keep the positive list and rely on review.** This is the status quo, and three misses in roughly six
  weeks is the evidence against it. Each was found by accident rather than by review: two while
  investigating an unrelated skipped lane, and one while auditing the first two.
- **Widen the required checks to run every lane unconditionally.** This removes the class outright, but
  gives up the saving the filter exists for, so a documentation-only pull request would boot a Simulator
  and an emulator. Rejected as disproportionate.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — sweep `bajutsu/` minus an explicit periphery exclusion list, inverting the default.
- [x] Unit 2 — add the static `ast` import-closure cross-check.
- [x] Unit 3 — restate the two ad-hoc parity tests as exclusion entries with reasons.
- [x] Unit 4 — measure the over-fire difference on a recent sample of merged pull requests.

Log (oldest first):

- All four units land together in the implementing PR. `scripts/e2e_changes.py` now sweeps `bajutsu/`
  minus two derived exclusion sets — `_PERIPHERY_EXCLUSIONS` (the periphery no lane exercises, each
  entry carrying its reason) and `_LANE_CLAIMED` (the per-backend leaves each lane reclaims) — so an
  unclassified file over-fires instead of firing nothing (Unit 1). `bajutsu/report/`, the manifest
  writer every run invokes, and the previously-unlisted `deprecations` / `object_store` /
  `record_capture` / `from_grouping` modules now fire. `tests/test_e2e_changes.py` walks the run
  path's static `ast` import closure and fails if any file it reaches is neither gated nor a
  classified periphery entry (Unit 2), and the two former ad-hoc parity assertions
  (`test_agent_factory_is_not_relevant_by_parity`, `test_untouched_subpackage_is_not_relevant`) are
  now reasoned entries in `_PERIPHERY_EXCLUSIONS` (Unit 3). The by-name package-split guards this
  subsumes were retired. `scripts/e2e_overfire_report.py` measures the trade against a baseline commit
  (Unit 4): across the last 80 merged pull requests the new default fired **identically** to the old
  on all three lanes — 0 over-fire, 0 under-fire — because the newly-gated files never appeared in a
  PR that was not already firing on other run-path code. The accepted cost is real for a future
  report-only or object-store-only change, but negligible on recent history.

## References

- [`scripts/e2e_changes.py`](../../scripts/e2e_changes.py) — the filter this item restructures.
- [`docs/ci.md`](../../docs/ci.md) — how the lanes, the `changes` job, and the required aggregators fit
  together.
- [BE-0279](../BE-0279-crossbackend-e2e-required-gate/BE-0279-crossbackend-e2e-required-gate.md) — the
  cross-backend required gate that makes an unexercised green check possible in the first place.
- [BE-0322](../BE-0322-e2e-scenario-scoped-filter/BE-0322-e2e-scenario-scoped-filter.md) — the
  scenario-scoped narrowing built on this filter, and the precedent for over-selecting toward safety.
- [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md) — the existing
  static import-graph contract (`lint-imports`), the precedent Unit 2 follows.
- PR [#1405](https://github.com/bajutsu-e2e/bajutsu/pull/1405), PR
  [#1408](https://github.com/bajutsu-e2e/bajutsu/pull/1408), and PR
  [#1409](https://github.com/bajutsu-e2e/bajutsu/pull/1409) — the three fixes and the guards that
  motivated this item.
