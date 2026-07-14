**English** · [日本語](BE-0247-serve-frontend-es-modules-ja.md)

# BE-0247 — Move the serve frontend to ES modules

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0247](BE-0247-serve-frontend-es-modules.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0247") |
| Implementing PR | [#1084](https://github.com/bajutsu-e2e/bajutsu/pull/1084) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md) |
<!-- /BE-METADATA -->

## Introduction

BE-0202 split the once-monolithic `bajutsu/templates/serve.js` into five section files —
`serve.core.js`, `serve.panels.js`, `serve.crawl.js`, `serve.metrics.js`, `serve.author.js`,
roughly 3,200 lines together — so each reads and diffs as its own file. `bajutsu/serve/handler.py`
concatenates them in a fixed order (`_JS_ASSETS`, `handler.py:657-663`) and inlines the result into
one `<script>` tag; the five files still share a single global JavaScript scope with no module
system, the same as when they were one file. This item gives the section boundary a real language
construct — ES modules — so a file's dependencies are declared in the file itself instead of
implied by where it sits in a hand-maintained tuple.

## Motivation

Two of the five files are still large on their own — `serve.core.js` (~1,017 lines) and
`serve.author.js` (~1,079 lines) — and every file can read or write every other file's state,
because state lives in module-level mutable `let` globals with no enforced boundary between
sections. Examples already spread across the split: `poll`/`recPoll`/`selectedRun`/`recPath`/
`scnFiles`/`targets`/`sims`/`recJobId`/`runJobId` and `keyState`/`ccTokState`/`provState`/
`gitCredState`/`projectsCache` in `serve.core.js:35-41` and its settings section; `crawlPoll`/
`crawlJobId`/`crawlRunId`/`crawlGraphData` in `serve.crawl.js`; `metricsCache`/`metricsSort` in
`serve.metrics.js`. Nothing stops `serve.author.js` from reading `serve.crawl.js`'s `crawlPoll`, or
a future section from shadowing `selectedRun` — the only thing keeping the five files coherent
today is that `_JS_ASSETS` happens to list them in an order where definitions precede their uses.

This is not duplicated logic: the shared primitives every section calls — `getJSON`/`postJSON`/
`streamJob`/`startJob`/`cancelJob`/`setStatus`/`esc` — are already centralized once in
`serve.core.js:48-96` (per BE-0202's own `startJob` unification). The debt is narrower than that:
a flat global namespace and a load order that is load-bearing but invisible at the point of use.
The project's own ESLint guardrail (BE-0129, `eslint.config.mjs`) documents the same gap from the
tooling side. `no-undef` is deliberately off there for two reasons: the *primary* one is that
enabling it would need the full set of browser + ES globals declared (the `globals` npm package),
pulling a Node toolchain into this Python repo; the comment then adds, as a secondary point, that it
"also can't see the cross-file globals the section files share, so it would misfire anyway." That an accidental global collision or a
reordering mistake has no lint that catches it is exactly the fragility this item removes, and it
gets worse, not better, as more panels are added to the UI.

## Detailed design

1. **Adopt native ES modules.** Convert each section file to `export` its public surface and
   `import` what it needs from the others (`serve.core.js` becomes the first module with
   `export function getJSON(...)`, etc.; `serve.author.js` becomes the entry module). This needs no
   build step: browsers execute `<script type="module">` natively, and this is a localhost dev tool
   with the same no-bundler stance BE-0202 kept. The one real change on the server side is delivery
   — `import` specifiers resolve against a URL, so the five files can no longer be concatenated into
   one inlined `<script>` the way `_JS_ASSETS` does today; `bajutsu/serve/handler.py` instead serves
   each section at its own path (a handful of static `GET` routes reading the same
   `bajutsu/templates/serve.*.js` files, mirroring how `serve.html.j2` already names them, not a new
   asset pipeline) and the page loads the entry module via `<script type="module" src="...">`. Five
   small requests over one inlined blob is a negligible cost on localhost, optionally softened with
   `modulepreload` links.
2. **Minimum-viable fallback: explicit namespaces.** If the delivery change in (1) is judged too
   large for one step, land the narrower version first: wrap each section in an IIFE that assigns an
   explicit namespace object (e.g. `window.bajutsu.crawl = {...}`) instead of bare top-level `let`/
   `function` declarations, while keeping today's single concatenated, inlined `<script>`. This makes
   cross-file reads an explicit `bajutsu.crawl.crawlJobId` property access instead of a bare
   identifier that could resolve to any file, without touching how `handler.py` serves the assets.
   It is a strict subset of (1): the same explicit boundaries, migrated later to real `import`/
   `export` once (1) is ready.
3. **Make the load order enforced, not conventional.** Whichever of (1) or (2) lands, the goal is
   the same: replace `_JS_ASSETS`'s tuple-order-as-contract with something the language or the
   tooling checks. Under (1), the `import` graph *is* the order — a module that isn't imported
   simply isn't loaded, and a missing dependency is a load-time `ReferenceError` at the specific
   `import`, not a silent global lookup. Update `eslint.config.mjs`'s `languageOptions.sourceType`
   from `"script"` to `"module"` for the converted files so ESLint parses `import`/`export` syntax,
   and revisit whether `no-undef` can be turned back on per-file now that each file's inputs are
   declared, rather than left off repo-wide. Note this only removes the *secondary* obstacle (the
   cross-file globals): re-enabling `no-undef` would still trip on bare browser globals (`window`,
   `fetch`, `document`, …) unless the primary obstacle — declaring them via the `globals` package —
   is also resolved, so making inputs explicit is necessary but not sufficient on its own.

**Verification.** This is a structural refactor of code the deterministic `run`/CI gate never
executes (prime directive 1 keeps AI and UI code off that path), so there is no automated pass/fail
signal for it beyond `make lint-js` (`node --check` on each file) and the existing serve dogfood
scenarios (BE-0058, BE-0189). Per established practice, the change must be reproduced and verified
by hand in the in-app browser — load each panel (record, replay, triage, crawl, metrics, author) and
confirm it behaves identically before and after — rather than judged from reading the diff alone,
since serve JS/CSS changes have shipped wrong on code-only review before. UI behavior is unchanged;
this item touches only how the frontend code is organized and loaded.

## Alternatives considered

- **A bundler (Webpack, esbuild, Rollup, …).** Rejected for the same reason BE-0202 rejected one:
  this is a Python repo whose UI's value is its zero-toolchain simplicity, and native ES modules get
  the same explicit-dependency benefit without introducing a build step, a `node_modules` tree, or a
  compiled-output artifact to keep in sync with source.
- **Leave the flat global scope as-is.** Rejected as the status quo this item addresses — two files
  are already large, cross-file state has no enforced boundary, and the trend (each new serve
  feature adds a panel) makes the flat namespace harder to reason about over time, not easier.
- **Namespace objects only, skip native modules entirely.** Considered as a smaller, permanent
  fix rather than the fallback staging step described in *Detailed design* (2). Rejected as the end
  state: it makes cross-file access explicit at the call site but the load order is still an
  unenforced convention in `_JS_ASSETS` — nothing fails loudly if a namespace is read before its
  file has loaded. Native `import`/`export` gets both explicit access *and* an enforced order for
  the same amount of code change, so it is kept as the target rather than (2)'s stopping point.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Native ES modules — each section file `export`s its public surface and `import`s its
      dependencies; `handler.py` serves each file at its own path instead of concatenating them.
- [ ] Fallback namespace step (optional) — not taken: the change landed as native modules directly,
      so this staging step is superseded by the item above.
- [x] Load order enforced by the `import` graph; `eslint.config.mjs` updated to `sourceType:
      "module"` for the converted files.

Log:

- PR [#1084](https://github.com/bajutsu-e2e/bajutsu/pull/1084) — converted the five `serve.*.js`
  section files to native ES modules (`serve.*.mjs`): each `import`s its dependencies and `export`s
  its public surface; cross-panel mutable state moved onto a shared `state` object (a live `export
  let` is read-only for importers); each section's top-level side effects moved into `init*()`
  functions the entry module (`serve.author.mjs`) calls in order, so the import graph — not a
  hand-maintained tuple — decides load order, with cycles safe because no binding is used at
  module-evaluation time. `handler.py` serves each module at its own route (`/serve.*.mjs`,
  `text/javascript`, open-GET like the index) and the page loads the entry via `<script
  type="module">` with `modulepreload` hints; `_JS_ASSETS` (concatenation) became `_JS_MODULES`.
  `eslint.config.mjs` → `sourceType: "module"`, `make lint-js` → per-file `node --check` on the
  `.mjs` set (the concatenation check retired). `no-undef` stays off — declaring the bare browser
  globals (the primary obstacle) is still deferred.

## References

[BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md) (split the
monolithic `serve.js` into the section files this item converts to modules, without a build step —
a stance this item keeps), `bajutsu/serve/handler.py` (`_JS_ASSETS`, `_index_html` — today's
concatenation and inlining), `bajutsu/templates/serve.core.js` (the shared primitives —
`getJSON`/`postJSON`/`streamJob`/`startJob`/`cancelJob`/`setStatus`/`esc` — already centralized by
BE-0202), `eslint.config.mjs` (BE-0129's guardrail, whose `no-undef`-off rationale names the
cross-file global visibility gap this item closes).
