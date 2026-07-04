**English** · [日本語](BE-0034-tags-selective-runs-ja.md)

# BE-0034 — Tags / labels + selective runs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0034](BE-0034-tags-selective-runs.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0034") |
| Implementing PR | [#6](https://github.com/bajutsu-e2e/bajutsu/pull/6) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

Run a subset of scenarios by `tags` with `--tag`/`--exclude` (include/exclude, exclude wins, `select_scenarios`). Useful for staged CI runs.

## Motivation

As a suite grows, not every run should execute every scenario. A pre-merge gate wants a fast smoke subset; a nightly run wants the full set; a feature branch wants only the scenarios touching that feature; a flaky or platform-specific scenario sometimes needs excluding from a given run. Without a selection mechanism, the only knobs are "run this one file" or "run everything," which forces awkward file layouts and rules out staged CI pipelines. Authors need to label scenarios and pick subsets by label at run time, without splitting or reorganizing the scenario files.

## Detailed design

Each scenario carries an optional `tags` list (`Scenario` in `bajutsu/scenario/`) — free-form labels such as `smoke` or `checkout`. The CLI exposes `--tag` and `--exclude`, each a comma-separated list, applied after the full set of scenarios has been loaded and expanded (so selection sees every data-driven and component-expanded scenario). `select_scenarios` keeps a scenario when it carries at least one `--tag` (or no `--tag` was given) **and** none of its tags appear in `--exclude`; `--exclude` wins over `--tag`. Filtering is pure metadata selection — it preserves declaration order and never mutates a scenario — and when the filters select nothing the CLI exits cleanly with a clear message rather than running an empty suite. Selection happens entirely before the deterministic run loop, so it has no effect on how the chosen scenarios execute or on their pass/fail; tags carry no semantics beyond selection.

## Alternatives considered

**One scenario file per subset (organize selection by file layout).** Rejected: it couples a scenario's selection to its location, forces duplication when a scenario belongs to several subsets (e.g. both `smoke` and `checkout`), and breaks down as the cross-product of subsets grows. Tags let a scenario belong to many subsets at once while living in its natural file.

**Include-only selection (no `--exclude`).** Rejected: excluding a small set ("everything except the slow ones") would otherwise require enumerating every wanted tag. Supporting both directions, with `--exclude` taking precedence, covers staged pipelines naturally — a broad include minus a few known exclusions.

**A query/expression language over tags (boolean combinations).** Rejected as over-engineered for the need: comma-separated include/exclude lists with a clear precedence rule cover the staged-CI use cases without the parsing surface and ambiguity of a full expression grammar. A richer grammar can be layered on later if a concrete need appears.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/scenario/` (`select_scenarios`), [cli.md](../../../docs/cli.md)
