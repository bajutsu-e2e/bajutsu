**English** · [日本語](BE-XXXX-normalized-coordinate-viewport-mapping-ja.md)

# BE-XXXX — Map a normalized coordinate through the viewport rather than the element tree's extent

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-normalized-coordinate-viewport-mapping.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

A normalized `[0,1]` coordinate becomes a device point by multiplying it by the screen's bounds, and
Bajutsu derives those bounds two ways: `screen_size_from_elements` (`bajutsu/elements.py`) takes the
maximum extent of the queried element tree, while `Driver.viewport()` asks the backend directly
([BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md)). The first overshoots the
screen whenever the tree keeps off-screen nodes, which BE-0326 established and introduced the second
to fix. That fix reached exactly one caller — the `scroll` action's stop condition. Every other
consumer still multiplies through the overshooting extent, the `tapPoint` step on the deterministic
`run` path among them. This item routes each consumer that wants *screen* bounds through the
viewport, and leaves the tree extent to the callers that genuinely want content bounds.

## Motivation

The two figures diverge by more than a rounding error. Measured on the showcase SwiftUI app's
Permissions tab (iPhone 17, iOS 26.5), `Driver.viewport()` reports `(402, 874)` — the real screen —
while `screen_size_from_elements(driver.query())` reports `(418, 2456)`, because a SwiftUI `Form`
keeps buffered off-screen rows in the accessibility tree. A normalized `(0.5, 0.5)` therefore maps to
`(209, 1228)` instead of `(201, 437)`: the y coordinate falls off the bottom of an 874-point screen
entirely.

What makes this more than a periphery bug is which step shares the arithmetic. `tapPoint` is a
deterministic scenario step, and its handler (`orchestrator/actions/handlers/gestures.py`) scales the
author's normalized point by `screen_size_from_elements` — its own comment names the alert guard and
the crawl as sharing "one screen-size definition". So a `tapPoint` on any scrollable screen taps
somewhere the author did not name, or off the screen, while the run records the step as performed:
the actuation record and what the device received disagree, which is the failure mode prime directive
2 exists to prevent. The directional `swipe` distance is scaled the same way, so on that same
Permissions tab a swipe travels a fraction of 2456 points rather than of 874 — roughly 2.8 times too
far.

The defect surfaced while capturing the alert fixtures for
[BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification.md),
and the alert guard shows the cost most sharply. That item measured a real model answering the
notification prompt with `(126.6, 517.8)` against a `Don’t Allow` centre of `(127, 518)` — correct to
within half a point. The guard then scales that answer by `(418, 2456)` and taps `(132, 1456)`,
off the screen. A correct decision is destroyed by the mapping beneath it.

BE-0326 already reached the right diagnosis, so the gap here is reach rather than insight. One
detail is worth correcting along the way: that item's own `_viewport` helper documents its fallback
as "any other backend queries only on-screen elements, so the screen extent is the viewport", and the
measurement above falsifies that premise for XCUITest. The `scroll` action is nonetheless correct
today, because `XcuitestDriver` implements `ViewportProvider` and never reaches the fallback — correct
in spite of the stated reason rather than because of it, which is exactly the kind of claim a later
reader would build on.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **One shared helper for "the screen a normalized coordinate maps through."** Promote the
  `ViewportProvider`-first, tree-extent-fallback shape that already lives privately in
  `orchestrator/actions/handlers/scroll.py` into a single shared function, so the choice is made once
  rather than per caller. Restate its fallback honestly: the tree extent is a degrade for a backend
  that cannot report a viewport, not a claim that such a backend's tree never overshoots.
