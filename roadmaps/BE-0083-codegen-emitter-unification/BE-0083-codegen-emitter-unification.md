**English** · [日本語](BE-0083-codegen-emitter-unification-ja.md)

# BE-0083 — Unify the codegen emitters behind a shared scenario walk

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0083](BE-0083-codegen-emitter-unification.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0083") |
| Implementing PR | [#228](https://github.com/bajutsu-e2e/bajutsu/pull/228) |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

This item extracts the scenario walk shared by the two codegen emitters into one place. The shared
walk is either a small intermediate representation or a `CodeGenerator` protocol that the
per-target emitters implement. The traversal then lives once, and each target supplies only its own
line syntax.

Bajutsu transpiles a scenario into a native test for two targets today: XCUITest
(`bajutsu/codegen.py`, shipped in [BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md))
and Playwright (`bajutsu/codegen_playwright.py`, shipped in
[BE-0062](../BE-0062-playwright-codegen/BE-0062-playwright-codegen.md)). The two modules share an
identical *control flow*:

1. Walk the scenarios.
2. For each scenario, merge the launch environment.
3. Emit a launch line.
4. Emit each step.
5. Emit the `expect` block.

The modules differ only in the per-line target syntax. Today the skeleton is copy-pasted between
the two files.

The change is a behavior-preserving refactor of an internal, deterministic, AI-free path. The
generated output is byte-for-byte identical, so no tool behavior changes and the change stays within
the prime directives.

## Motivation

- **The control flow is duplicated.** `_emit_scenario` and `to_xcuitest` / `to_playwright` in the
  two modules are structurally the same loop (env merge → launch line → `for step` → `expect` block →
  close). Suppose a change alters *how a scenario is walked*, for example by emitting a new top-level
  construct or handling a new scenario section. Someone must make that change twice and keep the two
  copies in sync by hand, and nothing in the gate proves the copies stayed aligned.
- **A third emitter would copy the walk a third time.** The natural next step, once an Android
  backend lands, is an Android codegen target. [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  has a per-platform table that already lists a new `codegen.py` emitter per platform. Without
  unification, that target means a third copy of the same skeleton. Unifying the walk now makes each
  new target cost *only* its line syntax, which is the part that genuinely differs.
- **The per-line helpers are already cleanly separated.** Both modules already factor the
  target-specific work into small helpers (`_emit_step`, `_emit_assertion`, selector/locator
  builders). Only the outer traversal is duplicated, so the refactor stays contained: lift the loop
  and keep the helpers.

## Detailed design

Two shapes are viable, and implementation will pick one:

1. **A `CodeGenerator` protocol.** A shared `walk_scenarios(scenarios, env, gen)` owns the loop and
   calls into a target object that supplies the variable parts: `file_header()`, `scenario_open(name)`,
   `launch_lines(env)`, `step_lines(step)`, `assertion_lines(a)`, `scenario_close()`. `codegen.py` and
   `codegen_playwright.py` become implementations of that protocol; `to_xcuitest` / `to_playwright`
   stay as thin public entry points that instantiate their generator and call the shared walk.
2. **A small intermediate representation.** The walk lowers a scenario to a neutral list of emit
   instructions (open-scenario, launch-env, step, assertion, close), and each target renders that list
   to text. This shape decouples *what to emit* from *how to render it* more strictly, at the cost of one
   more layer.

Either way, the per-target line builders (`_emit_step`, `_emit_assertion`, locator/selector helpers)
are unchanged, because they are already the right seam. The acceptance bar is that the generated
XCUITest and Playwright output is **byte-for-byte identical** to today's. The existing codegen tests
prove this, since `tests/` already covers both emitters. If the protocol shape needs it, a focused
test is added for the shared walk.

Scope is the two codegen modules plus their tests. The runner, scenario model, and driver do not
change.

## Alternatives considered

- **Leave the duplication.** Two emitters are a small, tolerable amount of copy-paste today. The
  duplication becomes load-bearing the moment a third target (Android) is added, and the gate cannot
  catch the two copies drifting apart. Unifying now is cheap insurance against that.
- **Share via free functions without a protocol/IR.** Passing a bag of callbacks into a shared loop
  works, but it reads worse than a named protocol and makes the contract between the walk and a
  target implicit. This option is preferred only if the protocol proves heavier than the duplication
  it removes.
- **Defer until the Android emitter is actually being written.** Reasonable. Still, doing the
  unification device-free now (it needs no Simulator) de-risks the Android codegen work and keeps the
  two existing emitters from drifting in the meantime.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- `bajutsu/codegen.py` (XCUITest) and `bajutsu/codegen_playwright.py` (Playwright): the two emitters
  this item unifies.
- [BE-0003 — M3 codegen / traces / network / CI](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md)
  (XCUITest codegen), [BE-0062 — Playwright codegen](../BE-0062-playwright-codegen/BE-0062-playwright-codegen.md)
  (Playwright codegen): the two shipped targets.
- [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md):
  its per-platform table lists a new `codegen.py` emitter per platform, the future caller that this
  unification serves.
