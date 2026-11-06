**English** · [日本語](BE-0422-inline-scenario-components-ja.md)

# BE-0422 — Inline, scenario-local components

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0422](BE-0422-inline-scenario-components.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0422") |
| Implementing PR | [#2011](https://github.com/bajutsu-e2e/bajutsu/pull/2011) |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

A scenario file can now declare an inline `components:` block. Each entry maps a name to a BE-0030 component. A component is a reusable, parameterized step sequence, and here it is scoped to that one file. A `use: { component: <name> }` step in the same file resolves the name directly. A step block reused inside a single file then needs no separate component file. BE-0030's cross-file `use: { component: <path> }` keeps working unchanged. It still owns reuse that spans a suite.

## Motivation

BE-0030 defines a *component*: a reusable, parameterized sequence of steps. A `use` step expands the component into the caller's step list before the run. A component always lives in its own file. A `use: { component: <path> }` step resolves that path.

Extracting even a short, three-step block needs a new file. It also needs a name and a relative path from the caller. That fixed cost is the same for a component that one scenario file calls and for one that every file calls.

When the reuse stays inside one file, the cost is out of proportion to the benefit. A file that defines two or three related scenarios often repeats one short step block across them. Examples are a search box tried with different queries, and a form submitted with different field combinations. Splitting that block into its own file adds a file. A reader must open it to see what a `use` step does, for a component that a single file calls. An author facing that trade instead repeats the steps by hand in each scenario. That is the duplication BE-0030 already set out to remove for the cross-file case.

An inline, file-scoped `components:` block removes that trade for the local case. An author defines the component next to the scenarios that call it, in the same file. A name that only one file uses then adds no entry to the suite's directory listing.

Take a scenario file that defines a component under `components:`. Two scenarios in that file call it with `use: { component: <name> }`. At load time, the file expands to the same step list that a hand-duplicated version would produce. Reading the expanded steps with `bajutsu trace --explain` confirms it. No second file appears for the reuse.

## Detailed design

The design is a file-scoped sibling of BE-0030's file-based component. It resolves through the same `use` step. It uses the same `expand_components` macro (`bajutsu/common/scenario/expand.py`). The run loop still sees plain, fully expanded steps alone, so determinism holds.

- **A new `components:` field.** `ScenarioFile` gains `components: dict[str, Component]`, defaulting to empty. The model lives in `bajutsu/common/scenario/models/scenario/scenario_file.py`. Each entry uses the `Component` model (`params` + `steps`). BE-0030 validates a standalone component file against the same model. One schema serves both forms.
  - A scenario file has two on-disk forms (`docs/dsl-grammar.md` §2). One is a bare list of scenarios. The other is a `{description, scenarios}` mapping.
  - The mapping form alone can carry `components:`. An author declaring one converts a bare-list file first.
- **A name resolves in the file, and a path resolves as a file.** `use: { component: <ref> }` keeps a single field. The ref's own shape decides how it resolves.
  - A ref containing `/` or ending in `.yaml` / `.yml` resolves as a file. BE-0030 already resolves it that way.
  - A bare name with neither marker looks up that key in the current file's own `components:` map. This holds while expansion is still inside that file's own scope. The next two bullets say exactly when that is.
  - The two forms read as distinct on sight. File-scoped names and file-based components therefore share the `use` step. No new field and no naming convention is needed.
- **A `ComponentResolver` object.** It keeps `bajutsu run` and the device-free tools in agreement. Two loaders expand a scenario file today.
  - `load_expanded_scenarios` serves `trace --explain`, `audit`, `coverage`, `impact`, and serve. It lives in `bajutsu/common/scenario/load_expanded.py`.
  - `_expand_file` in `run/cli.py` builds its own separate `resolve` lambda. That lambda serves the deterministic `run` gate.

  A new `ComponentResolver` is factored into `load_expanded.py`. It does not go into `expand.py`, which stays filesystem-free by design. It becomes the one place that builds a `resolve` callable. The callable is bound to a local `components:` map, a `root`, and a `base` directory. The resolver owns the bare-name-versus-path shape test and its own per-ref cache.

  A sibling method, `for_component_file()`, returns a new `ComponentResolver` bound to an empty map. It serves the expansion of a path-resolved component's own steps. The `resolve: Callable[[str], Component]` parameter of `expand_components` is unchanged. A `ComponentResolver` instance satisfies it. Both loaders build one and pass it in. An inline `components:` block therefore expands the same way in both paths.

  The two loaders have already drifted once without a shared resolver. `load_expanded_scenarios` normalizes a malformed component file into `invalid YAML in <file>` (BE-0150). `run/cli.py` lets the same `yaml.YAMLError` escape its `except (OSError, ValueError)` as a raw traceback. The shared resolver keeps the normalized form, so `run` gains the clean error too.
- **Crossing into a component file swaps resolvers.** The swap happens inside the same recursion. `expand_components` has an internal `expand(steps, stack)` in `bajutsu/common/scenario/expand.py`. It already recurses into a resolved component's substituted steps under the same call. When a path-shaped ref resolves, that recursive call receives `resolve.for_component_file()` in place of `resolve`.
  - The swap stays inside `expand`'s own `stack` and `max_depth` accounting. It is never a fresh top-level call to `expand_components`, because a fresh call would reset both. A real component cycle would then turn into a `RecursionError`. Today `expand_components` raises a clean error instead.
  - A bare name resolved under the empty-map sibling is always undefined. A plain `Component` (`params` + `steps`) declares no `components:` for the name to consult.
  - Each `ComponentResolver` instance owns its own per-ref cache. `expand_components` keeps one such cache per call today. Here the cache is scoped per resolver, not shared across the whole expansion. The same bare name resolved under two different resolvers therefore never collides. The two resolvers can belong to two component files, or to a scenario file and a component file.
  - A file-scoped component's own steps expand under the *same* resolver as their caller. A file-scoped component may therefore still `use` another file-scoped one. It may also `use` a file-based one, through a path-shaped ref that the swap does not touch.
  - A file-based component's own steps carry no local map, so they may not `use` a bare name at all.
- **A setup prelude expands its own `use` steps before splicing.** A `setup` reference (`Preconditions.setup`) also names a scenario-file-shaped document. `apply_setups` is unchanged. It lives in `bajutsu/common/scenario/expand.py`. It takes a caller-supplied `resolve: Callable[[str], list[Step]]`. It splices whatever steps that callable returns ahead of the calling scenario's own.
  - What changes is the `resolve` lambda in `run/cli.py`. That lambda is the sole caller of `apply_setups`. `load_expanded_scenarios` never applies setups. A device-free reader already treats a scenario's `setup` as inert, with or without this item.
  - The lambda used to return a prelude's steps unexpanded. It now does four things:
    1. It loads the prelude's `ScenarioFile`.
    2. It builds a `ComponentResolver`. The resolver binds the prelude's own `components:` map, the prelude's own directory, and the same suite `root`.
    3. It runs `expand_components` on the prelude's own steps under that resolver.
    4. It returns the result.
  - No unexpanded `use` step ever crosses from a prelude into the scenario that includes it. A shared prelude's bare names therefore always resolve against the prelude's own `components:`. They never resolve against whichever scenario happened to set the prelude as `setup`.
  - A prelude's own path-shaped refs keep resolving against the prelude's own directory. They stay inside BE-0174's containment, exactly as a path-shaped ref resolves anywhere else. No scenario or prelude in this repository writes such a ref today. Nothing observable changes for an existing suite.
- **No new path-containment surface.** The loader parses a file-scoped component once, with the scenario file it already read. Resolving the name never opens a second file. BE-0174's containment check has nothing new to guard here.
- **Scoped to one file.** The loader reads `components:` per file. It never merges the map across the other files of a suite directory. `load_scenarios_dir` loads each file independently. A name declared in one file therefore stays invisible to another. Reuse across files stays BE-0030's file-ref job.

Without this item, an author facing the duplication has two options. The first is to repeat the steps by hand:

```yaml
# one scenario file — the same three steps copied into every scenario that needs them
scenarios:
  - name: search returns dogs
    steps:
      - type: { text: dog, into: { id: home.search }, submit: true }
    expect:
      - label: { sel: { id: home.status }, equals: "1 result" }
  - name: search returns cats
    steps:
      - type: { text: cat, into: { id: home.search }, submit: true }
    expect:
      - label: { sel: { id: home.status }, equals: "2 results" }
```

The second is to pay BE-0030's file-per-component cost for a block that this file alone calls. This item's inline `components:` block gives a third option, defined and called from the same file:

```yaml
# one scenario file — no separate component file
components:
  search:
    params: [query]
    steps:
      - type: { text: "${params.query}", into: { id: home.search }, submit: true }

scenarios:
  - name: search returns dogs
    steps:
      - use: { component: search, with: { query: dog } }
    expect:
      - label: { sel: { id: home.status }, equals: "1 result" }
  - name: search returns cats
    steps:
      - use: { component: search, with: { query: cat } }
    expect:
      - label: { sel: { id: home.status }, equals: "2 results" }
```

## Alternatives considered

* **A suite-wide implicit registry (bare names searched across every file).** Rejected. The reuse this item targets already stays inside one file. A suite-wide search buys nothing that the file-scoped rule does not. Searching a whole suite for a bare name would blur that boundary back into BE-0030's cross-file job. It would also reopen the path-containment question that BE-0174 exists to close. A real file already answers that case.
* **YAML anchors / merge keys for in-file reuse.** Rejected, for the reason BE-0030 gave for cross-file reuse. An anchor cannot take a named, validated argument the way `with:` binds `params`. A caller cannot vary the reused block per call site without hand-editing the expanded text.
* **A distinguishing syntax on `use` (for example `component: "local:<name>"`).** Rejected. The ref's own shape decides instead: a path-like ref versus a bare name. Every existing file ref in this repository's scenarios already ends in `.yaml` / `.yml`. The shape rule needs no new syntax on `use`. Every scenario in this repository keeps working unchanged.

  One ref shape does change meaning. It is a bare ref that names a component file with no `.yaml` / `.yml` suffix and no `/`. Such a ref now fails loudly as an undefined component instead of resolving as a file. The trade is deliberate and low-risk. No scenario in this repository writes such a ref, though a suite outside it could.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `components: dict[str, Component]` to `ScenarioFile`
- [x] Add `ComponentResolver` to `load_expanded.py`: binds a `resolve` callable to a local
      `components:` map, a `root`, and a `base` directory (dispatching a `use` ref by shape: bare
      name → the bound local map, path-like ref → the existing file resolution), owns its own
      per-ref cache, and exposes `for_component_file()` for a sibling bound to an empty map. Wire
      both `load_expanded_scenarios` and `run/cli.py`'s `_expand_file` through it
- [x] In `expand_components`'s `expand(steps, stack)` recursion, hand the recursive call
      `resolve.for_component_file()` in place of `resolve` the moment a path-shaped ref resolves —
      inside the same `stack` / `max_depth` accounting, not a fresh `expand_components` call — so a
      bare name inside that component's steps always fails as undefined
- [x] Update `run/cli.py`'s setup `resolve` lambda to load the prelude's `ScenarioFile`, build a
      `ComponentResolver` bound to the prelude's own `components:` map and its own directory, run
      `expand_components` on the prelude's own steps under it, and return the expanded result to
      `apply_setups` (which itself needs no change)
- [x] Cover it in the fast suite:
      - A file-scoped component expands identically to its hand-duplicated steps.
      - A file-scoped component and a file-based component coexist in one scenario.
      - A bare name undefined in `components:` fails with a clear error.
      - A file-scoped component's name stays invisible from a sibling file in the same suite
        directory.
      - `bajutsu run` and `load_expanded_scenarios` expand the same inline-`components:` scenario
        file identically.
      - A file-scoped component may itself `use` another file-scoped component, and may `use` a
        file-based one.
      - A bare `use` inside a separate component file fails as undefined, even when a same-named
        entry exists in the including scenario file's `components:` map and that file has already
        expanded the same name once.
      - A setup prelude's own bare `use` resolves against the prelude's own `components:`, unaffected
        by a same-named entry in the calling scenario file's map.
      - A malformed component file read through `bajutsu run` reports the same
        `invalid YAML in <file>` message `load_expanded_scenarios` already produces (BE-0150).
- [x] Update `docs/scenarios.md` (§Components) and `docs/dsl-grammar.md` (the `ScenarioFile`
      production in §2, §6.2, and §6.4 / §6.5 — §6.5's pipeline note "`apply_setups` … (so a prelude
      may itself `use` components)" states the order this item changes) plus their `docs/ja/`
      mirrors

Log:

- [#2011](https://github.com/bajutsu-e2e/bajutsu/pull/2011) — Landed the whole work breakdown: the
  `components:` field on `ScenarioFile` (with a validator rejecting a path-shaped key, so a name
  `use` could never reach fails loudly at load time instead of sitting unreachable), the shared
  `ComponentResolver`
  wired through both `load_expanded_scenarios` and `run/cli.py`, the per-ref scope swap inside
  `expand_components`'s own recursion (still under one `stack` / `max_depth`, so a cycle through a
  component file still raises cleanly), and the setup-prelude `resolve` rewritten to expand a
  prelude's own `use` steps — bare names and path refs alike — against the prelude's own scope
  before splicing. Docs and both `docs/ja/` mirrors updated. Every Progress box is now checked;
  `Status` moves to Implemented.

## References

`bajutsu/common/scenario/models/scenario/component.py` — the component model. `bajutsu/common/scenario/expand.py`
(`expand_components`, `apply_setups`) and `bajutsu/common/scenario/load_expanded.py` (where
`ComponentResolver` lives) — the expansion call sites this item unifies behind one resolver.
`bajutsu/run/cli.py` (`_expand_file`, its setup `resolve` lambda) — the other call site, updated to
use the shared resolver.

[BE-0030 — Parameterized shared steps](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md) —
the file-based component this item adds a scenario-local sibling to.

[BE-0174 — Contain scenario component and data refs within the suite root](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md) —
the containment guarantee a file-scoped component needs no change to, since it never opens a second
file.

[docs/scenarios.md](../../docs/scenarios.md#components-use--reusable-steps),
[docs/dsl-grammar.md](../../docs/dsl-grammar.md#62-components-use--reusable-steps) — the authoring
and normative docs this item updates.
