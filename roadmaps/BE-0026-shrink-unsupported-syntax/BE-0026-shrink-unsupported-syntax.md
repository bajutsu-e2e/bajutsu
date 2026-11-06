**English** · [日本語](BE-0026-shrink-unsupported-syntax-ja.md)

# BE-0026 — Shrink unsupported syntax

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0026](BE-0026-shrink-unsupported-syntax.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0026") |
| Implementing PR | [#210](https://github.com/bajutsu-e2e/bajutsu/pull/210), [#280](https://github.com/bajutsu-e2e/bajutsu/pull/280), [#316](https://github.com/bajutsu-e2e/bajutsu/pull/316) |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

Reduce the range of cases that drop to a `// TODO`, such as unknown selectors.

## Motivation

`codegen` has one contract for a construct it cannot translate. It emits a `// TODO` comment
instead of failing. The output stays reviewable and never breaks the build. That is the right
safety net, but each `// TODO` is a line a human must port by hand. A flow with several of them is
effectively not generated at all.

The more of the scenario grammar that maps structurally, the more of a passing scenario carries over intact into a team's own Xcode CI. That is the whole point of codegen. Today the residual gaps include two kinds:

- **Unknown selector forms.** A [selector](../../docs/glossary.md#scenario-authoring) that reaches
  `_element`'s fallback emits `el("UNSUPPORTED_SELECTOR")`.
- **Step kinds that drop straight to `// TODO: unsupported step`.** These are `setLocation`, `push`,
  the non-`gone` `until` waits beyond the existing settle comment, and the `request` assertion.

Each construct left in the fallback set is a flow that codegen cannot fully reproduce.

## Detailed design

This is an umbrella proposal. It shrinks the fallback set incrementally, mapping one construct at a
time. Each mapping stays purely structural, with no AI and no change to the run/CI gate. The
candidates, in rough order of value, are as follows.

- **Selectors.** `_element` already handles `id` / `label` / `idMatches` / `labelMatches`. Add the compound forms a selector can carry: `traits`, `value`, `within` (scope to a container
  subtree), and `index` (the k-th match). These compose `NSPredicate`s and `XCUIElementQuery`
  instead of falling through to `el("UNSUPPORTED_SELECTOR")`. `within` maps to a nested query over
  `descendants(matching:)`, and `index` maps to `element(boundBy:)`.
- **Device-control steps.** `setLocation` and `push` have no app-level XCUITest API equivalent,
  and neither do any primitives from
  [BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md). They drive
  the simulator through `simctl`. For these steps the honest mapping is a clearly-labeled `// TODO` that names the `simctl` command a reviewer would run, in place of a bare "unsupported
  step". They are documented as out of scope and never dropped silently.
- **Waits and assertions.** Map the `request` assertion and the `until: { request: ... }` wait
  only where a structural equivalent exists. Network observation generally has no XCUITest
  counterpart, so these stay explicit `// TODO`s as well.

The governing rule is unchanged and load-bearing: **a construct moves out of the fallback set
only when a faithful, deterministic, AI-free structural mapping exists.** Anything that would
require inferring intent at generation time stays a `// TODO`. A reviewable, honest gap is better
than a wrong translation. The rule keeps codegen's "purely structural, AI-independent" guarantee
intact while steadily reducing the hand-porting a team must do.

## Alternatives considered

- **Fail generation on an unsupported construct instead of emitting `// TODO`.** This would force
  completeness. It breaks codegen's promise that the output always compiles and is always
  reviewable, and a single unmapped step would block emitting the rest of a long flow. Rejected,
  because it trades a small manual edit for a hard stop.
- **Auto-fill every gap with a best-effort guess.** One example translates an unknown selector to
  "whatever matched first". Another translates a `simctl` step to an approximate in-app gesture.
  Both violate determinism and the directive that an ambiguous selector fails instead of tapping
  whatever matched. They would produce tests that pass for the wrong reason. Rejected outright.
- **Do nothing and rely on the `// TODO` net.** This is acceptable for rarely used constructs. The
  common ones, compound selectors above all, appear often enough that leaving them unmapped
  meaningfully weakens codegen. Rejected for the high-value cases and kept for the genuinely
  unmappable ones.

## Progress

- [x] Compound-selector mapping — `value` / `traits` / `index` (including a negative index, via `element(boundBy: count - k)`) compose one `NSPredicate` query instead of dropping to `el("UNSUPPORTED_SELECTOR")`.
- [x] Device-control steps (`setLocation` / `push`) emit a labeled `// TODO` naming the `simctl` command.
- [x] Network assertions and the `until: { request }` wait emit a labeled `// TODO` naming the endpoint and the no-interception reason.
- [x] Kept an honest `el("UNSUPPORTED_SELECTOR")` only where no faithful structural form exists (`labelMatches` regex, `within`, an unknown trait).

## References

[codegen.md](../../docs/codegen.md)
