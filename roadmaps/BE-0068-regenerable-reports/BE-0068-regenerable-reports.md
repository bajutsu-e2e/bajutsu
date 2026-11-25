**English** · [日本語](BE-0068-regenerable-reports-ja.md)

# BE-0068 — Regenerable reports (render from stored run data)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0068](BE-0068-regenerable-reports.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0068") |
| Implementing PR | [#282](https://github.com/bajutsu-e2e/bajutsu/pull/282) (the render-on-view slice completing the item) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

This item makes the run report a **pure rendering of data already stored in the run directory**. The report can then be regenerated from a finished run at any time, without re-executing the scenario.

Today `report.html` is baked once, during `run`, from the live in-memory result. A better report template leaves every past run frozen with the old report. Such improvements include a new tab, a clearer layout, a fixed rendering bug, and a new view computed from already-captured evidence. The only way to refresh a past report is to run the scenario again.

This proposal introduces a **versioned report data contract**, the persisted model that the renderer consumes. It routes two surfaces through the one renderer:

- An offline `bajutsu report <run>` command re-renders `report.html` for an existing run and re-emits `junit.xml`.
- The `serve` web UI renders each report on view from the stored model with the current template. A report seen in the browser is thus always current.

The change adds no LLM anywhere. It never re-runs an assertion or alters a verdict, because the render only re-presents recorded outcomes. It therefore sits squarely inside the determinism-first contract.

## Motivation

A finished run is already **self-describing**. The deterministic runner writes these files:

- `manifest.json`, the serialized result with every step, assertion, duration, device, and artifact path
- the executed `scenario.yaml`
- the evidence tree (`screenshot`, `elements.json`, `network.json`, video, and device log)

The report is a *view* over exactly this data. Yet the run produces that view **only at the moment of execution**. `bajutsu/runner/pipeline.py` calls `write_report`, which renders `bajutsu/templates/report.html.j2` once and writes `runs/<id>/report.html`. From then on, the HTML is a fixed artifact. Data and presentation are coupled where they should be separable, and the coupling bites in several concrete ways:

1. **Report improvements never reach past runs.** Every change to the report only affects runs executed *after* the change. Such changes include a new evidence tab, a better step table, a rendering bug fix, and a summary computed from data the run already captured. A two-week-old failure report keeps the old presentation. The single hub artifact of the whole tool cannot be re-presented ([DESIGN §9](../../DESIGN.md): `manifest.json` is "the single source of truth for the report and CI").
2. **"Re-run to refresh" is wasteful, and often impossible.** Re-executing a scenario just to pick up a template change spends a Simulator, minutes, and (on AI paths) tokens. It recomputes a result that is already on disk. Worse, re-running is frequently *not an option*: the failure was a one-off, the app build or backend state has moved on, or the run is a release record that must not change. Refreshing the presentation should not require re-execution.
3. **A hosted or shared `serve` shows stale reports.** When `serve` ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)) is upgraded, every report it serves is still the HTML baked by whichever version ran that scenario. A browser user who compares two runs sees two different report layouts for one reason: *when* each run happened.

No path today reads a stored run back into the renderer. A `manifest_dict` serializes the result out, but no inverse loads it in. `html_report` takes live `RunResult` objects plus side inputs rather than one persisted model.

Closing that gap is the missing **re-render** half of the report subsystem. It needs a versioned model that is the *complete* render input, and a loader for it. The work is a pure presentation concern, so it fits the determinism-first contract by construction.

## Detailed design

### One renderer, a persisted model, two surfaces

The load-bearing change makes the renderer a **pure function of a persisted model**. Today `html_report(run_id, results, run_dir, definitions, sources, …)` consumes live `RunResult` objects together with side inputs. The `definitions` and `sources` come from the in-memory scenarios.

The redesign defines the input of the renderer as **one serializable model written in full to the run directory**. Both the initial bake and every later re-render read *that* model. The two can never diverge in what a report shows.

The natural home for the model is the existing **`manifest.json`**. The change promotes it from a summary to the canonical, lossless render input, which carries a **`schemaVersion`**. The invariant is that **every input the renderer needs is recoverable from the run directory**:

- the per-scenario and per-step outcomes
- the executed scenario (structured definition and YAML source)
- device metadata
- artifact paths
- network exchanges
- dismissed alerts

The work audits the inputs of `html_report` against what is persisted and closes each gap. It also adds the inverse of `manifest_dict`: a loader that reconstructs the render model from `manifest.json`, plus `scenario.yaml` and the evidence under `run_dir`.

Whether the whole model lives in `manifest.json` or in a sibling `report.json` is a small open detail. The load-bearing decision is that the render input is *persisted in full and versioned*, and that one renderer serves every surface.

### Offline surface — `bajutsu report <run>`

A new command re-renders a finished run from its stored artifacts, using the **current** template. `bajutsu report <run-id|path>` rewrites `runs/<id>/report.html` in place and re-emits `junit.xml`. `bajutsu report --all` (or a glob) re-bakes every run under `runs/`. The command launches no device and calls no model. It reads the persisted model and renders.

This is the path for refreshing the portable, self-contained `report.html`. CI uploads that file, and [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) packages it as a zip. The exact command spelling is a small open detail. The load-bearing decision is that the command shares the one renderer with `serve` and `run`.

### serve surface — render on view

`serve` renders the report **dynamically on each view** from the stored model with the current template. It goes through the existing `ArtifactStore` boundary (`bajutsu/serve/artifacts.py`), which already confines run-file access.

The baked `report.html` becomes a regenerable cache and export artifact rather than the canonical view. Upgrading `serve` thus refreshes every report it shows at once. No per-run re-bake step remains, and two runs no longer render differently because of when they ran.

