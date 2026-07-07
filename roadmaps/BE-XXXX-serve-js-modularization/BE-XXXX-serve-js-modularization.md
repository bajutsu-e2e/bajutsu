**English** · [日本語](BE-XXXX-serve-js-modularization-ja.md)

# BE-XXXX — Split serve.js into section files without a build step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-js-modularization.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/templates/serve.js` has grown to ~2.5k lines and ~150 top-level declarations in one
file of build-step-free browser JavaScript, organized by section comments (login, config
browser, settings, record, replay, triage, stats, coverage, crawl plus its ~400-line graph,
codegen, tiling). This item splits it into a few section files delivered through the template
mechanism the server already uses for multiple assets, and unifies the three copy-pasted
job-start handlers. The deliberate no-bundler stance is unchanged.

## Motivation

The file is coherent but past the size one untested file supports: the ESLint guardrail
(BE-0129) was calibrated when the file was ~1.5k lines, and it has since grown ~65%. State is
module-level `let` globals per feature area with the DOM as the source of truth — workable per
section, hard to navigate across 2.5k lines.

No new tooling is needed to split it. `serve/handler.py` already renders `serve.css`,
`serve.themes.css`, and `serve.js` as separate Jinja-rendered assets, so serving two to four
JS files instead of one is pure file organization with zero build machinery.

One concrete internal duplication also belongs to this item: the job-start handlers for run
(`#go`), record (`#rec-go`), and crawl (`#crawl-go`) share an identical skeleton — close the
old stream, `setBusy(true)`, clear panes, POST `/api/{run,record,crawl}`, destructure
`{jobId,error}`, on error `setStatus` + `setBusy(false)`, else store the id and `streamJob(…)`.
A `startJob(…)` helper unifies the skeleton while the per-panel pane clearing stays at each
call site.

## Detailed design

1. Split `serve.js` into section files (2–4: e.g. core helpers + shared state, panel handlers,
   the crawl graph), each rendered as its own asset by the existing template mechanism in
   `serve/handler.py` — no bundler, no imports, same global-scope semantics as today, load
   order fixed by the template.
2. Add a `startJob(…)` helper and migrate the run / record / crawl start handlers onto it.
3. Extend `eslint.config.mjs`'s `files` list to the new file set (same rules).
4. Rely on the serve UI dogfood gate (BE-0189) to pin behavior across the split; extend a
   dogfood scenario only if a gap shows up.

## Alternatives considered

- **A bundler or framework.** Rejected by design (see `eslint.config.mjs`'s rationale): this is
  a Python repo and the UI's value is its zero-toolchain simplicity; the delivery mechanism
  already supports multi-file without one.
- **Keep one file.** The growth trend is steady (each serve feature lands UI code here); the
  navigation cost and the single shared global scope get worse on the current trajectory.
- **Introduce a JS unit-test harness now.** The ESLint config defers a harness until the
  branching logic demands it; the crawl graph arguably crosses that line, but a harness is a
  separate, bigger decision — this item deliberately excludes it so the split stays a pure
  reorganization.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] serve.js split into section files served via the existing template mechanism
- [ ] `startJob(…)` helper; run / record / crawl handlers migrated
- [ ] `eslint.config.mjs` covers the new file set
- [ ] Dogfood gate green across the split (scenario extended only if a gap shows)

## References

- [`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) · [`bajutsu/serve/handler.py`](../../bajutsu/serve/handler.py) · [`eslint.config.mjs`](../../eslint.config.mjs)
- [BE-0129](../BE-0129-serve-scope-boundary/BE-0129-serve-scope-boundary.md) — the guardrail whose calibration this file outgrew
- [BE-0189](../BE-0189-serve-ui-dogfood-ci-gate/BE-0189-serve-ui-dogfood-ci-gate.md) — the dogfood gate that pins UI behavior across the split
