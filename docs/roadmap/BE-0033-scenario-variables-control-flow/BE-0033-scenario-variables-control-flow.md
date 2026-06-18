**English** · [日本語](BE-0033-scenario-variables-control-flow-ja.md)

# BE-0033 — Scenario variables + light control flow

* Proposal: [BE-0033](BE-0033-scenario-variables-control-flow.md)
* Status: **Accepted, in progress**
* Track: [Accepted](../README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

The `${...}` interpolation primitive (`interp.py`, handling params/row/secrets uniformly) is implemented. What remains is capturing UI values for later reuse via `vars.*`, and conditionals and loops within bounds that preserve determinism.

## Motivation

Some flows depend on a value the test cannot know in advance: an order number shown after checkout, a generated username, a count that the next screen must echo back. Today a scenario can only assert against literals it was written with, so these cases cannot be expressed at all. A related gap is shape variation — a flow that takes a different path when a banner is present, or repeats a step a bounded number of times. Authors need to capture a runtime value and reuse it later, and to branch or loop within strict bounds — without turning scenarios into a general-purpose program, which would undermine determinism and reviewability.

## Detailed design

The shared interpolation primitive is implemented: `${...}` tokens are resolved uniformly across namespaces by `interp.interpolate` / `interp.find_tokens` (`bajutsu/interp.py`), with `params.*` and `row.*` substituted at load time and `secrets.*` / `vars.*` at action time. A token present in the `bindings` map is replaced; one whose key is absent is left untouched, so each layer substitutes only its own namespace.

**Runtime variables (`vars.*`) — implemented.** A step's `extract` modifier captures a UI element's property into a runtime variable after the step executes: each entry names a variable and gives a `sel` (selector) plus optional `prop` (`value` | `label` | `identifier`, default `value`). `_run_extract` (`bajutsu/orchestrator/`) resolves the selector via `resolve_unique` and stores the result under `vars.<name>` in a mutable bindings map carried through the run; if the selector is not uniquely resolved or the property is `None`, the step fails. Subsequent steps and the scenario-level `expect` then interpolate `${vars.<name>}` from that map (`_interp_step` / `_interp_asserts`). Because capture is just a deterministic property read and substitution, the run/CI verdict stays machine-only — no LLM is involved.

**Bounded control flow — remaining.** What is still to be designed, at proposal altitude: a conditional that runs steps only when a machine-checkable predicate over the live UI (e.g. an element exists, a `vars.*` value equals something) holds, and a loop that repeats a block a statically bounded number of times (a fixed count, or "until a condition, capped at N"). The guiding constraints are the prime directives: every predicate must be deterministic and machine-evaluated (never an LLM judgment); every loop must have a hard upper bound so a run always terminates; and a skipped or short-circuited branch must be reported transparently so the report still reflects exactly what executed. Expansion-time resolution is preferred where a branch depends only on load-time data; only genuinely runtime-dependent branching needs to live in the run loop.

## Alternatives considered

**Expose a general scripting/expression language in scenarios.** Rejected: arbitrary expressions and unbounded loops would let a scenario diverge or make pass/fail depend on logic the runner cannot bound, breaking determinism-first and making review harder. The design instead admits only a narrow, bounded primitive set.

**Push capture and branching to an external driver script around the tool.** Rejected: it would move test logic out of the scenario YAML — the shared hub — so the executed flow would no longer be fully visible in the file or the report. Keeping `extract`/`vars.*` and bounded control flow in-scenario keeps the whole flow reviewable and reproducible.

**Allow an LLM to decide a branch at runtime.** Rejected outright: it would put a model in the Tier-2 run/CI gate, violating "AI is never the judge." All control-flow predicates must be machine-checkable.

## References

`bajutsu/interp.py`, [scenarios.md](../../scenarios.md)
