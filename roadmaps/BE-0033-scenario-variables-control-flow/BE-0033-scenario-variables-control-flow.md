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
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

The `${...}` interpolation primitive (`interp.py`, handling params/row/secrets uniformly), runtime value capture via `vars.*`, and bounded conditionals and loops that preserve determinism are all implemented.

## Motivation

Some flows depend on a value the test cannot know in advance: an order number shown after checkout, a generated username, a count that the next screen must echo back. Today a scenario can only assert against literals it was written with, so these cases cannot be expressed at all. A related gap is shape variation — a flow that takes a different path when a banner is present, or repeats a step a bounded number of times. Authors need to capture a runtime value and reuse it later, and to branch or loop within strict bounds — without turning scenarios into a general-purpose program, which would undermine determinism and reviewability.

## Detailed design

The shared interpolation primitive is implemented: `${...}` tokens are resolved uniformly across namespaces by `interp.interpolate` / `interp.find_tokens` (`bajutsu/interp.py`), with `params.*` and `row.*` substituted at load time and `secrets.*` / `vars.*` at action time. A token present in the `bindings` map is replaced; one whose key is absent is left untouched, so each layer substitutes only its own namespace.

**Runtime variables (`vars.*`) — implemented.** A step's `extract` modifier captures a UI element's property into a runtime variable after the step executes: each entry names a variable and gives a `sel` (selector) plus optional `prop` (`value` | `label` | `identifier`, default `value`). `_run_extract` (`bajutsu/orchestrator/`) resolves the selector via `resolve_unique` and stores the result under `vars.<name>` in a mutable bindings map carried through the run; if the selector is not uniquely resolved or the property is `None`, the step fails. Subsequent steps and the scenario-level `expect` then interpolate `${vars.<name>}` from that map (`_interp_step` / `_interp_asserts`). Because capture is just a deterministic property read and substitution, the run/CI verdict stays machine-only — no LLM is involved.

**Bounded control flow — implemented.** Two deterministic control-flow steps are now available. `if` takes a `condition` — a machine-checkable assertion over the live UI (an element exists, a `vars.*` value equals something) — and runs its `then` steps when the condition holds or its optional `else` steps otherwise. `forEach` iterates over the elements matching a selector, binding each matched element's identifier to `vars.<as>` and running the nested `steps` once per element, so the loop is bounded by the statically resolved match set and always terminates. Both preserve the prime directives: the predicate is a deterministic assertion evaluated by machine (never an LLM judgment), the loop has a hard upper bound from the match set, and a skipped or short-circuited branch is reported transparently so the report reflects exactly what executed. Control-flow steps carry no `capture`/`extract` modifiers, keeping them pure structure.

## Alternatives considered

**Expose a general scripting/expression language in scenarios.** Rejected: arbitrary expressions and unbounded loops would let a scenario diverge or make pass/fail depend on logic the runner cannot bound, breaking determinism-first and making review harder. The design instead admits only a narrow, bounded primitive set.

**Push capture and branching to an external driver script around the tool.** Rejected: it would move test logic out of the scenario YAML — the shared hub — so the executed flow would no longer be fully visible in the file or the report. Keeping `extract`/`vars.*` and bounded control flow in-scenario keeps the whole flow reviewable and reproducible.

**Allow an LLM to decide a branch at runtime.** Rejected outright: it would put a model in the Tier-2 run/CI gate, violating "AI is never the judge." All control-flow predicates must be machine-checkable.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/interp.py`, [scenarios.md](../../docs/scenarios.md)
