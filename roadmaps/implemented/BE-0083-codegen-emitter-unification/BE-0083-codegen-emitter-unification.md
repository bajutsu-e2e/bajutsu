**English** · [日本語](BE-0083-codegen-emitter-unification-ja.md)

# BE-0083 — Unify the codegen emitters behind a shared scenario walk

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0083](BE-0083-codegen-emitter-unification.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Implementing PR | [#228](https://github.com/bajutsu-e2e/bajutsu/pull/228) |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

Bajutsu transpiles a scenario into a native test for two targets today: XCUITest
(`bajutsu/codegen.py`, shipped in [BE-0003](../../implemented/BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md))
and Playwright (`bajutsu/codegen_playwright.py`, shipped in
[BE-0062](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md)). The two
modules share an identical *control flow* — walk the scenarios, for each one merge the launch
environment, emit a launch line, emit each step, then emit the `expect` block — and differ only in
the per-line target syntax. That skeleton is currently copy-pasted between the two files. This item
extracts the shared walk into one place (a small intermediate representation, or a `CodeGenerator`
protocol the per-target emitters implement) so the traversal lives once and each target only supplies
its own line syntax.

This is a behavior-preserving refactor of an internal, deterministic, AI-free path: the generated
output is byte-for-byte identical, so it changes no tool behavior and stays within the prime
directives.

## Motivation

- **The control flow is duplicated.** `_emit_scenario` and `to_xcuitest` / `to_playwright` in the
  two modules are structurally the same loop (env merge → launch line → `for step` → `expect` block →
  close). A change to *how a scenario is walked* — say, emitting a new top-level construct, or
  handling a new scenario section — has to be made twice and kept in sync by hand, with nothing in the
  gate proving the two stayed aligned.
- **A third emitter would copy it a third time.** Adding an Android codegen target (the natural next
  step once an Android backend lands — see
  [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), whose
  per-platform table already lists a new `codegen.py` emitter per platform) would mean a third copy of
  the same skeleton. Unifying the walk now makes each new target the cost of *only* its line syntax,
  which is the part that genuinely differs.
- **The per-line helpers are already cleanly separated.** Both modules already factor the
  target-specific work into small helpers (`_emit_step`, `_emit_assertion`, selector/locator
  builders). Only the outer traversal is duplicated, so the refactor is contained: lift the loop, keep
  the helpers.

## Detailed design

Two shapes are viable; the item will pick one during implementation:

1. **A `CodeGenerator` protocol.** A shared `walk_scenarios(scenarios, env, gen)` owns the loop and
   calls into a target object that supplies the variable parts: `file_header()`, `scenario_open(name)`,
   `launch_lines(env)`, `step_lines(step)`, `assertion_lines(a)`, `scenario_close()`. `codegen.py` and
   `codegen_playwright.py` become implementations of that protocol; `to_xcuitest` / `to_playwright`
   stay as thin public entry points that instantiate their generator and call the shared walk.
2. **A small intermediate representation.** The walk lowers a scenario to a neutral list of emit
   instructions (open-scenario, launch-env, step, assertion, close), and each target renders that list
   to text. This decouples *what to emit* from *how to render it* more strictly, at the cost of one
   more layer.

Either way, the per-target line builders (`_emit_step`, `_emit_assertion`, locator/selector helpers)
are unchanged — they are already the right seam. The acceptance bar is that the generated XCUITest and
Playwright output is **byte-for-byte identical** to today's, proven by the existing codegen tests
(`tests/` already covers both emitters), with a focused test added for the shared walk if the
protocol shape needs one.

Scope is the two codegen modules plus their tests; no runner, scenario model, or driver change.

## Alternatives considered

- **Leave the duplication.** Two emitters is a small, tolerable amount of copy-paste today — but the
  duplication is load-bearing the moment a third target (Android) is added, and the gate cannot catch
  the two drifting apart. Unifying now is cheap insurance against that.
- **Share via free functions without a protocol/IR.** Passing a bag of callbacks into a shared loop
  works but reads worse than a named protocol and makes the contract between the walk and a target
  implicit. Preferred only if the protocol proves heavier than the duplication it removes.
- **Defer until the Android emitter is actually being written.** Reasonable, but doing the unification
  device-free now (it needs no Simulator) de-risks the Android codegen work and keeps the two existing
  emitters from drifting in the meantime.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- `bajutsu/codegen.py` (XCUITest), `bajutsu/codegen_playwright.py` (Playwright) — the two emitters
  this unifies.
- [BE-0003 — M3 codegen / traces / network / CI](../../implemented/BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md)
  (XCUITest codegen), [BE-0062 — Playwright codegen](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md)
  (Playwright codegen) — the two shipped targets.
- [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  — its per-platform table lists a new `codegen.py` emitter per platform, the future caller this
  unification serves.
