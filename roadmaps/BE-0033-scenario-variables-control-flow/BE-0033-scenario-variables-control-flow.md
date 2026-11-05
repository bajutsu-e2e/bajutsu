**English** · [日本語](BE-0033-scenario-variables-control-flow-ja.md)

# BE-0033 — Scenario variables + light control flow

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0033](BE-0033-scenario-variables-control-flow.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0033") |
| Implementing PR | [#42](https://github.com/bajutsu-e2e/bajutsu/pull/42), [#67](https://github.com/bajutsu-e2e/bajutsu/pull/67) |
| Topic | Scenario authoring features |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

Three pieces are all implemented: the `${...}` interpolation primitive (`interp.py`, which handles params/row/secrets uniformly), runtime value capture via `vars.*`, and bounded conditionals and loops that preserve determinism.

## Motivation

Some flows depend on a value the test cannot know in advance: an order number shown after checkout, a generated username, or a count that the next screen must echo back. Today a scenario can only assert against the literals it was written with, so these cases cannot be expressed at all.

A related gap is shape variation. One flow takes a different path when a banner is present. Another repeats a step a bounded number of times.

Authors need to capture a runtime value and reuse it later. They also need to branch or loop within strict bounds. Scenarios must not turn into a general-purpose program, because that would undermine determinism and reviewability.

## Detailed design

The shared interpolation primitive is implemented. `interp.interpolate` / `interp.find_tokens` (`bajutsu/interp.py`) resolve `${...}` tokens uniformly across namespaces. The primitive substitutes `params.*` and `row.*` at load time, and `secrets.*` / `vars.*` at action time. A token present in the `bindings` map is replaced. A token whose key is absent is left untouched, so each layer substitutes only its own namespace.

**Runtime variables (`vars.*`), implemented.** A step's `extract` modifier captures a UI element's property into a runtime variable after the step executes. Each entry names a variable and gives a `sel` (selector) plus an optional `prop` (`value` | `label` | `identifier`, default `value`). `_run_extract` (`bajutsu/orchestrator/`) resolves the selector via `resolve_unique`. It stores the result under `vars.<name>` in a mutable bindings map carried through the run. If the selector does not resolve uniquely, or the property is `None`, the step fails. Subsequent steps and the scenario-level `expect` then interpolate `${vars.<name>}` from that map (`_interp_step` / `_interp_asserts`). Capture is just a deterministic property read and substitution, so the run/CI verdict stays machine-only, and no LLM is involved.

**Bounded control flow, implemented.** Two deterministic control-flow steps are now available.

- **`if`.** It takes a `condition`, a machine-checkable assertion over the live UI (an element exists, or a `vars.*` value equals something). It runs its `then` steps when the condition holds, or its optional `else` steps otherwise.
- **`forEach`.** It iterates over the elements matching a selector. It binds each matched element's identifier to `vars.<as>` and runs the nested `steps` once per element. The statically resolved match set bounds the loop, so it always terminates.

Both preserve the prime directives:

- The predicate is a deterministic assertion evaluated by machine, never an LLM judgment.
- The loop has a hard upper bound from the match set.
- A skipped or short-circuited branch is reported transparently, so the report reflects exactly what executed.

Control-flow steps carry no `capture`/`extract` modifiers, which keeps them pure structure.

## Alternatives considered

**Expose a general scripting/expression language in scenarios.** Rejected. Arbitrary expressions and unbounded loops would let a scenario diverge, or make pass/fail depend on logic the runner cannot bound. That breaks determinism-first and makes review harder. The design instead admits only a narrow, bounded primitive set.

**Push capture and branching to an external driver script around the tool.** Rejected. It would move test logic out of the scenario YAML, the shared hub, so the executed flow would no longer be fully visible in the file or the report. Keeping `extract`/`vars.*` and bounded control flow in the scenario keeps the whole flow reviewable and reproducible.

**Allow an LLM to decide a branch at runtime.** Rejected outright. It would put a model in the Tier-2 run/CI gate, violating "AI is never the judge." All control-flow predicates must be machine-checkable.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/interp.py`, [scenarios.md](../../docs/scenarios.md)
