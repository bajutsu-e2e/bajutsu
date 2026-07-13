**English** · [日本語](BE-0031-data-driven-scenarios-ja.md)

# BE-0031 — Data-driven scenarios

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0031](BE-0031-data-driven-scenarios.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0031") |
| Implementing PR | [#6](https://github.com/bajutsu-e2e/bajutsu/pull/6) |
| Topic | Scenario authoring features |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

Repeat one scenario over multiple rows via `data` (inline) / `dataFile` (CSV). Substitute `${row.*}` per row (`expand_data`). Useful for multilingual and boundary-value testing.

## Motivation

The same flow often needs running against many inputs — a search across several queries, a form across boundary values, a screen across locales. Written by hand, that means one near-identical scenario per case: dozens of copies differing only in a few literals, all of which must be edited together when the flow changes. Authors need to write the flow once and supply a table of inputs, so coverage grows by adding a row rather than duplicating a scenario.

## Detailed design

A scenario carries its inputs as `data` (a list of inline row mappings) or `dataFile` (a CSV path); the two are mutually exclusive (`Scenario` in `bajutsu/scenario/` rejects both). Before the run, `expand_data` replaces each such scenario with one derived scenario per row, substituting `${row.<column>}` through the shared `${...}` interpolation primitive (`interp.interpolate`, keyed `row.<column>`). A CSV `dataFile` has a header row naming the columns and is read by `read_csv`; a scenario with neither field passes through untouched.

Each derived scenario is renamed `"<name> [row N: col=val, …]"` so reports and JUnit testcases stay distinct, and keeps the original's preconditions — including the `erase` default — so every row reinstalls the app fresh and runs in its own clean environment. Interpolation preserves types: a string that is exactly one token (`"${row.id}"`) takes the raw bound value, while a token embedded in a larger string is spliced in as text. Expansion is purely load-time and removes the `data`/`dataFile` fields, so the deterministic runner only ever sees plain scenarios with literals in place — determinism and per-row isolation are both preserved.

## Alternatives considered

**A runtime loop over rows inside one scenario.** Rejected: it would share one device and one evidence stream across all rows, so a failure on row 3 would not be cleanly isolated and the report could not show each case as its own pass/fail. Expanding to one scenario per row gives each case its own fresh device, evidence, and JUnit testcase.

**An external test-matrix harness (parameterize from outside the tool).** Rejected: it pushes the input table out of the scenario file and into wrapper scripting, breaking the "scenario YAML is the shared hub" premise and the ability to review inputs alongside steps. Keeping `data`/`dataFile` in the scenario keeps the cases versioned and reviewable with the flow they exercise.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/scenario/` (`expand_data`), [scenarios.md](../../docs/scenarios.md)
