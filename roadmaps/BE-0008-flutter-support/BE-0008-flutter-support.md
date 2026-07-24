**English** · [日本語](BE-0008-flutter-support-ja.md)

# BE-0008 — Flutter support

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0008](BE-0008-flutter-support.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0008") |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

Drive Flutter apps with the **existing XCUITest / adb backends unchanged**, by treating Flutter as
an id convention plus a verification task rather than a new backend or a semantics bridge. Flutter
renders its own pixels through Skia / Impeller, but neither native backend reads pixels — XCUITest
reads the XCTest automation snapshot and adb reads the UI Automator tree, and Flutter already
bridges its own semantics tree into both. A Flutter widget that sets
`SemanticsProperties.identifier` therefore surfaces as a resolvable element on both
[backends](../../docs/glossary.md#driver-backend-actuator-platform) with no new code. This item
ships the id convention, an example app, and the on-device verification that the surfacing actually
holds — not a new actuator.

## Motivation

The native iOS ([XCUITest](../../docs/drivers.md#xcuitest-ios)) and Android
([adb](../../docs/drivers.md#adb-android)) backends resolve
[selectors](../../docs/glossary.md#scenario-authoring) by reading an accessibility tree, never by
analyzing rendered pixels — the same for a Flutter app as for a native one. The two backends then
actuate differently: XCUITest taps the resolved element directly by its accessibility identifier (a
semantic tap, no coordinates), while adb, the one coordinate backend, computes a tap from the
element's frame center. Either way, the tap lands on the widget without ever touching a pixel, so
the premise "Flutter draws its own pixels, therefore it needs a new actuator" conflates two
independent layers. Flutter does draw a single opaque surface via Skia / Impeller, but
**separately** it maintains a semantics tree and translates it into the OS accessibility APIs:
Android's `AccessibilityBridge` turns each `SemanticsNode` into a virtual `AccessibilityNodeInfo`,
and the iOS engine exposes `SemanticsObject`s (`UIAccessibility` elements). XCUITest's automation
snapshot and adb's UI Automator dump read exactly that. Each semantics node also carries an
on-screen rectangle, so on Android a frame-center tap lands on the widget and Flutter's own gesture
arena routes the delivered OS touch to the right widget; on iOS, XCUITest resolves the same node by
identifier and taps it directly, with no coordinate math at all. The renderer (Skia or Impeller) is
irrelevant to either path.

Since Flutter 3.19, `SemanticsProperties.identifier` maps straight into that tree — `resource-id` on
Android (via `AccessibilityNodeInfo.setViewIdResourceName`) and `accessibilityIdentifier` on iOS. So
the platform-neutral `id` selector the rest of the system already uses lands on the surfaced
identifier, keeping the selector model, machine assertions, the orchestrator loop, and the reporter
byte-for-byte unchanged. What Flutter support needs is therefore the id convention and the proof
that it holds on device, not a new OS actuator or a framework bridge.

## Detailed design

### Why XCUITest / adb already reach a Flutter UI

Flutter draws its UI into one native view (Android `FlutterSurfaceView`, iOS `FlutterView`) with no
child views, so the OS accessibility tree sees one opaque surface by default. What makes the
widgets resolvable is the engine's accessibility bridge, which is independent of rendering: it
builds a semantics tree and pushes it into the OS accessibility APIs the native backends already
read. A `resource-id` surfaced this way is resolved by adb's unchanged `resolve_unique` path and
actuated by a frame-center coordinate tap; an `accessibilityIdentifier` surfaced this way is
resolved by XCUITest's automation snapshot and actuated by its native semantic tap — no coordinates
involved. This is why the item adds no backend: the surfacing already exists on both platforms, and
the work is to make an app use it and to prove the two conditions below hold.

### The two conditions that must hold

The bridge is not unconditional. Whether XCUITest / adb can drive a Flutter widget depends on the
framework's semantics state, not on the renderer:

- **The semantics tree is built lazily.** Flutter constructs it only when an accessibility client
  connects, or when the app calls `SemanticsBinding.instance.ensureSemantics()`. On Android,
  UI Automator connects as an accessibility service and triggers construction. Whether reading the
  XCTest automation snapshot (through BajutsuKit's resident runner) triggers it on iOS the same way
  is the open question to settle on device; if it does not, the convention documents a one-line
  `ensureSemantics()` call as the fallback.
- **Only widgets that carry semantics appear.** Standard Material / Cupertino widgets and text carry
  semantics automatically, but a `CustomPaint`-drawn control that is not wrapped in `Semantics` never
  enters the tree and cannot be resolved. Wrapping it in `Semantics(identifier: …)` is the same
  convention that surfaces the id. Whether a custom control is drawn by Skia or Impeller does not
  affect this — only whether the developer attached semantics does.

### The id convention

The convention stated in the docs, alongside the iOS and Android ones:

| `Selector` field | Flutter (via XCUITest / adb) |
|---|---|
| `id` (primary) | `Semantics(identifier: "…")` → `accessibilityIdentifier` (iOS) / `resource-id` (Android), Flutter 3.19+ |
| `label` (auxiliary) | the widget's semantics label (visible text) |
| `value` | the widget's semantics value (the state-value mirror, SPEC §2.1) |
| `traits` (role filter) | the semantics role surfaced as the platform widget class / trait |

Flutter 3.19 (February 2024) is the stated minimum, because `identifier` surfacing lands there.

### Work breakdown (MECE)

1. **Flutter showcase target.** A Flutter demo under `demos/` whose widgets set
   `Semantics(identifier: …)`, added as a `backend: [ios]` / `backend: [android]` target so the
   existing showcase scenarios (id / tap / type / value) run against it through XCUITest and adb
   unchanged. No Flutter-specific scenario DSL is introduced. Any `Element` normalization tweak a
   surfaced Flutter tree needs to map cleanly lands here.
2. **The id-convention docs.** State the Flutter id convention (the table above), the Flutter 3.19
   minimum, the lazy-semantics precondition and the `ensureSemantics()` fallback, and the explicit
   out-of-scope boundary (below) in `docs/drivers.md` and its ja mirror.
3. **On-device verification.** The item's real technical risk lives here — settle the three hypotheses
   on device and fold the results into the convention:
   - semantics-tree activation timing (Android via UI Automator's accessibility-service connection;
     iOS via XCUITest's automation snapshot through BajutsuKit's resident runner — the open question,
     with the `ensureSemantics()` fallback);
   - `MergeSemantics` merging and off-screen (scroll) culling, confirming a selector still resolves
     uniquely and documenting the caveats;
   - that `identifier` surfaces as `accessibilityIdentifier` / `resource-id` on both backends in
     3.19+.

### Out of scope

- **A Dart VM Service semantics bridge.** Not built (see *Alternatives considered*): it does not serve
  the app it is meant to save, and its preconditions conflict with the E2E use case.
- **The WebView / embedded-web hybrid case.** Owned by [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md),
  which is **Implemented** (`bajutsu/webview.py` + the BajutsuKit bridge). Extending it to the Android
  WebView is a BE-0037 follow-up, unrelated to Flutter's own rendering, so it is not part of this item.
- **Flutter Web (CanvasKit).** Flutter for the web paints to a canvas and does not surface elements
  into the DOM, so the Playwright backend cannot resolve them; the SEO/semantics DOM overlay is a
  separate concern and is out of scope.
- **Apps that neither surface `identifier` nor can be modified.** Without a developer-assigned id in
  *some* tree, coordinate actuation cannot resolve selectors deterministically and ambiguous-fails-fast
  cannot be honored, so such apps are honestly out of scope rather than served by a fragile bridge.

### Phasing — the last remaining platform

iOS ([XCUITest](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)),
[Android](../BE-0007-android-backend/BE-0007-android-backend.md) (adb), and
[Web](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) (Playwright) have all
landed and been validated on-device / on-gate, so this item is no longer blocked on a predecessor
landing — Flutter is the one remaining platform in the reach axis (see
[docs/vision.md §1](../../docs/vision.md#1-reach--more-platforms-and-surfaces)). What is left is this
item's own scope: a demo, a convention, and the on-device verification above — a fraction of a new
backend, not a framework-specific bridge.

## Alternatives considered

- **A Dart VM Service semantics bridge (the earlier primary fallback).** Rejected as built work,
  demoted to a documented option to revisit only on clear demand. Reading the semantics tree over the
  Dart VM Service requires a debug/profile build (the VM Service is disabled in release, which the E2E
  use case wants) and app-side `integration_test` / Flutter Driver instrumentation — and if the app
  can be instrumented, setting `identifier` is cheaper and needs no bridge, so the fallback's intended
  beneficiary (an app that cannot surface an id) is essentially empty. Flutter Driver is itself
  deprecated, so maintaining an in-repo VM Service client would track the framework for little reach.
  Revisit only if a concrete need appears for an app that cannot set `identifier` yet may be driven in
  a debug build.
- **A new OS-level actuator for Flutter (coordinate taps over rendered pixels).** Rejected: without a
  stable, developer-assigned id surfaced into *some* tree, coordinate actuation cannot resolve
  selectors deterministically, and ambiguous-fails-fast cannot be honored. Flutter's own semantics
  tree, bridged into the OS accessibility tree, is the right source — and the native backends already
  read it.
- **Building a bridge before the native trees land.** Deferred with the phasing note above while iOS
  and Android were still being built out; both have since landed (BE-0290, BE-0007), which is what
  unblocks this item's on-device verification work now.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Flutter showcase target — a `demos/` Flutter app using `Semantics(identifier: …)`, driven by the existing XCUITest / adb backends over the existing showcase scenarios.
- [ ] The id-convention docs — the Flutter id convention, the 3.19 minimum, the lazy-semantics precondition + `ensureSemantics()` fallback, and the out-of-scope boundary (`docs/drivers.md` + ja).
- [ ] On-device verification — settle the three hypotheses (semantics activation timing, `MergeSemantics`/culling, `identifier` surfacing) and fold the results into the convention.

## References

[DESIGN](../../DESIGN.md), `bajutsu/drivers/`, `bajutsu/backends.py`,
[drivers.md](../../docs/drivers.md), [vision.md](../../docs/vision.md),
[BE-0290 — Make XCUITest the default iOS backend](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md),
[BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md),
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)
