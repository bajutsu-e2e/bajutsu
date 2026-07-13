**English** · [日本語](BE-XXXX-coordinate-tree-driver-base-ja.md)

# BE-XXXX — Extract a shared CoordinateTreeDriver base for idb and adb

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-coordinate-tree-driver-base.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`IdbDriver` (`bajutsu/drivers/idb.py`) and `AdbDriver` (`bajutsu/drivers/adb.py`) are the two
coordinate-based device backends: both dump an accessibility tree from the device, normalize it
into `Element`s, and act by tapping a resolved frame's center. Their determinism-sensitive read
path — the transient-empty retry, the settle loop, the stable-key projection, and the not-found
resolve loop — is close to byte-for-byte identical between the two files. This item proposes
extracting that shared logic into one base class, `CoordinateTreeDriver`, so each backend supplies
only its own tree source and its own actuators.

## Motivation

The two backends carry roughly 90 lines of near-verbatim duplication in exactly the part of the
driver layer where determinism matters most — the logic that decides whether a freshly read tree
is a real screen or a transient artifact of the device mid-transition:

- The six tuning constants `_READY_MIN` / `_EMPTY_RETRIES` / `_EMPTY_BACKOFF_S` /
  `_EMPTY_BACKOFF_MAX_S` / `_SETTLE_MAX_POLLS` / `_SETTLE_POLL_S`, same values and same comments
  (`bajutsu/drivers/idb.py:213-218`, `bajutsu/drivers/adb.py:183-188`).
- The `_StableKey` type alias (`bajutsu/drivers/idb.py:24`, `bajutsu/drivers/adb.py:45`).
- The `query()` retry loop, byte-for-byte identical apart from the describe call it wraps
  (`bajutsu/drivers/idb.py:229-246`, `bajutsu/drivers/adb.py:212-227`).
- `_is_transient_empty`, `_empty_backoff`, `_settle`, `_stable_key`, and `_resolve` — all identical
  logic. `AdbDriver._settle`'s own docstring even says "idb's logic"
  (`bajutsu/drivers/adb.py:239-258`), which is the duplication naming itself.

Because this logic is duplicated rather than shared, a fix to the transient-empty heuristic — for
example, tightening the backoff cap, or changing `_READY_MIN` after a new flake is diagnosed — has
to be made twice, in two files, by whoever happens to be touching either backend. Nothing forces
the second edit; the two read paths can silently drift apart, and a scenario that behaves
differently on iOS than on Android for no app-level reason becomes possible. This is the largest
and most correctness-sensitive duplication anywhere in the driver layer: hoisting it into one place
turns "remember to fix it twice" into "fix it once, both backends inherit it."

## Detailed design

The work is a pure hoist — no behavior changes — split into independent units:

1. **Introduce `CoordinateTreeDriver` in `bajutsu/drivers/`** (its own module, e.g.
   `coordinate_tree.py`, alongside `base.py`) holding everything that is identical today: the six
   tuning constants, the `_StableKey` alias, `query()`, `_settle`, `_stable_key`,
   `_is_transient_empty`, `_empty_backoff`, and `_resolve`. These carry the constructor-managed
   state (`_max_seen`, `_last_stable_key`) they close over today, so the base class owns that state
   too.
2. **Give the base class one abstract hook, `_describe()`.** Everything in (1) calls `_describe()`
   already (`idb.py:285-286`, `adb.py:229-230`); making it an abstract method (or a
   `NotImplementedError` stub) is the entire seam between shared logic and backend-specific
   argv/parse. Each subclass implements only its own describe: idb's `ui describe-all` + JSON parse
   (`parse_describe_all`), adb's `uiautomator dump` + XML parse (`parse_hierarchy`).
3. **Reparent `IdbDriver` and `AdbDriver` onto the base class**, deleting the now-shared members
   from each and keeping only what is genuinely backend-specific: idb's tap/swipe/text argv
   builders and the gRPC-companion text path; adb's scroll-into-view retry
   (`_scroll_into_view`/`_scroll_toward`, which idb has no equivalent of), the sendevent
   double-tap path, and its own actuators. `_resolve` stays shared even though adb's
   `_resolve_frame_and_screen` layers scroll-into-view on top of it — that layering is adb-specific
   and composes with the shared `_resolve` rather than duplicating it.
4. **Leave `XcuitestDriver` and `PlaywrightDriver` untouched.** Both have their own read models —
   XCUITest's native condition-wait capability and Playwright's DOM query — with no equivalent
   transient-empty/settle heuristic, so they are out of scope for this hoist and should not be
   forced onto the new base class.
5. **Add tests asserting the shared behavior is identical on both backends** — the transient-empty
   retry (a degenerate tree followed by a richer one), the exponential backoff schedule, and the
   settle loop's cache-hit/cache-miss paths — parametrized (or otherwise shared) across
   `IdbDriver` and `AdbDriver` fakes so a future change to the base class is verified against both
   subclasses at once, rather than against each driver's own copy of the test.

Determinism note: `_resolve`'s semantics — a unique match required, an ambiguous match (2+) fails
fast via `AmbiguousSelector` — are unchanged by this hoist; this item moves code, it does not
change what "found" or "ambiguous" means. Every existing test for either backend's read path should
pass unmodified against the new base class, which is itself evidence the hoist is
behavior-preserving.

## Alternatives considered

- **A mixin instead of a base class.** Either shape can carry the shared state and methods; a
  mixin would let `IdbDriver`/`AdbDriver` combine it with some other base if one were ever needed.
  This item picks a plain base class because the abstract `_describe()` hook reads more naturally
  as "a template method a subclass completes" than as a mixin contract, but a mixin is a reasonable
  alternative if a future need for multiple inheritance makes it clearly better.
- **Fix duplication drift by convention (a comment pointing each file at its twin).** Rejected: this
  is exactly the status quo — `AdbDriver._settle`'s docstring already says "idb's logic" as a
  comment-level pointer, and the drift risk this item is written against is precisely that a
  convention doesn't force the second edit the way a shared base class does.
- **Leave the duplication and only add cross-backend tests.** Rejected: tests can catch a drift
  after the fact, but they don't remove the duplicate-edit burden that causes the drift in the
  first place; the hoist and the tests are complementary, not substitutes (see design step 5, which
  keeps the tests as part of this item rather than dropping them).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Introduce `CoordinateTreeDriver` holding the shared constants, `query()`, `_settle`,
      `_stable_key`, `_is_transient_empty`, `_empty_backoff`, and `_resolve`.
- [ ] Give the base class one abstract `_describe()` hook.
- [ ] Reparent `IdbDriver` and `AdbDriver` onto the base class, deleting the now-shared members.
- [ ] Confirm `XcuitestDriver` and `PlaywrightDriver` stay untouched (out of scope).
- [ ] Add tests asserting the shared transient-empty/settle behavior is identical on both backends.

## References

- [`bajutsu/drivers/idb.py:209-321`](../../bajutsu/drivers/idb.py) — `IdbDriver`'s read path, the
  first copy of the duplicated logic.
- [`bajutsu/drivers/adb.py:180-331`](../../bajutsu/drivers/adb.py) — `AdbDriver`'s read path, the
  second copy.
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `resolve_unique` / `find_all`, the
  shared selector-resolution core that `_resolve` calls into and that this item leaves unchanged.
- [BE-0118 — Unify the `wait_for` polling contract across
  drivers](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md) — the
  prior cross-backend unification this item's `_resolve`/settle hoist builds on.
- Originates from a 2026-07 codebase-analysis pass (technical debt).
