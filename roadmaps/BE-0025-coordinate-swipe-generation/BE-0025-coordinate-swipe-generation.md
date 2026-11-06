**English** · [日本語](BE-0025-coordinate-swipe-generation-ja.md)

# BE-0025 — Coordinate swipe generation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0025](BE-0025-coordinate-swipe-generation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0025") |
| Implementing PR | [#217](https://github.com/bajutsu-e2e/bajutsu/pull/217) |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

`swipe { from, to }` currently falls back to a `// TODO` comment in generated tests.

## Motivation

`codegen` maps a passing scenario to a native XCUITest. The mapping is purely structural, so every step a scenario can express should have a generated equivalent. Otherwise the emitted test is silently incomplete.

Today the selector form of `swipe` (`{ on, direction }`) maps to `swipeUp/Down/Left/Right()`. The coordinate form (`{ from, to }`) emits only a `// TODO` comment. A scenario that swipes by raw coordinates therefore runs under `run`, but it loses that gesture when the team takes the generated XCUITest into its own Xcode CI.

The gap is small but real. Coordinate swipes exist for cases the selector form cannot reach: a map pan, a custom-drawn canvas, and a drag with no addressable element. A team is most likely to want exactly those cases preserved.

## Detailed design

XCUITest expresses an arbitrary drag with `XCUICoordinate.press(forDuration:thenDragTo:)`. The generated helper builds two coordinates relative to a stable anchor and drags between them:

```swift
private func coord(_ x: CGFloat, _ y: CGFloat) -> XCUICoordinate {
  let origin = app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
  return origin.withOffset(CGVector(dx: x, dy: y))
}
```

`swipe: { from: [x1, y1], to: [x2, y2] }` then emits `coord(x1, y1).press(forDuration: 0.1, thenDragTo: coord(x2, y2))`. This mirrors the idb backend, which already adds a short duration. The duration lets SwiftUI recognize the drag as a pan, not an instantaneous flick.

The design stays within the prime directives:

- **Determinism.** Coordinates in the scenario are fixed numbers, so the generated drag is reproducible. The mapping remains purely structural. No AI is consulted at generation time, and the run/CI gate is unaffected.
- **Portability.** Coordinates anchor to the app window's top-left through `withNormalizedOffset`. Bajutsu already uses that origin convention, so the generated test reads the numbers the way `run` does. The coordinate form stays the documented last resort ([scenarios](../../docs/scenarios.md#swipe)). The selector form remains preferred because it survives layout changes.
- **App-agnostic.** No per-app configuration is introduced. The emitted helper is identical across apps, like the existing `el`, `byLabel`, and `matchingId` helpers.

The `// TODO` fallback path itself stays. It still catches genuinely unsupported constructs (see [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)). This proposal removes only coordinate swipes from that set.

## Alternatives considered

- **Resolve the coordinates back to an element and emit a directional swipe.** This requires inferring which element sits under the start point and which direction the drag approximates. That inference is the ambiguity coordinate swipes exist to avoid. It also reintroduces non-determinism, because the answer depends on the screen at generation time. Rejected: it changes the gesture's meaning.
- **Leave coordinate swipes as a `// TODO` and document the omission.** The generator stays simpler, but the structural mapping stays incomplete for the one gesture a team most needs preserved (custom canvases, map pans). Rejected: the mapping is cheap, and users can see the gap.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[codegen.md](../../docs/codegen.md)
