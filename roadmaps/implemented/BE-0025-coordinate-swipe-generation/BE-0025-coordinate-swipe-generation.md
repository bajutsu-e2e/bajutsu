**English** · [日本語](BE-0025-coordinate-swipe-generation-ja.md)

# BE-0025 — Coordinate swipe generation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0025](BE-0025-coordinate-swipe-generation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | [#217](https://github.com/bajutsu-e2e/bajutsu/pull/217) |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

`swipe { from, to }` currently falls back to a `// TODO`.

## Motivation

`codegen` maps a passing scenario to a native XCUITest. The mapping is purely structural,
so every step a scenario can express should have a generated equivalent — otherwise the
emitted test is silently incomplete. Today the selector form of `swipe` (`{ on, direction }`)
maps to `swipeUp/Down/Left/Right()`, but the coordinate form (`{ from, to }`) only emits a
`// TODO` comment. A scenario that swipes by raw coordinates therefore runs under `run` but
loses that gesture when the team takes the generated XCUITest into their own Xcode CI. The
gap is small but real: coordinate swipes exist for the cases the selector form cannot reach
(a map pan, a custom-drawn canvas, a drag with no addressable element), which are exactly the
cases a team is most likely to want preserved.

## Detailed design

XCUITest expresses an arbitrary drag with
`XCUICoordinate.press(forDuration:thenDragTo:)`. The generated helper would build two
coordinates relative to a stable anchor and drag between them:

```swift
private func coord(_ x: CGFloat, _ y: CGFloat) -> XCUICoordinate {
  let origin = app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
  return origin.withOffset(CGVector(dx: x, dy: y))
}
```

`swipe: { from: [x1, y1], to: [x2, y2] }` then emits
`coord(x1, y1).press(forDuration: 0.1, thenDragTo: coord(x2, y2))`, mirroring the idb backend,
which already adds a short duration so SwiftUI recognizes the drag as a pan rather than an
instantaneous flick.

This stays within the prime directives:

- **Determinism.** Coordinates in the scenario are fixed numbers, so the generated drag is
  reproducible. The mapping remains purely structural — no AI is consulted at generation time,
  and the run/CI gate is unaffected.
- **Portability.** Coordinates anchor to the app window's top-left via
  `withNormalizedOffset`, the same origin convention bajutsu already uses, so the generated
  test reads the numbers the same way `run` does. The coordinate form stays the documented
  last resort ([scenarios](../../../docs/scenarios.md#swipe)); the selector form remains preferred
  precisely because it survives layout changes.
- **App-agnostic.** No per-app configuration is introduced; the emitted helper is identical
  across apps, like the existing `el` / `byLabel` / `matchingId` helpers.

The `// TODO` fallback path itself stays — it still catches genuinely unsupported constructs
(see [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)).
This proposal only removes coordinate swipes from that set.

## Alternatives considered

- **Resolve the coordinates back to an element and emit a directional swipe.** This would
  require inferring which element sits under the start point and which direction the drag
  approximates, which is exactly the ambiguity coordinate swipes exist to avoid. It would also
  reintroduce non-determinism (the answer depends on the screen at generation time). Rejected:
  it changes the gesture's meaning.
- **Leave coordinate swipes as a `// TODO` and document the omission.** This keeps the
  generator simpler but leaves the structural mapping incomplete for the one gesture a team is
  most likely to need preserved (custom canvases, map pans). Rejected: the cost of the mapping
  is low and the gap is user-visible.

## References

[codegen.md](../../../docs/codegen.md)
