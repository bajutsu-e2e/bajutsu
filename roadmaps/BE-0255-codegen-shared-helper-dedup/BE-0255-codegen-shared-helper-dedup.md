**English** · [日本語](BE-0255-codegen-shared-helper-dedup-ja.md)

# BE-0255 — Deduplicate codegen identifier and regex helpers into codegen_common

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0255](BE-0255-codegen-shared-helper-dedup.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0255") |
| Implementing PR | [#1071](https://github.com/bajutsu-e2e/bajutsu/pull/1071) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

[BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md) gave the
codegen family a shared scenario walk: `codegen_common.py` holds the `CodeGenerator` protocol plus
`render_test_file`/`_scenario_lines`, and `codegen_emit.py` is the single dispatcher every caller
goes through. That seam covers *how a scenario is traversed*. It does not cover the small
language-agnostic helpers each target still defines for itself, below that seam, by copy-paste:
turning a scenario name into an identifier, deriving a class name, converting seconds to
milliseconds, and recognizing whether a string is a plain substring or a real regex. This item
extends the existing `codegen_common.py` seam to those helpers, so a third or fourth target
inherits them instead of re-copying them.

This is a **behavior-preserving** internal refactor of an internal, deterministic, AI-free path
(codegen is Tier-1 authoring output, never on the deterministic `run`/CI verdict path): moving code
without changing what it emits, except where duplication has already let the targets drift (see
Motivation) — those spots are reconciled onto the more correct behavior, matching the existing
"unify, do not just relocate" spirit of BE-0083.

## Motivation

Concretely, the following are copy-pasted across the per-target codegen modules today:

- **`_ident` is byte-identical** between `bajutsu/codegen.py:50-57` (XCUITest) and
  `bajutsu/codegen_uiautomator.py:75-82` (uiautomator/Kotlin). Both sanitize a scenario name into
  a `test_`-prefixed method identifier with the same regex and the same digit-prefix guard.
- **`_class_name` is near-identical** between `bajutsu/codegen.py:60-64` and
  `bajutsu/codegen_uiautomator.py:85-92` — there are two deltas, not one. First, the class-name
  suffix differs: `codegen.py:64` returns `f"{cleaned}UITests"` (plural) while
  `codegen_uiautomator.py:92` returns `f"{cleaned}UITest"` (singular), so the shared helper must
  keep the suffix a per-target parameter. Second, the digit-prefix guard at
  `codegen_uiautomator.py:89-91` (`"A Kotlin class name cannot start with a digit"`). XCUITest's
  version has no such guard, even though a Swift `class` name has the identical restriction —
  today a scenario name that starts with a digit produces an invalid Swift class name that only
  the uiautomator target guards against. This is exactly the kind of drift that copy-pasted
  helpers invite: a fix applied to one copy and forgotten in the other.
- **`_ms` is identical** between `bajutsu/codegen_playwright.py:289-290` and
  `bajutsu/codegen_uiautomator.py:163-164` — both convert a `float` seconds duration into an `int`
  milliseconds count for a generated timeout/delay call.
- **`_RE_METACHARS` is an identical frozenset with near-identical comments** in
  `bajutsu/codegen.py:73` and `bajutsu/codegen_uiautomator.py:55` — both use it to decide whether a
  `labelMatches` pattern is a metacharacter-free plain substring (translatable to a native
  `CONTAINS`/`contains` call) or a real regex (which stays a `// TODO`, since neither NSPredicate
  nor UiAutomator2 has a faithful arbitrary-regex form matching Python's `re.search`).
- **The `_NO_NETWORK` constant and its network-assertion `// TODO` block are duplicated** between
  `bajutsu/codegen.py` and `bajutsu/codegen_uiautomator.py` — both black-box, on-device targets
  have no network-interception surface, so both emit the same shaped `// TODO: wait until
  request (...)` / `// TODO: request assertion (...)` lines, differing only in the constant's
  wording (`"XCUITest has no network interception..."` vs. `"the adb backend has no network
  interception..."`).

None of this is a bug in the current, tested output — but every one of these helpers is
language-agnostic (a Swift, Kotlin, or hypothetical fourth-target identifier all sanitize a
scenario name the same way), so keeping them as independent copies is pure duplication cost with
no compensating benefit, and it has already let one target's fix (the digit-prefix guard) diverge
from the others. Centralizing them in `codegen_common.py` — the module BE-0083 already established
as the shared, target-agnostic home for codegen logic — removes the drift risk and shrinks the
per-target modules to the parts that are genuinely target-specific: the line syntax.

## Detailed design

The work breaks down MECE by helper, plus one explicitly-scoped item for the network-TODO
duplication and one explicit non-goal:

1. **Move `_ident` into `codegen_common.py`.** Byte-identical today in `codegen.py` and
   `codegen_uiautomator.py`; the Playwright target is JS/TS's `test(...)` call and does not need a
   Swift/Kotlin-style bare identifier, so it opts in only if that changes. Both current callers
   import the shared function and drop their local copy.
2. **Move `_class_name` into `codegen_common.py`, taking the suffix as a parameter and applying
   the digit-prefix guard uniformly.** The two targets differ in the class-name suffix
   (`"UITests"` for XCUITest vs. `"UITest"` for uiautomator), so the shared helper keeps that a
   per-target argument. Separately, the digit-prefix guard currently only in
   `codegen_uiautomator.py` (`cleaned[0].isdigit()` → prefix `_`) applies with equal force to
   XCUITest's Swift `class` name, which has the identical restriction and is silently unguarded
   today. Moving the helper is also the moment to close that gap, rather than moving a copy of the
   bug alongside the fix.
3. **Move `_ms` into `codegen_common.py`.** Identical in `codegen_playwright.py` and
   `codegen_uiautomator.py`; XCUITest's `codegen.py` does not currently need a milliseconds
   conversion, so it gains access to the shared helper without being forced to use it.
4. **Add a shared `is_plain_substring(pattern)` (backed by the existing `_RE_METACHARS`) to
   `codegen_common.py`**, and have `codegen.py` and `codegen_uiautomator.py` call it instead of
   each inlining the `set(pattern) & _RE_METACHARS` check. The frozenset itself moves alongside it
   as a common-module constant, with one comment instead of two near-identical ones.
5. **Consider a shared `NetworkUnsupported`-style helper** for the two black-box mobile targets'
   `_NO_NETWORK` TODO block (the wait-until-request and request/requestSequence-assertion lines).
   This one is scoped as "consider" rather than "do," because the two constants' wording is
   target-specific prose (naming the actual backend — XCUITest vs. the adb backend) that a shared
   helper would need to parameterize; if that parameterization reads more awkwardly than the two
   short constants it replaces, keeping the constants separate and only sharing the surrounding
   `// TODO` line-shape (already largely identical) is an acceptable outcome. Playwright has its
   own, already-different network story (it can intercept requests, so it isn't part of this
   duplication) and stays untouched.
6. **Leave the per-target `_emit_step` dispatch shape alone.** Each target's chain of `if`
   branches mapping a `Step` variant to its target-specific lines is inherent to per-target code
   emission (BE-0083 already unified everything above that layer), and collapsing it further is
   out of scope here — see Alternatives considered.

Each step is independently landable and behavior-preserving except where called out (item 2's
digit-guard fix, which is a narrowly-scoped correctness fix that piggybacks on the move rather than
a separate proposal, since duplicating a known bug into the shared module would defeat the point of
centralizing it).

## Alternatives considered

**A shared iterator that calls per-target hooks to collapse the three `_emit_step` if-chains
directly**, rather than stopping at the small helpers above. This was considered and deferred: the
three `_emit_step` implementations differ enough in their per-step-kind line generation (Swift vs.
Kotlin vs. TypeScript syntax, plus each target's own selector/predicate builders) that forcing them
through one generic dispatch would need a wider hook surface than the current `CodeGenerator`
protocol, for a payoff that only shows up once a fourth target exists. It is lower effort and
higher reward to land the concrete, already-duplicated helpers now and revisit the dispatch shape
if and when an Android (or other) target actually lands, rather than design an abstraction ahead
of its second concrete need.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Move `_ident` into `codegen/common.py` as `ident`; update `codegen/xcuitest.py` and
      `codegen/uiautomator.py` to import it.
- [x] Move `_class_name` into `codegen/common.py` as `class_name(name, suffix)`, applying the
      digit-prefix guard uniformly to all targets (closing the silent XCUITest gap).
- [x] Move `_ms` into `codegen/common.py` as `ms`; update `codegen/playwright.py` and
      `codegen/uiautomator.py` to import it.
- [x] Add a shared `is_plain_substring(pattern)`/`_RE_METACHARS` helper to `codegen/common.py` and
      switch `codegen/xcuitest.py` / `codegen/uiautomator.py` to it.
- [x] Evaluate a shared `network_unsupported(subject)` helper for the `_NO_NETWORK` TODO block —
      landed: the parameterized version reads at least as clearly and folds the duplicated tail
      prose (and its "why" comment) into one place.
- [x] Confirm the per-target `_emit_step` dispatch shape is explicitly out of scope (documented in
      *Alternatives considered*, not silently dropped).

> **Note (path drift):** since this proposal was written, the flat `bajutsu/codegen*.py` modules
> became the `bajutsu/codegen/` package (BE-0257), so the moves above target `codegen/common.py`,
> `codegen/xcuitest.py`, `codegen/uiautomator.py`, and `codegen/playwright.py` — the same helpers,
> the same seam.

**Log**

- Implemented in [#1071](https://github.com/bajutsu-e2e/bajutsu/pull/1071): moved `ident`,
  `class_name`, `ms`, `is_plain_substring` (+ `_RE_METACHARS`), and `network_unsupported` into
  `codegen/common.py`; the three per-target emitters now import them. The XCUITest digit-prefix
  guard gap is closed (item 2).

## References

- [BE-0083 — Unify the codegen emitters behind a shared scenario walk](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md) —
  the shared scenario walk this item extends the same "unify, don't just relocate" approach to.
- [`bajutsu/codegen_common.py`](../../bajutsu/codegen_common.py) — the existing shared module this
  proposal adds the identifier/regex/duration helpers to.
- [`bajutsu/codegen_emit.py`](../../bajutsu/codegen_emit.py) — the single dispatcher every codegen
  caller already goes through; unaffected by this item.
- [`bajutsu/codegen.py`](../../bajutsu/codegen.py),
  [`bajutsu/codegen_uiautomator.py`](../../bajutsu/codegen_uiautomator.py),
  [`bajutsu/codegen_playwright.py`](../../bajutsu/codegen_playwright.py) — the three per-target
  emitters holding the duplicated helpers today.