### What re-rendering can and cannot reflect (the honest boundary)

Re-rendering reflects **presentation, and any view derivable from already-captured data**. Examples are layout, styling, a fixed rendering bug, and a new tab or summary computed from the evidence the run already holds.

It **cannot** retroactively populate **data a past run never captured**, such as a new evidence kind or a field added to the model later. For such a run, the renderer reads the older `schemaVersion` and shows the absent section as "not captured for this run". It neither fails nor invents a value.

The promise stays precise: re-rendering reflects template and feature changes *that derive from data the run already captured*. A genuinely new capture still needs a fresh run. The explicit marking of what is absent follows the honesty that the runner already practices for skipped evidence ([DESIGN §10](../../DESIGN.md)).

### Determinism, the gate, and the verdict

- **No LLM, no effect on pass/fail.** Rendering re-presents outcomes that the deterministic `run` already recorded, and it never re-evaluates an assertion. The verdict of a run is read from the stored model and never recomputed, so re-rendering cannot change it. Prime directives 1 and 2 ([CLAUDE.md](../../CLAUDE.md)) hold by construction. The result strengthens determinism, because the report becomes a reproducible function of stored evidence ([DESIGN §2](../../DESIGN.md)).
- **App-agnostic.** Rendering depends on the run model and on no app. Nothing here touches the `apps.<name>` config, the drivers, or the runner.
- **Linux-testable.** Re-rendering needs no Simulator, because it reads files and renders. The loader, the renderer, and the command are unit-tested on the existing Linux gate against a fixture run directory.

### The test contract (machine-checkable)

Tests that need no Simulator pin four properties:

- (a) **Round-trip.** Loading the persisted model back reconstructs the input of the renderer without loss. `load(persist(result))` carries every field that the report uses.
- (b) **Purity.** Re-rendering a fixture run twice is byte-identical. A fresh `run` bakes a report that equals the report `bajutsu report` produces for the same run and template. The render depends only on stored data, with timestamps drawn from that data and not from the wall clock.
- (c) **Older versions still render.** A fixture written at an earlier `schemaVersion` renders without error, with newer-only sections shown as "not captured".
- (d) **No verdict drift.** The pass/fail of the re-rendered report matches the stored model for every scenario.

### Out of scope

Three topics stay out of scope:

- Adding *new* evidence kinds or capturing more during a run (the evidence subsystem, [DESIGN §9](../../DESIGN.md)).
- Cross-run report diffing or a multi-run dashboard. The data contract enables both as a natural follow-on, but they form a separate item.
- Any change to what `run` decides or captures.

## Alternatives considered

- **Keep baking only; re-run to refresh (the status quo).** This is the problem itself. A re-run spends a Simulator and minutes (and tokens, on AI paths) to recompute a result already on disk. It cannot run at all when the failure was a one-off, the app or backend state has moved on, or the run is an immutable release record.
- **serve renders dynamically, but no offline command.** This refreshes the browser view but leaves the portable `report.html` frozen at its baked version. CI uploads that artifact, and [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) zips it. Rejected as half a solution. The offline re-render keeps shared and archived reports current.
- **An offline command, but serve keeps serving the baked file.** This refreshes artifacts on demand, but the web UI still shows stale HTML until each run is re-baked. Rejected for the same reason from the other side. Both surfaces are wanted, which is why they share one renderer.
- **A separate `report.json` instead of elevating `manifest.json`.** This keeps `manifest.json` a lean summary. It also adds a second near-duplicate artifact and a way for the two to drift. The item prefers reusing `manifest.json` as the single versioned source. A separate file is the fallback only if the manifest must stay a summary. Either way, the model is persisted in full and carries a `schemaVersion`.
- **Ship raw JSON plus a client-side JS app (render in the browser).** The report is deliberately a self-contained static HTML with inline assets and relative links. It opens by double-click and zips cleanly ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md), [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)). A JSON + SPA would break that portability. The renderer stays server-side and offline, and it keeps emitting self-contained HTML.
- **Auto-rebake on a template-version mismatch.** Tempting, but silently rewriting the artifacts of a past run on read is surprising and at odds with treating a run as a record. The explicit `bajutsu report` command plus the render-on-view of serve cover the need without mutating old runs behind the back of the user. A later change could revisit an opt-in auto-refresh.
- **A dedicated "Reporting" roadmap topic.** Deferred. This item is filed under *Authoring experience* alongside [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md), following the precedent that BE-0060 set (one topic until siblings accumulate). This item and BE-0060 are the first two report-subsystem proposals. If more arrive, a "Reporting" topic can be carved out then.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [CLAUDE.md](../../CLAUDE.md), [DESIGN §2](../../DESIGN.md) — AI never judges; determinism
  first. Re-rendering adds no LLM and never recomputes a verdict; it makes the report a
  reproducible function of stored data.
- [DESIGN §9](../../DESIGN.md) — `manifest.json` as the single source of truth for the report
  and CI; this proposal makes it literally the render input.
- [DESIGN §10](../../DESIGN.md) — being explicit about skipped / absent evidence; the model for
  the graceful-degradation boundary above.
- [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)
  — the embedded report this makes render-on-view, and the relative-link, self-contained report it
  must keep emitting.
- [BE-0060 — Download / export a run report as a zip](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)
  — complementary: BE-0060 packages a run dir; regeneration keeps the report inside it current, and
  a bundled versioned model keeps an exported zip re-renderable later.
- `bajutsu/report/` (`html.py`, `manifest.py`, `panels.py`, `templates/report.html.j2`),
  `bajutsu/runner/pipeline.py`, `bajutsu/serve/`, [reporting.md](../../docs/reporting.md) — the
  renderer, the manifest writer, the run pipeline that bakes the report, and the serve surface this
  re-renders through.
