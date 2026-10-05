**English** · [日本語](BE-0453-per-scenario-result-json-ja.md)

# BE-0453 — Persist each scenario's result as soon as it finishes

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0453](BE-0453-per-scenario-result-json.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0453") |
| Implementing PR | [#2128](https://github.com/bajutsu-e2e/bajutsu/pull/2128) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

`bajutsu run` runs a suite of scenarios and records their verdicts in `manifest.json`. That file is
the run's single source of truth. The runner writes it, with `junit.xml`, `ctrf.json`, and
`report.html`, once the last scenario finishes. We propose that the runner also write each verdict
to `<sid>/result.json` the moment that scenario ends. Here `<sid>` is the scenario's evidence
directory. A run that stops partway through a suite then keeps the verdict of every scenario it
completed.

## Motivation

A suite that drives a real device or browser can take a long time. A run can also stop early for
reasons outside the scenarios themselves. The machine can go down, or a continuous integration (CI)
job can hit its timeout. An operator can also stop the process by hand. Such a run leaves evidence
files, such as screenshots and `network.json`, but no verdict for any scenario. Every verdict stays
in memory until the runner writes the final report. Picture an operator who ran thirty scenarios and
lost the process during the last one. That operator learns nothing about the twenty-nine that
finished.

[BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md) handles one case of the
problem. A cancel request lets the scenario loop finish in order, so the run still writes its
report. That path needs the process to cooperate. A `SIGKILL` or an out-of-memory termination
gives it no chance.

The observable outcome: a run stopped during its Nth scenario leaves a `result.json` for each of
the first N−1 scenarios, each stating that scenario's verdict.

## Detailed design

### What the runner writes

When a scenario finishes, the runner writes `<run_dir>/<sid>/result.json` before it starts the next
scenario. The file has two keys:

| Key | Content |
|---|---|
| `schemaVersion` | The `manifest.json` schema version, so a reader knows which entry shape it holds |
| `scenario` | The same entry `manifest.json` lists for this scenario under `scenarios` |

Reusing the manifest entry spares a reader of the partial records a second parser. It also keeps
the two records from drifting apart. `manifest.json` stays the single source of truth for a run that
completes. `result.json` is the partial record an interrupted run leaves behind.

### Where the write happens

The scenario runner's per-scenario entry point writes the file once the scenario's result is
final. A crash-recovery retry counts as part of the same scenario, so the file holds the last
attempt's result. The write happens with `--trace-driver` on or off. The write masks secret values
exactly as `manifest.json` does. It does not apply the config's `redact.fields`, which masks
matching JSON keys: the entry's own keys, such as `reason`, would then differ from the manifest's.
A run with no run directory writes nothing.

### Parallel and cross-browser runs

With `--workers`, each worker writes the file of the scenario it ran. Every scenario has its own
`<sid>`, so no two workers share a file and the runner takes no lock.

A cross-browser matrix run writes under `<run_dir>/<engine>/<sid>/`, beside the rest of that
scenario's evidence. The matrix tags each result with its engine only after the engine's whole pass
ends. In this file, then, the parent directory names the engine, the entry's `engine` is empty, and
artifact paths are relative to `<run_dir>/<engine>/`. The finished `manifest.json` carries the
tagged form.

### A failed write

The file records partial progress; it does not decide the verdict. When the write raises an
operating-system error, the runner logs a warning and continues. `driver_trace.json` already gets
the same treatment. `manifest.json` still carries the scenario's result once the run completes.

### Prime directives

The deterministic runner writes the file from a result it already computed. No large language
model (LLM) enters the run, and the verdict stays the same. The feature needs no per-app
configuration.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Append one line per scenario to a run-level `results.jsonl` | Parallel workers would share one file and need a lock; a per-scenario file sits beside that scenario's evidence |
| Regenerate `manifest.json`, `junit.xml`, and `report.html` after each scenario | Rebuilds every report once per scenario, and a reader could mistake a partial manifest for a finished run |
| Rely on graceful cancellation (BE-0370) alone | Covers a requested cancel, not a process stopped without warning |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] The runner writes `<sid>/result.json` as each scenario finishes
- [x] An error on the write logs a warning instead of ending the run
- [x] Tests cover serial, parallel, crash-recovery, `--trace-driver`, and failed-write runs
- [x] `docs/reporting.md`, `docs/architecture.md`, and their Japanese mirrors document the file

Log:

- [#2128](https://github.com/bajutsu-e2e/bajutsu/pull/2128) — Proposal and implementation together,
  completing the item. With `--trace-driver` on, the verdict is written before the trace is flushed,
  so a process killed while serializing the trace still keeps the finished scenario's result.

## References

- [BE-0370 — Finish a cancelled run gracefully](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md)
- [BE-0415 — Driver call trace per scenario](../BE-0415-driver-call-trace-per-scenario/BE-0415-driver-call-trace-per-scenario.md)
- [`docs/reporting.md`](../../docs/reporting.md)
