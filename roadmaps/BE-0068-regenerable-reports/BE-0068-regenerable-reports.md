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

Make the run report a **pure rendering of data already stored in the run directory**, so it can
be regenerated from a finished run at any time — without re-executing the scenario. Today
`report.html` is baked once, during `run`, from the live in-memory result; improving the report
template (a new tab, a clearer layout, a fixed rendering bug, a new view computed from
already-captured evidence) leaves every past run frozen with the old report, and the only way to
refresh one is to run the scenario again. This proposal introduces a **versioned report data
contract** — the persisted model the renderer consumes — and routes two surfaces through the one
renderer: an offline `bajutsu report <run>` command that re-renders `report.html` (and re-emits
`junit.xml`) for an existing run, and a `serve` web UI that renders each report on view from the
stored model with the current template, so a report seen in the browser is always current. It
adds no LLM anywhere, and never re-runs an assertion or alters a verdict — the render only
re-presents recorded outcomes — so it sits squarely inside the determinism-first contract.

## Motivation

A finished run is already **self-describing**: the deterministic runner writes `manifest.json`
(the serialized result — every step, assertion, duration, device, and artifact path), the
executed `scenario.yaml`, and the evidence tree (`screenshot` / `elements.json` / `network.json`
/ video / device log). The report is a *view* over exactly this data. Yet that view is produced
**only at the moment of execution**: `bajutsu/runner/pipeline.py` calls `write_report`, which
renders `bajutsu/templates/report.html.j2` once and writes `runs/<id>/report.html`. From then on
the HTML is a fixed artifact. Data and presentation are coupled where they should be separable,
and the coupling bites in several concrete ways:

1. **Report improvements never reach past runs.** Every change to the report — a new evidence
   tab, a better step table, a rendering bug fix, a summary computed from data the run already
   captured — only affects runs executed *after* the change. A two-week-old failure report keeps
   the old presentation. The single hub artifact of the whole tool ([DESIGN §9](../../DESIGN.md):
   `manifest.json` is "the single source of truth for the report and CI") cannot be re-presented.
2. **"Re-run to refresh" is wasteful, and often impossible.** Re-executing a scenario just to pick
   up a template change spends a Simulator, minutes, and (on AI paths) tokens to recompute a
   result that is already on disk. Worse, it is frequently *not an option*: the failure was a
   one-off, the app build or backend state has moved on, or the run is a release record that must
   not change. Refreshing the presentation should not require re-execution.
3. **A hosted or shared `serve` shows stale reports.** When `serve`
   ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)) is
   upgraded, every report it serves is still the HTML baked by whatever version ran each scenario.
   A browser user comparing two runs sees two different report layouts for no reason other than
   *when* each was run.

No path today reads a stored run back into the renderer: a `manifest_dict` serializes the result
out, but there is no inverse that loads it in, and `html_report` takes live `RunResult` objects
plus side inputs rather than one persisted model. Closing that gap — a versioned model that is the
*complete* render input, and a loader for it — is the missing **re-render** half of the report
subsystem, and it is a pure presentation concern that fits the determinism-first contract by
construction.

## Detailed design

### One renderer, a persisted model, two surfaces

The load-bearing change is to make the renderer a **pure function of a persisted model**. Today
`html_report(run_id, results, run_dir, definitions, sources, …)` consumes live `RunResult`
objects together with side inputs (`definitions` / `sources` taken from the in-memory scenarios).
The redesign defines the renderer's input as **one serializable model written in full to the run
directory**, and has both the initial bake and every later re-render read *that* model — so the
two can never diverge in what a report shows.

The natural home for the model is the existing **`manifest.json`**: promote it from a summary to
the canonical, lossless render input, carrying a **`schemaVersion`**. The invariant is that
**every input the renderer needs is recoverable from the run directory**: the per-scenario /
per-step outcomes, the executed scenario (structured definition and YAML source), device
metadata, artifact paths, network exchanges, and dismissed alerts. The work is to audit
`html_report`'s inputs against what is persisted, close each gap, and add the inverse of
`manifest_dict` — a loader that reconstructs the render model from `manifest.json` (plus
`scenario.yaml` and the evidence under `run_dir`). Whether the whole model lives in
`manifest.json` or a sibling `report.json` is a small open detail; the load-bearing decision is
that the render input is *persisted in full and versioned*, and that one renderer serves every
surface.

### Offline surface — `bajutsu report <run>`

A new command re-renders a finished run from its stored artifacts, using the **current**
template. `bajutsu report <run-id|path>` rewrites `runs/<id>/report.html` in place (and re-emits
`junit.xml`); `bajutsu report --all` (or a glob) re-bakes every run under `runs/`. It launches no
device and calls no model — it reads the persisted model and renders. This is the path for
refreshing the portable, self-contained `report.html` that CI uploads and that
[BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) packages
as a zip. (The exact command spelling is a small open detail; the load-bearing decision is that it
shares the one renderer with `serve` and `run`.)

### serve surface — render on view

`serve` renders the report **dynamically on each view** from the stored model with the current
template, through the existing `ArtifactStore` boundary (`bajutsu/serve/artifacts.py`) that
already confines run-file access. The baked `report.html` becomes a regenerable cache / export
artifact rather than the canonical view, so upgrading `serve` immediately refreshes every report
it shows — no per-run re-bake step, and no reason for two runs to render differently because of
when they ran.

### What re-rendering can and cannot reflect (the honest boundary)