- **Audit every `screen_size_from_elements` caller and convert the screen-bounds ones.** Each caller
  wants either the screen (a normalized coordinate, a screen-fraction gesture distance) or the
  content (the tree's own extent), and the item's value is naming which per caller rather than
  converting them wholesale. The screen-bounds set identified so far is the `tapPoint` handler and
  the directional-swipe distance (`handlers/gestures.py`), the vision alert guard
  (`agents/alerts.py`), and the crawl's coordinate-tap replay (`crawl/core.py`); the assertion,
  `serve`, and pipeline readers need the same case-by-case decision rather than an assumed verdict.
- **A regression test per converted caller, on the fast gate.** No device is needed: `FakeDriver`
  implements `ViewportProvider` over BE-0326's own scrollable model, so it can serve a tree whose
  extent exceeds its viewport. Each conversion is then pinned by asserting the mapped point is the
  viewport-relative one *and* differs from what the tree extent would have produced — a test that
  fails before the change and passes after it, rather than one that merely agrees with the new code.
- **One on-device check that the deterministic path is fixed.** A `tapPoint` scenario against a
  scrollable showcase screen on the iOS lane, whose expectation holds only if the tap landed where
  the normalized coordinate names. The fast-gate tests prove the arithmetic; only a real device
  proves the tree really overshoots there, which is the premise the arithmetic rests on.

Nothing here adds a model call, a fixed sleep, or a per-app knob: the change replaces one geometric
source with another behind the existing driver interface, so the deterministic `run` verdict stays
deterministic and every backend keeps a correct answer through the fallback.

## Alternatives considered

- **Make `screen_size_from_elements` return the viewport itself.** It is a pure function over an
  element tree with no backend to ask, so it cannot; and some callers genuinely want the content
  extent, so redefining it would silently repurpose every call site instead of making each one
  choose.
- **Fix only the alert guard, where the defect was measured.** The `tapPoint` step shares the same
  helper and the same arithmetic while sitting on the deterministic verdict path, so repairing the
  AI periphery and leaving the gate path wrong would invert the priority between them.
- **Make `viewport()` a `Driver` requirement instead of an opt-in Protocol.** That changes the
  driver interface across every implementation, and it buys nothing here: the `ViewportProvider`-first shape
  with a tree-extent fallback already yields a correct answer on every backend that reports one, and
  a defensible one where none does.
- **Remove `tapPoint` from the scenario schema rather than fix it.** It is the documented escape
  hatch for a control no selector can address, and the alert guard's dismissal depends on the same
  mapping regardless, so removing the step would leave the defect in place.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Promote the shared "screen a normalized coordinate maps through" helper, with an honest fallback.
- [ ] Audit every `screen_size_from_elements` caller and convert the screen-bounds ones.
- [ ] Add a fast-gate regression test per converted caller over an overshooting `FakeDriver` tree.
- [ ] Add one on-device `tapPoint` check on a scrollable showcase screen.

## References

- [BE-0326 — The `scroll` action: scroll until an element appears](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md)
  — it diagnosed the overshoot and introduced `Driver.viewport()` / `ViewportProvider`
- [BE-0308 — Real-model verification of the system-alert guard](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification.md)
  — its capture measured the divergence, and its Progress log records the figures quoted above
- [BE-0349 — Verify tappability before acting, with a bounded scroll safety net](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md)
  — the adjacent question of whether a target is reachable, once the coordinate aimed at it is right
- The sibling proposal *Actuate the vision alert guard's decision through the native SpringBoard tap*
  (`vision-alert-guard-native-actuation`), which addresses the second defect BE-0308 measured: on
  iOS, an app-scoped coordinate tap cannot press a SpringBoard prompt's button at all. Fixing the
  mapping proposed here is necessary but not sufficient for the iOS vision guard, and neither item
  depends on the other landing first.
- `bajutsu/elements.py` (`screen_size_from_elements`), `bajutsu/drivers/base.py`
  (`ViewportProvider`), `bajutsu/orchestrator/actions/handlers/scroll.py` (`_viewport`),
  `bajutsu/orchestrator/actions/handlers/gestures.py`, `bajutsu/agents/alerts.py`,
  `bajutsu/crawl/core.py`
