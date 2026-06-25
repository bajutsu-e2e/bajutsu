**English** · [日本語](BE-0026-shrink-unsupported-syntax-ja.md)

# BE-0026 — Shrink unsupported syntax

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0026](BE-0026-shrink-unsupported-syntax.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

Reduce the range of cases (e.g. unknown selectors) that drop to a `// TODO`.

## Motivation

`codegen`'s contract is that any construct it cannot translate emits a `// TODO` comment
rather than failing, so the output is always reviewable and never breaks the build. That is
the right safety net, but every `// TODO` is a line a human must port by hand, and a flow with
several of them effectively isn't generated at all. The more of the scenario grammar maps
structurally, the more a passing scenario carries over intact into a team's own Xcode CI —
which is the whole point of codegen. Today the residual gaps include unknown selector forms
(a selector reaching `_element`'s fallback emits `el("UNSUPPORTED_SELECTOR")`), and several
step kinds that drop straight to `// TODO: unsupported step` — `setLocation`, `push`,
the non-`gone` `until` waits beyond the existing settle comment, and the `request` assertion.
Each one left in the fallback set is a flow codegen can't fully reproduce.

## Detailed design

This is an umbrella proposal: shrink the fallback set incrementally, mapping one construct at a
time, each mapping staying purely structural (no AI, no run/CI-gate change). Candidates, in
rough order of value:

- **Selectors.** `_element` already handles `id` / `label` / `idMatches` / `labelMatches`.
  Add the compound forms a selector can carry — `traits`, `value`, `within` (scope to a
  container subtree), and `index` (the k-th match) — by composing `NSPredicate`s and
  `XCUIElementQuery` rather than falling through to `el("UNSUPPORTED_SELECTOR")`. `within` maps
  to a nested query over `descendants(matching:)`; `index` to `element(boundBy:)`.
- **Device-control steps.** `setLocation` and `push` (and any primitives from
  [BE-0035](../../implemented/BE-0035-device-control-primitives/BE-0035-device-control-primitives.md)) have no
  XCUITest API equivalent at the app level — they drive the simulator through `simctl`. For
  these the honest mapping is a clearly-labeled `// TODO` that names the `simctl` command a
  reviewer would run, rather than a bare "unsupported step." They are documented out of scope,
  not silently dropped.
- **Waits and assertions.** Map the `request` assertion and `until: { request: ... }` wait
  only where a structural equivalent exists; network observation generally has no XCUITest
  counterpart, so these stay explicit `// TODO`s as well.

The governing rule is unchanged and load-bearing: **a construct moves out of the fallback set
only when a faithful, deterministic, AI-free structural mapping exists.** Anything that would
require inferring intent at generation time stays a `// TODO` — a reviewable, honest gap is
better than a wrong translation. This keeps codegen's "purely structural, AI-independent"
guarantee intact while steadily reducing the hand-porting a team must do.

### Implementation status

The **compound-selector** slice shipped (`bajutsu/codegen.py`): a single `id` / `label` / `idMatches`
keeps its readable helper, while `value`, `traits`, `index` (alone or combined) now compose one
`NSPredicate` query instead of dropping to `el("UNSUPPORTED_SELECTOR")`. Traits map faithfully over
the small vocabulary (`button` / `link` → `elementType`, `notEnabled` → `enabled == NO`, `selected`
→ `selected == YES`); a metacharacter-free `labelMatches` is a substring (`label CONTAINS`); a
non-negative `index` becomes `element(boundBy:)`. The **device-control** steps (`setLocation` /
`push`) now emit a labeled `// TODO` naming the `simctl` command, rather than a bare "unsupported
step."

Kept an honest `el("UNSUPPORTED_SELECTOR")` / `// TODO` where no *faithful* structural form exists —
the governing rule (a construct leaves the fallback set only when a deterministic, AI-free mapping
exists): a `labelMatches` regex (it is `re.search`, unlike NSPredicate's full-match `MATCHES`),
`within` (geometric frame containment, not a tree query), a negative `index`, an unknown trait, and
the network `request` assertion / `until: { request }` wait.

## Alternatives considered

- **Fail generation on an unsupported construct instead of emitting `// TODO`.** This would
  force completeness but breaks codegen's promise that the output always compiles and is always
  reviewable; a single unmapped step would block emitting the rest of a long flow. Rejected: it
  trades a small manual edit for a hard stop.
- **Auto-fill every gap with a best-effort guess.** For example, translate an unknown selector
  to "whatever matched first," or a `simctl` step to an approximate in-app gesture. This
  violates determinism and the "ambiguous selector fails rather than tapping whatever matched"
  directive, and would produce tests that pass for the wrong reason. Rejected outright.
- **Do nothing and rely on the `// TODO` net.** Acceptable for rarely-used constructs, but the
  common ones (compound selectors especially) appear often enough that leaving them unmapped
  meaningfully weakens codegen. Rejected for the high-value cases, kept for the genuinely
  unmappable ones.

## References

[codegen.md](../../../docs/codegen.md)
