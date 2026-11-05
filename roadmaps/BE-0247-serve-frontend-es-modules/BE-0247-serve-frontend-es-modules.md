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

BE-0202 split the once-monolithic `bajutsu/templates/serve.js` into five section files:
`serve.core.js`, `serve.panels.js`, `serve.crawl.js`, `serve.metrics.js`, and `serve.author.js`. The
five total roughly 3,200 lines. The split lets each section read and diff as its own file.
`bajutsu/serve/handler.py` concatenates them in a fixed order (`_JS_ASSETS`, `handler.py:657-663`) and inlines the result into
one `<script>` tag. The five files still share a single global JavaScript scope with no module
system, the same as when they were one file.

This item gives the section boundary a real language construct: ES modules. A file then declares its
dependencies in the file itself. Today they are implied by where the file sits in a hand-maintained
tuple.

## Motivation

Two of the five files are still large on their own: `serve.core.js` (~1,017 lines) and
`serve.author.js` (~1,079 lines). Every file can read or write every other file's state. State lives
in module-level mutable `let` globals, with no enforced boundary between sections. The state already
spreads across the split:

- `poll`/`recPoll`/`selectedRun`/`recPath`/`scnFiles`/`targets`/`sims`/`recJobId`/`runJobId` and
  `keyState`/`ccTokState`/`provState`/`gitCredState`/`projectsCache` in `serve.core.js:35-41` and its
  settings section
- `crawlPoll`/`crawlJobId`/`crawlRunId`/`crawlGraphData` in `serve.crawl.js`
- `metricsCache`/`metricsSort` in `serve.metrics.js`

Nothing stops `serve.author.js` from reading `crawlPoll` in `serve.crawl.js`. Nothing stops a future
section from shadowing `selectedRun`. Today, the five files stay coherent only because `_JS_ASSETS`
happens to list them in an order where definitions precede their uses.

The debt is not duplicated logic. The shared primitives that every section calls are already
centralized once in `serve.core.js:48-96`, per BE-0202's own `startJob` unification: `getJSON`,
`postJSON`, `streamJob`, `startJob`, `cancelJob`, `setStatus`, and `esc`. The debt is narrower: a flat
global namespace, and a load order that is load-bearing but invisible at the point of use.

The project's own ESLint guardrail (BE-0129, `eslint.config.mjs`) documents the same gap from the
tooling side. `no-undef` is deliberately off there, for two reasons.

- The *primary* reason: enabling the rule would need the full set of browser and ES globals declared
  (the `globals` npm package). Declaring them would pull a Node toolchain into this Python repo.
- The *secondary* reason, which the comment adds: the rule "also can't see the cross-file globals the
  section files share, so it would misfire anyway."

No lint catches an accidental global collision or a reordering mistake. That fragility is what this
item removes, and the fragility grows as more panels are added to the UI.

## Detailed design

1. **Adopt native ES modules.** Convert each section file to `export` its public surface and `import`
   what it needs from the others. `serve.core.js` becomes the first module, with
   `export function getJSON(...)` and the like. `serve.author.js` becomes the entry module. No build
   step is needed. Browsers execute `<script type="module">` natively, and this is a localhost dev
   tool with the same no-bundler stance that BE-0202 kept.

   The one real change on the server side is delivery. `import` specifiers resolve against a URL, so
   the five files can no longer be concatenated into one inlined `<script>` the way `_JS_ASSETS` does
   today. Instead, `bajutsu/serve/handler.py` serves each section at its own path. This takes a
   handful of static `GET` routes reading the same `bajutsu/templates/serve.*.js` files, mirroring how
   `serve.html.j2` already names them. It is not a new asset pipeline. The page loads the entry module
   via `<script type="module" src="...">`. Five small requests instead of one inlined blob cost
   little on localhost. `modulepreload` links can soften the cost further.
2. **Minimum-viable fallback: explicit namespaces.** Suppose the delivery change in (1) is judged too
   large for one step. Then land the narrower version first. Wrap each section in an immediately
   invoked function expression (IIFE) that assigns an explicit namespace object (e.g.
   `window.bajutsu.crawl = {...}`), instead of bare top-level `let`/`function` declarations. Keep
   today's single concatenated, inlined `<script>`. A cross-file read then becomes an explicit
   `bajutsu.crawl.crawlJobId` property access. It is no longer a bare identifier that could resolve to
   any file. The way `handler.py` serves the assets stays untouched. This fallback is a strict subset
   of (1): it sets the same explicit boundaries, and a later step migrates them to real `import` /
   `export` once (1) is ready.
3. **Make the load order enforced, not conventional.** Whichever of (1) or (2) lands, the goal is the
   same. Replace `_JS_ASSETS`'s tuple order as a contract with something that the language or the
   tooling checks. Under (1), the `import` graph *is* the order. A module that no file imports is
   simply not loaded. A missing dependency is a load-time `ReferenceError` at the specific `import`,
   not a silent global lookup.

   Update `eslint.config.mjs`'s `languageOptions.sourceType` from `"script"` to `"module"` for the
   converted files, so ESLint parses `import` / `export` syntax. Then revisit whether `no-undef` can be
   turned back on per file, now that each file's inputs are declared, rather than left off repo-wide.
   This step removes the *secondary* obstacle alone, the cross-file globals. Re-enabling `no-undef`
   would still trip on bare browser globals (`window`, `fetch`, `document`, …) until the primary
   obstacle is also resolved, by declaring them via the `globals` package. Making inputs explicit is
   therefore necessary but not sufficient.

**Verification.** This is a structural refactor of code that the deterministic `run`/CI gate never
executes (prime directive 1 keeps AI and UI code off that path). Beyond `make lint-js` (`node --check`
on each file) and the existing serve dogfood scenarios (BE-0058, BE-0189), no automated pass/fail
signal exists for it.

Per established practice, a person must reproduce and verify the change by hand in the in-app browser.
The person loads each panel (record, replay, triage, crawl, metrics, author) and confirms that it
behaves identically before and after. Reading the diff alone does not suffice, because serve JS/CSS
changes have shipped wrong on code-only review before. UI behavior is unchanged. This item touches
only how the frontend code is organized and loaded.

## Alternatives considered

- **A bundler (Webpack, esbuild, Rollup, …).** Rejected for the reason BE-0202 gave. The project is a Python
  repo, and the UI's value is its zero-toolchain simplicity. Native ES modules get the same
  explicit-dependency benefit. They introduce no build step, no `node_modules` tree, and no
  compiled-output artifact to keep in sync with source.
- **Leave the flat global scope as-is.** Rejected as the status quo that this item addresses. Two
  files are already large, and cross-file state has no enforced boundary. Each new serve feature adds
  a panel, so the flat namespace gets harder to reason about over time, not easier.
- **Namespace objects only, skip native modules entirely.** Considered as a smaller, permanent fix,
  rather than the fallback staging step described in *Detailed design* (2). Rejected as the end state.
  Namespaces make cross-file access explicit at the call site. But the load order stays an unenforced
  convention in `_JS_ASSETS`, and nothing fails loudly if a namespace is read before its file has
  loaded. Native `import` / `export` gets both explicit access *and* an enforced order for the same
  amount of code change. Native modules therefore stay the target, and (2) is not the stopping point.

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
