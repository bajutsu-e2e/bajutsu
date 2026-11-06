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

Repeat one scenario over multiple rows via `data` (inline) or `dataFile` (CSV). Load-time expansion substitutes `${row.*}` per row (`expand_data`). The feature suits multilingual and boundary-value testing.

## Motivation

The same flow often needs running against many inputs: a search across several queries, a form across boundary values, or a screen across locales. Written by hand, that means one near-identical scenario per case. The result is dozens of copies that differ only in a few literals, and all of them must be edited together when the flow changes.

Authors need to write the flow once and supply a table of inputs. Coverage then grows by adding a row rather than by duplicating a scenario.

## Detailed design

A scenario carries its inputs as `data` (a list of inline row mappings) or `dataFile` (a CSV path). The two are mutually exclusive: `Scenario` in `bajutsu/scenario/` rejects both. Before the run, `expand_data` replaces each such scenario with one derived scenario per row. It substitutes `${row.<column>}` through the shared `${...}` interpolation primitive (`interp.interpolate`, keyed `row.<column>`). A CSV `dataFile` has a header row naming the columns, and `read_csv` reads it. A scenario with neither field passes through untouched.

Each derived scenario is renamed `"<name> [row N: col=val, …]"`, so reports and JUnit testcases stay distinct. It keeps the original's preconditions, including the `erase` default, so every row reinstalls the app fresh and runs in its own clean environment.

Interpolation preserves types. A string that is exactly one token (`"${row.id}"`) takes the raw bound value. A token embedded in a larger string is spliced in as text.

Expansion is purely load-time and removes the `data`/`dataFile` fields. The deterministic runner only ever sees plain scenarios with literals in place, so determinism and per-row isolation are both preserved.

## Alternatives considered

**A runtime loop over rows inside one scenario.** Rejected. It would share one device and one evidence stream across all rows, so a failure on row 3 would not be cleanly isolated, and the report could not show each case as its own pass/fail. Expanding to one scenario per row gives each case its own fresh device, evidence, and JUnit testcase.

**An external test-matrix harness (parameterize from outside the tool).** Rejected. It pushes the input table out of the scenario file and into wrapper scripting. That breaks the "scenario YAML is the shared hub" premise, and it breaks the ability to review inputs alongside steps. Keeping `data`/`dataFile` in the scenario keeps the cases versioned and reviewable with the flow they exercise.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/scenario/` (`expand_data`), [scenarios.md](../../docs/scenarios.md)
