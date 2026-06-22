**English** · [日本語](BE-0030-parameterized-shared-steps-ja.md)

# BE-0030 — Parameterized shared steps

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0030](BE-0030-parameterized-shared-steps.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | [#6](https://github.com/bajutsu-e2e/bajutsu/pull/6) |
| Track | [Accepted](../../README.md#accepted) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

Define and call reusable components with arguments via the `use` step, expanding `${params.*}` (`expand_components`). Usable alongside the `setup` prelude (no args). Removes duplication in common steps like login.

## Motivation

Most scenarios share a few common prefixes — log in, accept onboarding, navigate to a screen. Copied into every scenario, that boilerplate is repeated dozens of times, drifts out of sync when the flow changes, and bloats each file. The existing `setup` prelude already covers the no-argument case (a fixed shared flow prepended to a scenario), but it cannot vary by caller: a login flow that needs a different user per scenario, or a navigation that targets a different tab, has no way to take an argument. Authors need a reusable unit of steps that accepts parameters, so one definition serves many call sites with the value filled in per call.

## Detailed design

A *component* is a reusable, parameterized sequence of steps defined in its own file as a single mapping: a `params` list naming the arguments a caller must supply, and the `steps` that reference them as `${params.<name>}` (`Component` in `bajutsu/scenario/`). A scenario invokes one with the `use` step — `use: { component: <file>, with: { <name>: <value>, … } }` — passing each declared param a value through `with`.

Expansion is a pure compile-time macro, not a runtime action. Before the run, `expand_components` walks every scenario and replaces each `use` step in place with the referenced component's steps, substituting the `with` values through the shared `${...}` interpolation primitive (`interp.interpolate`, keyed `params.<name>`). A component may itself `use` another, so expansion recurses; a reference cycle, missing or unknown param, or a residual `${params.*}` token that maps to no declared param is a hard error, as is nesting beyond `max_depth`. After expansion no `use` steps remain — the deterministic runner only ever sees plain, fully-expanded steps, so determinism is unaffected. `use` composes with the no-argument `setup` prelude: the prelude is prepended (`apply_setups`) and components expanded in the same load-time pass.

## Alternatives considered

**Runtime call/return (treat a component as a subroutine the runner steps into).** Rejected: it adds a call stack and live frame state to the run loop for no behavioral gain, and blurs the line between authoring sugar and execution. Compile-time expansion keeps the runner seeing only flat steps, so the executed scenario is exactly what the report shows.

**Inline-only reuse (YAML anchors / a templating engine over the file).** Rejected: anchors duplicate text within one file but cannot cross files or take named arguments cleanly, and a general templating engine invites arbitrary logic into scenario files. A named component with an explicit `params` contract is narrower, validated (unknown/missing params fail), and self-documents its inputs.

## References

`bajutsu/scenario/` (`use`/`expand_components`), [scenarios.md](../../../docs/scenarios.md)
