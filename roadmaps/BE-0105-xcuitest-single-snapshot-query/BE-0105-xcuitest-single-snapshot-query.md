**English** · [日本語](BE-0105-xcuitest-single-snapshot-query-ja.md)

# BE-0105 — Single-snapshot element query for XCUITest

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0105](BE-0105-xcuitest-single-snapshot-query.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0105") |
| Implementing PR | [#670](https://github.com/bajutsu-e2e/bajutsu/pull/670) |
| Topic | Platform support (iOS / Android / Web / Flutter) |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) |
<!-- /BE-METADATA -->

## Introduction

Replace the XCUITest runner's per-element, per-attribute element query with a single
snapshot walk, cutting `GET /elements` from hundreds of XCUITest round-trips to one — while
keeping BE-0019's addressing guarantees intact (Python resolves the unique element, the runner
acts on exactly that element, ambiguity fails immediately, staleness fails loudly).

## Motivation

On the showcase app's 84-element screen, a single `GET /elements` takes 10–12 seconds. Because
that query gates run readiness, every condition-wait poll, and each step's element resolution, the
whole run is rate-limited by it; a stopgap has already raised the Python driver's socket timeout to
30 seconds so requests do not simply time out.

The likely cause is the shape of the query. The runner's `queryElements()` walks
`app.descendants(matching: .any).allElementsBoundByIndex` and reads `identifier`, `label`, `value`,
`frame`, `isEnabled`, `isSelected`, and `elementType` **individually** for each element. Each read is
an XCUITest round-trip, so the count scales as elements × attributes — roughly 84 × 7 ≈ 600 round-trips
for one screen. Materializing an `XCUIElement` per node via `allElementsBoundByIndex` is itself costly
on top of that.

This is a follow-on to [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md),
not a re-litigation of it. BE-0019 established the snapshot-handle addressing — `SnapshotStore` mints a
per-snapshot handle for each element, Python resolves the unique element from the `/elements` JSON, and
the runner acts on the exact element that handle maps to. That design optimized for **correctness**, not
query latency. This item narrows it: make the query cheap without weakening any of those guarantees.

The design wall is real. The fast read, the public `XCUIElement.snapshot()` (`app.snapshot()`), returns
an attribute-bearing subtree in a single round-trip — but its nodes are `XCUIElementSnapshot` **values**,
not tappable `XCUIElement`s. Reconciling "one cheap read" with "tap exactly the element Python resolved"
is the problem this proposal solves.

## Detailed design

The change is confined to the runner-side `ElementProviding` conformance (the real XCUITest-backed
implementation supplied by the UI test target) plus a small Python timeout revert. The public
`ElementProviding` protocol, `Router`, `SnapshotStore`'s handle scheme, and the Python driver's
resolve → handle flow are all **unchanged** — `ElementSnapshot.backingElement` is already an opaque
`AnyObject`, so what it holds can change with no effect above it.

1. **Measure first.** Before choosing, establish where the time goes — the `allElementsBoundByIndex`
   materialization versus the per-attribute reads — and record a baseline on a named environment (the
   Simulator model, the Xcode version, and the showcase 84-element screen). The measurement mainly
   confirms the hypothesis and fixes the baseline for the acceptance number; both suspected costs are
   removed by the same single-snapshot approach, so the design direction does not hinge on which
   dominates.
2. **Single-snapshot query.** `queryElements()` performs **one** `app.snapshot()` walk and reads every
   attribute from the returned tree — no per-element attribute round-trips. It emits the same normalized
   `Element[]` shape (`identifier` / `label` / `value` / `traits` / `frame`) so `find_all` /
   `resolve_unique` and the `/elements` JSON contract are unchanged.
3. **Position-path backing with attribute re-verification.** Each node records a **root-relative index
   position path** in `ElementSnapshot.backingElement` (opaque, so no protocol change). At `tap` /
   `gesture` time the runner re-derives the `XCUIElement` from that path — a single element resolution,
   not a re-walk of all 84 — and then **verifies the re-derived element's attributes
   (`identifier` / `label` / `traits` / `frame`) match those recorded at snapshot time**. On a mismatch
   it returns `stale`; the driver raises the same error a vanished element raises today. This upgrades
   stale detection from BE-0019's generation-only scheme (which catches an old-snapshot handle but not a
   same-generation UI change) to an attribute match, preserving "never silently act on whatever now
   matches." A pure index path without the attribute check would risk tapping a different element after a
   sibling-order change; the verification is what keeps it deterministic-safe.
4. **Driver timeout re-tuning.** Once the query is within target, revert the stopgap socket timeout
   (currently 30 s) to a bounded value in `bajutsu/drivers/xcuitest.py`, so an unresponsive runner still
   fails loudly within a reasonable window rather than hanging for 30 s.