Re-rendering reflects **presentation, and any view derivable from already-captured data**: layout,
styling, a fixed rendering bug, a new tab or summary computed from the evidence the run already
holds. It **cannot** retroactively populate **data a past run never captured** — a new evidence
kind, or a field added to the model later. For such a run the renderer reads its older
`schemaVersion` and shows the absent section as "not captured for this run" rather than failing or
inventing a value. This keeps the promise precise: re-rendering reflects template and feature
changes *that derive from data the run already captured*; a genuinely new capture still needs a
fresh run. Being explicit about what is absent follows the same honesty the runner already
practices for skipped evidence ([DESIGN §10](../../DESIGN.md)).

### Determinism, the gate, and the verdict

* **No LLM, no effect on pass/fail.** Rendering re-presents outcomes the deterministic `run`
  already recorded; it never re-evaluates an assertion. A run's verdict is read from the stored
  model, never recomputed, so re-rendering cannot change it. Prime directives 1 and 2
  ([CLAUDE.md](../../CLAUDE.md)) hold by construction, and the result strengthens determinism:
  the report becomes a reproducible function of stored evidence ([DESIGN §2](../../DESIGN.md)).
* **App-agnostic.** Rendering depends on the run model, not on any app; nothing here touches
  `apps.<name>` config, the drivers, or the runner.
* **Linux-testable.** Re-rendering needs no Simulator — it reads files and renders — so the
  loader, the renderer, and the command are unit-tested on the existing Linux gate against a
  fixture run directory.

### The test contract (machine-checkable)

Pinned by tests that need no Simulator: (a) **round-trip** — loading the persisted model back
reconstructs the renderer's input without loss (`load(persist(result))` carries every field the
report uses); (b) **purity** — re-rendering a fixture run twice is byte-identical, and a fresh
`run`'s baked report equals the report `bajutsu report` produces for the same run and template
(the render depends only on stored data, with timestamps drawn from that data, not the wall
clock); (c) **older versions still render** — a fixture written at an earlier `schemaVersion`
renders without error, with newer-only sections shown as "not captured"; (d) **no verdict drift**
— the re-rendered report's pass/fail matches the stored model for every scenario.

### Out of scope

Adding *new* evidence kinds or capturing more during a run (the evidence subsystem,
[DESIGN §9](../../DESIGN.md)); cross-run report diffing or a multi-run dashboard (a natural
follow-on the data contract enables, but a separate item); and any change to what `run` decides or
captures.

## Alternatives considered

* **Keep baking only; re-run to refresh (the status quo).** This is the problem itself: it spends
  a Simulator and minutes (and tokens, on AI paths) to recompute a result already on disk, and it
  cannot run at all when the failure was a one-off, the app or backend state has moved on, or the
  run is an immutable release record.
* **serve renders dynamically, but no offline command.** Refreshes the browser view, but leaves
  the portable `report.html` — the artifact CI uploads and
  [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) zips —
  frozen at its baked version. Rejected as half a solution; the offline re-render is what keeps
  shared and archived reports current.
* **An offline command, but serve keeps serving the baked file.** Refreshes artifacts on demand,
  but the web UI still shows stale HTML until each run is re-baked. Rejected for the same reason
  from the other side. Both surfaces are wanted, which is why they share one renderer.
* **A separate `report.json` instead of elevating `manifest.json`.** Keeps `manifest.json` a lean
  summary, but adds a second near-duplicate artifact and a way for the two to drift. Reusing
  `manifest.json` as the single versioned source is preferred; a separate file is the fallback
  only if the manifest must stay a summary. Either way the model is persisted in full and carries
  a `schemaVersion`.
* **Ship raw JSON plus a client-side JS app (render in the browser).** The report is deliberately a
  self-contained static HTML with inline assets and relative links, so it opens by double-click
  and zips cleanly ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)). A
  JSON + SPA would break that portability. The renderer stays server-side / offline and keeps
  emitting self-contained HTML.
* **Auto-rebake on a template-version mismatch.** Tempting, but silently rewriting a past run's
  artifacts on read is surprising and at odds with treating a run as a record. The explicit
  `bajutsu report` command plus serve's render-on-view cover the need without mutating old runs
  behind the user's back; an opt-in auto-refresh could be revisited later.
* **A dedicated "Reporting" roadmap topic.** Deferred. Filed under *Authoring experience* alongside
  [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md),
  following the precedent BE-0060 itself set (one topic until siblings accumulate). This item and
  BE-0060 are the first two report-subsystem proposals; if more arrive, a "Reporting" topic can be
  carved out then.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

* [CLAUDE.md](../../CLAUDE.md), [DESIGN §2](../../DESIGN.md) — AI never judges; determinism
  first. Re-rendering adds no LLM and never recomputes a verdict; it makes the report a
  reproducible function of stored data.
* [DESIGN §9](../../DESIGN.md) — `manifest.json` as the single source of truth for the report
  and CI; this proposal makes it literally the render input.
* [DESIGN §10](../../DESIGN.md) — being explicit about skipped / absent evidence; the model for
  the graceful-degradation boundary above.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)
  — the embedded report this makes render-on-view, and the relative-link, self-contained report it
  must keep emitting.
* [BE-0060 — Download / export a run report as a zip](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)
  — complementary: BE-0060 packages a run dir; regeneration keeps the report inside it current, and
  a bundled versioned model keeps an exported zip re-renderable later.
* `bajutsu/report/` (`html.py`, `manifest.py`, `panels.py`, `templates/report.html.j2`),
  `bajutsu/runner/pipeline.py`, `bajutsu/serve/`, [reporting.md](../../docs/reporting.md) — the
  renderer, the manifest writer, the run pipeline that bakes the report, and the serve surface this
  re-renders through.
