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
codegen family a shared scenario walk. `codegen_common.py` holds the `CodeGenerator` protocol plus
`render_test_file` and `_scenario_lines`. `codegen_emit.py` is the single dispatcher every caller
goes through. That seam covers *how a scenario is traversed*.

Below that seam, each target still defines its own small language-agnostic helpers by copy-paste:

- turning a scenario name into an identifier;
- deriving a class name;
- converting seconds to milliseconds;
- recognizing whether a string is a plain substring or a real regex.

This item extends the existing `codegen_common.py` seam to those helpers. A third or fourth target
then inherits them instead of re-copying them.

The change is a **behavior-preserving** internal refactor of an internal, deterministic, AI-free
path. Codegen is Tier-1 authoring output and never sits on the deterministic `run`/CI verdict path.
The refactor moves code without changing what it emits. The exception is where duplication has
already let the targets drift (see Motivation). The refactor reconciles those spots onto the more correct
behavior, in the "unify, do not just relocate" spirit of BE-0083.

## Motivation

The following helpers are copy-pasted across the per-target codegen modules today.

- **`_ident` is byte-identical** in `bajutsu/codegen.py:50-57` (XCUITest) and
  `bajutsu/codegen_uiautomator.py:75-82` (uiautomator/Kotlin). Both sanitize a scenario name into a
  `test_`-prefixed method identifier. Both use the same regex and the same digit-prefix guard.
- **`_class_name` is near-identical** in `bajutsu/codegen.py:60-64` and
  `bajutsu/codegen_uiautomator.py:85-92`. The two copies differ in two places, not one.
  - The class-name suffix differs. `codegen.py:64` returns `f"{cleaned}UITests"` (plural), and
    `codegen_uiautomator.py:92` returns `f"{cleaned}UITest"` (singular). The shared helper must
    keep the suffix a per-target parameter.
  - The digit-prefix guard at `codegen_uiautomator.py:89-91` (`"A Kotlin class name cannot start
    with a digit"`) has no XCUITest counterpart, although a Swift `class` name has the identical
    restriction. Today a scenario name that starts with a digit produces an invalid Swift class
    name, and only the uiautomator target guards against it.

  This is the kind of drift that copy-pasted helpers invite: a fix applied to one copy and
  forgotten in the other.
- **`_ms` is identical** in `bajutsu/codegen_playwright.py:289-290` and
  `bajutsu/codegen_uiautomator.py:163-164`. Both convert a `float` seconds duration into an `int`
  milliseconds count for a generated timeout or delay call.
- **`_RE_METACHARS` is an identical frozenset with near-identical comments** in
  `bajutsu/codegen.py:73` and `bajutsu/codegen_uiautomator.py:55`. Both targets use it to classify
  a `labelMatches` pattern.
  - A metacharacter-free plain substring translates to a native `CONTAINS`/`contains` call.
  - A real regex stays a `// TODO`. Neither NSPredicate nor UiAutomator2 has a faithful
    arbitrary-regex form matching Python's `re.search`.
- **The `_NO_NETWORK` constant and its network-assertion `// TODO` block are duplicated** in
  `bajutsu/codegen.py` and `bajutsu/codegen_uiautomator.py`. Both black-box, on-device targets have
  no network-interception surface. Both emit the same shaped `// TODO: wait until request (...)`
  and `// TODO: request assertion (...)` lines. Only the constant's wording differs
  (`"XCUITest has no network interception..."` versus `"the adb backend has no network
  interception..."`).

None of this is a bug in the current, tested output. Every one of these helpers is
language-agnostic, though. A Swift identifier, a Kotlin identifier, and the identifier of a
hypothetical fourth target all sanitize a scenario name the same way. Independent copies are pure
duplication cost with no compensating benefit. They have already let one target's fix (the
digit-prefix guard) diverge from the others.

BE-0083 already established `codegen_common.py` as the shared, target-agnostic home for codegen
logic. Centralizing the helpers there removes the drift risk. The per-target modules shrink to the
genuinely target-specific part, the line syntax.

## Detailed design

The work breaks down MECE by helper. One explicitly scoped item covers the network-TODO
duplication, and one explicit non-goal closes the list.

1. **Move `_ident` into `codegen_common.py`.** The helper is byte-identical today in `codegen.py`
   and `codegen_uiautomator.py`. Both callers import the shared function and drop their local copy.
   The Playwright target emits JS/TS's `test(...)` call and needs no Swift/Kotlin-style bare
   identifier. It opts in if that changes.
2. **Move `_class_name` into `codegen_common.py`, taking the suffix as a parameter and applying
   the digit-prefix guard uniformly.**
   - The two targets differ in the class-name suffix (`"UITests"` for XCUITest, `"UITest"` for
     uiautomator). The shared helper keeps the suffix a per-target argument.
   - The digit-prefix guard exists only in `codegen_uiautomator.py` today
     (`cleaned[0].isdigit()` → prefix `_`). It applies with equal force to XCUITest's Swift
     `class` name, which has the identical restriction and is unguarded.

   Moving the helper is the moment to close that gap, instead of moving a copy of the bug
   alongside the fix.
3. **Move `_ms` into `codegen_common.py`.** The helper is identical in `codegen_playwright.py`
   and `codegen_uiautomator.py`. XCUITest's `codegen.py` does not need a milliseconds conversion
   today. It gains access to the shared helper without being forced to use it.
4. **Add a shared `is_plain_substring(pattern)` to `codegen_common.py`, backed by the existing
   `_RE_METACHARS`.** `codegen.py` and `codegen_uiautomator.py` call it instead of each inlining
   the `set(pattern) & _RE_METACHARS` check. The frozenset moves alongside it as a common-module
   constant, with one comment instead of two near-identical ones.
5. **Consider a shared `NetworkUnsupported`-style helper** for the `_NO_NETWORK` TODO block of the
   two black-box mobile targets (the wait-until-request and request/requestSequence-assertion
   lines).
   - This step is scoped as "consider" rather than "do". The two constants' wording is
     target-specific prose that names the actual backend (XCUITest or the adb backend), and a
     shared helper would need to parameterize it.
   - If the parameterization reads more awkwardly than the two short constants it replaces, an
     acceptable outcome is to keep the constants separate. The targets then share only the
     surrounding `// TODO` line shape, which is already largely identical.
   - Playwright has its own, already-different network story. It can intercept requests, so it is
     not part of this duplication, and it stays untouched.
6. **Leave the per-target `_emit_step` dispatch shape alone.** Each target has a chain of `if`
   branches that maps a `Step` variant to its target-specific lines. That chain is inherent to
   per-target code emission, since BE-0083 already unified everything above that layer. Collapsing
   the chain further is out of scope here (see Alternatives considered).

Each step is independently landable and behavior-preserving, except where called out. The one
exception is the digit-guard fix in step 2. This is a narrowly scoped correctness fix that
piggybacks on the move instead of getting a separate proposal. Duplicating a known bug into the
shared module would defeat the point of centralizing.

## Alternatives considered

**A shared iterator that calls per-target extension points to collapse the three `_emit_step` if-chains
directly**, rather than stopping at the small helpers above. We considered and deferred it.

The three `_emit_step` implementations differ enough in their per-step-kind line generation
(Swift, Kotlin, and TypeScript syntax, plus each target's own selector and predicate builders).
Forcing them through one generic dispatch would need a wider extension surface than the current
`CodeGenerator` protocol. The payoff would appear once a fourth target exists.

Landing the concrete, already-duplicated helpers now is lower effort and higher reward. The
dispatch shape can be revisited when an Android (or other) target lands. That is better
than designing an abstraction ahead of its second concrete need.

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
