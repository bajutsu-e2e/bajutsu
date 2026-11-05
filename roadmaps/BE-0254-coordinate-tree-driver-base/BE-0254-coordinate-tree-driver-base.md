**English** · [日本語](BE-0254-coordinate-tree-driver-base-ja.md)

# BE-0254 — Extract a shared CoordinateTreeDriver base for idb and adb

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0254](BE-0254-coordinate-tree-driver-base.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0254") |
| Implementing PR | [#1074](https://github.com/bajutsu-e2e/bajutsu/pull/1074) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`IdbDriver` (`bajutsu/drivers/idb.py`) and `AdbDriver` (`bajutsu/drivers/adb.py`) are the two
coordinate-based device backends. Both dump an accessibility tree from the device and normalize it
into `Element`s. Both act by tapping the center of a resolved frame.

The two files also share a determinism-sensitive read path, and the shared code is close to
byte-for-byte identical. It covers the transient-empty retry, the settle loop, the stable-key
projection, and the not-found resolve loop.

This item extracts that shared logic into one base class, `CoordinateTreeDriver`. Each backend then
supplies only its own tree source and its own actuators.

## Motivation

The two backends carry roughly 90 lines of near-verbatim duplication. The duplicated code sits in
the part of the driver layer where determinism matters most. It decides whether a freshly read tree
is a real screen or a transient artifact of the device mid-transition.

- Five of the six tuning constants share values and comments: `_READY_MIN`, `_EMPTY_RETRIES`,
  `_EMPTY_BACKOFF_S`, `_EMPTY_BACKOFF_MAX_S`, and `_SETTLE_MAX_POLLS`
  (`bajutsu/drivers/idb.py:213-218`, `bajutsu/drivers/adb.py:202-215`).
  The sixth, `_SETTLE_POLL_S`, has since diverged: idb uses `0.05` and adb uses `0.0`. BE-0234
  tuned adb's poll to 0 because adb's read of about 2.4 seconds already paces the loop. The shared
  base therefore keeps that one constant as a per-backend value.
- The `_StableKey` type alias is identical (`bajutsu/drivers/idb.py:24`,
  `bajutsu/drivers/adb.py:64`).
- The `query()` retry loop is byte-for-byte identical, apart from the describe call it wraps
  (`bajutsu/drivers/idb.py:229-246`, `bajutsu/drivers/adb.py:248-263`).
- `_is_transient_empty`, `_empty_backoff`, `_settle`, `_stable_key`, and `_resolve` carry identical
  logic. The docstring of `AdbDriver._settle` even says "idb's logic"
  (`bajutsu/drivers/adb.py:292-311`), so the duplication names itself.

Because the logic is duplicated, a fix to the transient-empty heuristic must be made twice. An
example is tightening the backoff cap, or changing `_READY_MIN` after a new flake is diagnosed.
The fix lands in two files, made by whoever happens to touch either backend. Nothing forces the
second edit. The two read paths can silently drift apart. A scenario could then behave differently
on iOS than on Android for no app-level reason.

This is the largest and most correctness-sensitive duplication in the driver layer. Hoisting it
into one place turns "remember to fix it twice" into "fix it once, and both backends inherit it."

## Detailed design

The work is a pure hoist with no behavior change. It splits into independent units.

1. **Introduce `CoordinateTreeDriver` in `bajutsu/drivers/`.** It lives in its own module, for
   example `coordinate_tree.py`, alongside `base.py`. It holds everything that is identical today:
   - the five shared tuning constants (`_SETTLE_POLL_S` stays a per-backend value);
   - the `_StableKey` alias;
   - `query()`, `_settle`, `_stable_key`, `_is_transient_empty`, `_empty_backoff`, and `_resolve`.

   These members close over constructor-managed state today (`_max_seen`, `_last_stable_key`).
   The base class therefore owns that state too.
2. **Give the base class one abstract hook, `_describe()`.** Everything in step 1 already calls
   `_describe()` (`idb.py:285-286`, `adb.py:265-266`). Making it an abstract method (or a
   `NotImplementedError` stub) is the entire seam between the shared logic and the backend-specific
   argv and parse. Each subclass implements only its own describe:
   - idb: `ui describe-all` plus a JSON parse (`parse_describe_all`).
   - adb: `uiautomator dump` plus an XML parse (`parse_hierarchy`).
3. **Reparent `IdbDriver` and `AdbDriver` onto the base class.** Each driver deletes the
   now-shared members and keeps only what is genuinely backend-specific:
   - idb: the tap, swipe, and text argv builders, and the gRPC-companion text path.
   - adb: the scroll-into-view retry (`_scroll_into_view` and `_scroll_toward`, which idb has no
     equivalent of), the sendevent double-tap path, and its own actuators.

   `_resolve` stays shared. adb's `_resolve_frame_and_screen` layers scroll-into-view on top of
   `_resolve`. That layer is adb-specific, and it composes with the shared `_resolve` instead of
   duplicating it.
4. **Leave `XcuitestDriver` and `PlaywrightDriver` untouched.** Each has its own read model:
   XCUITest's native condition-wait capability, and Playwright's DOM query. Neither has an
   equivalent transient-empty or settle heuristic. Both are out of scope for this hoist and should
   not be forced onto the new base class.
5. **Add tests asserting that the shared behavior is identical on both backends.** The tests cover:
   - the transient-empty retry (a degenerate tree followed by a richer one);
   - the exponential backoff schedule;
   - the settle loop's cache-hit and cache-miss paths.

   The tests are parametrized (or otherwise shared) across `IdbDriver` and `AdbDriver` fakes. A
   future change to the base class is then verified against both subclasses at once, rather than
   against each driver's own copy of the test.

Determinism note: this hoist leaves the semantics of `_resolve` unchanged. A unique match is
required, and an ambiguous match (2 or more) fails fast via `AmbiguousSelector`. This item moves
code and does not change what "found" or "ambiguous" means. Every existing test for either
backend's read path should pass unmodified against the new base class. That result is itself
evidence that the hoist preserves behavior.

## Alternatives considered

- **A mixin instead of a base class.** Either shape can carry the shared state and methods. A mixin
  would let `IdbDriver` and `AdbDriver` combine it with another base if one were ever needed. This
  item picks a plain base class because the abstract `_describe()` hook reads more naturally as a
  template method that a subclass completes than as a mixin contract. A mixin remains a reasonable
  alternative if a future need for multiple inheritance makes it clearly better.
- **Fix duplication drift by convention (a comment pointing each file at its twin).** Rejected.
  This is exactly the status quo: the docstring of `AdbDriver._settle` already says "idb's logic"
  as a comment-level pointer. The drift risk this item targets is that a convention does not force
  the second edit the way a shared base class does.
- **Leave the duplication and only add cross-backend tests.** Rejected. Tests can catch a drift
  after the fact, but they do not remove the duplicate-edit burden that causes the drift. The hoist
  and the tests complement each other. Design step 5 keeps the tests in this item for that reason.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Introduce `CoordinateTreeDriver` holding the shared constants, `query()`,
      `_stable_key`, `_is_transient_empty`, `_empty_backoff`, `_resolve`, and `wait_for`. `_settle`
      turned out to have already diverged by the time this landed — idb bounds it by a fixed poll
      count, adb by a wall-clock deadline (BE-0245, which shipped after this proposal was written) —
      a genuine strategy difference, not shared tuning, so it stays per-backend rather than being
      forced into the base (see "Detailed design divergence" below).
- [x] Give the base class one abstract `_describe()` hook.
- [x] Reparent `IdbDriver` and `AdbDriver` onto the base class, deleting the now-shared members.
- [x] Confirm `XcuitestDriver` and `PlaywrightDriver` stay untouched (out of scope).
- [x] Add tests asserting the shared transient-empty/settle behavior is identical on both backends
      (`tests/test_coordinate_tree.py`, parametrized across both real subclasses).

### Detailed design divergence (found during implementation)

The Motivation section, written in 2026-07, described `_settle` as byte-for-byte identical between
the two drivers. By implementation time, adb's `_settle` had changed to a wall-clock deadline
(`_SETTLE_DEADLINE_S`), while idb kept a fixed poll count (`_SETTLE_MAX_POLLS`). That is a real
behavioral difference, not merely diverged tuning constants.

Hoisting `_settle` as proposed would have forced one backend onto the other's strategy. That would
violate the "pure hoist, no behavior changes" mandate, so `_settle` stays per-backend.

The rest of the Detailed design hoisted as planned: the four remaining shared constants,
`_StableKey`, `query()`, `_stable_key`, `_is_transient_empty`, `_empty_backoff`, and `_resolve`.
`wait_for` also turned out to be byte-identical, so the same pass hoisted it. Every pre-existing
test for either backend's read path passes unmodified against the new base class.

## References

- [`bajutsu/drivers/idb.py:209-321`](../../bajutsu/drivers/idb.py) — `IdbDriver`'s read path, the
  first copy of the duplicated logic.
- [`bajutsu/drivers/adb.py:194-335`](../../bajutsu/drivers/adb.py) — `AdbDriver`'s read path, the
  second copy.
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `resolve_unique` / `find_all`, the
  shared selector-resolution core that `_resolve` calls into and that this item leaves unchanged.
- [BE-0118 — Unify the `wait_for` polling contract across
  drivers](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md) — a
  narrower precedent that unified only `Driver.wait_for` into a single-shot contract; this item is
  broader (hoisting the shared `query`/settle/`_resolve` machinery), and complements rather than
  depends on it.
- Originates from a 2026-07 codebase-analysis pass (technical debt).