5. **Validation split.** The fast gate (fake transport) guards **addressing correctness** — path
   re-derivation returns `stale` on an attribute mismatch, and an ambiguous selector still fails before
   any request — but it **cannot** measure Swift-side query latency, so the acceptance *number* (a typical
   screen's `/elements` within the target) is an on-device / `e2e.yml` measurement, not a fast-gate
   assertion. The two are deliberately separated: the gate proves the new addressing does not regress,
   the on-device path proves the speed.

**Acceptance criteria.** A typical screen's `GET /elements` completes within the target (proposed:
1–2 seconds on the measured environment) with no regression to BE-0019's guarantees — Python-side
resolution, actuation of exactly the resolved element via its handle, ambiguous selectors failing
immediately, and stale handles failing explicitly.

## Alternatives considered

- **Private snapshot API (WebDriverAgent-style).** The fastest option: XCTest's internal, non-public
  symbols beneath `app.snapshot()` (historically `_XCTElementSnapshot` / `snapshotWithError:` and the
  `XCAXClient_iOS` accessibility client WebDriverAgent uses) fetch the whole attribute tree in one
  accessibility query, often another order of magnitude faster than the public call. Rejected as the primary path:
  private symbols are fragile across Xcode versions and pull against the thin-dependency stance
  (DESIGN §4) — the same reason BE-0019 deferred vendoring WebDriverAgent. It stays a deferred fallback,
  reconsidered only if the public `app.snapshot()` walk misses the target.
- **Narrow the walk (depth / type / `hittable`-only filters).** Dropping any element a selector could
  match breaks determinism (directive 2: an ambiguous selector must fail, and a match is never missed).
  The single-snapshot walk should make the full tree cheap enough that filtering is unnecessary, so it is
  kept as rejected-unless-proven-safe rather than a performance lever.
- **Identifier alone as the handle backing.** An accessibility `identifier` need not be unique, so
  re-resolving by it on the runner reintroduces the very ambiguity selection removed — already rejected
  by BE-0019. Position path + attribute verification is the unique-safe form.
- **Fold into BE-0019.** BE-0019's Detailed design is MECE around correctness and capabilities and its
  close-out is on-device validation; query performance is a separable, measurable concern with its own
  acceptance number and its own design alternatives. Landing it as a Related follow-on keeps both items'
  breakdowns clean, matching how the roadmap tracks performance follow-ons as their own items.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Measure and record the `/elements` baseline (where the time goes; named environment).
- [x] Single-snapshot `queryElements()` via `app.snapshot()`, same normalized `Element[]` output.
- [x] Position-path backing with attribute re-verification (stale on mismatch).
- [x] Revert the stopgap driver socket timeout to a bounded value.
- [x] Validation: fast-gate addressing-correctness tests; on-device latency measurement against the target.

Log:

- 2026-07-05: Implementation slice. The single-snapshot walk and the deterministic-safe
  addressing now live in the pure `BajutsuRunner` library so the `swift test` gate covers them:
  `PositionPath.swift` adds a normalized `SnapshotNode`, `flattenSnapshot(root:)` (pre-order,
  root-excluded, one `PositionPathBacking` per element), and `attributesMatch(...)`, with
  `PositionPathTests.swift` exercising path assignment, field copy, and match/mismatch. The
  XCTest-facing `XcuitestElementProvider` now takes one `app.snapshot()` and re-derives the live
  `XCUIElement` from the recorded index path at `tap` / `gesture` time, returning `.stale` on an
  attribute mismatch. The Python driver's stopgap 60s socket timeout is reverted to a bounded
  `_SOCKET_TIMEOUT_SECONDS` (`tests/test_xcuitest.py`). The `/elements` baseline and the on-device
  acceptance number (target 1–2s) remain, per the proposal's validation split.
- 2026-07-05: On-device measurement and validation on iPhone 17 Pro Max, iOS 26.5, Xcode 26.6
  (17F113), against the showcase SwiftUI screen (~112–114 elements). `GET /elements` went from a
  **~18.6s** median (old per-attribute walk) to a **~0.034s** median (single `app.snapshot()`) —
  well inside the 1–2s target. The old walk actually exceeded the reverted 15s socket timeout,
  confirming the stopgap was load-bearing. Addressing correctness verified end-to-end: the on-device
  driver conformance suite (`tests/test_driver_conformance_ondevice.py`, xcuitest + idb) passes all
  18 cases, so the position-path re-derivation taps exactly the resolved element and stale/ambiguous
  handling is intact. This completes the five-piece breakdown.

## References

[BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md),
[DESIGN §4](../../DESIGN.md), `bajutsu/drivers/xcuitest.py`,
`BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift`,
`BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift`
