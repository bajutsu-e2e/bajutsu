**English** · [日本語](BE-0008-flutter-support-ja.md)

# BE-0008 — Flutter support

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0008](BE-0008-flutter-support.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0008") |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

Drive Flutter apps with the **existing idb / adb backends unchanged**, by treating Flutter as an
id convention plus a verification task rather than a new backend or a semantics bridge. Flutter
renders its own pixels through Skia / Impeller, but the native backends never read pixels — they
read the OS accessibility (a11y) tree, and Flutter already bridges its own semantics tree into that
tree. A Flutter widget that sets `SemanticsProperties.identifier` therefore surfaces as a
resolvable element on both native backends with no new code. This item ships the id convention, an
example app, and the on-device verification that the surfacing actually holds — not a new actuator.

## Motivation

The native iOS (idb) and Android (adb) backends resolve selectors by reading the OS accessibility
tree, never by analyzing rendered pixels — the same for a Flutter app as for a native one, with the
tap computed from the element's bounds center. So the premise "Flutter draws its own pixels,
therefore it needs a new actuator" conflates two independent layers. Flutter does draw a single
opaque surface via Skia / Impeller, but **separately** it maintains a semantics tree and translates
it into the OS accessibility APIs: Android's `AccessibilityBridge` turns each `SemanticsNode` into a
virtual `AccessibilityNodeInfo`, and the iOS engine exposes `SemanticsObject`s (`UIAccessibility`
elements). idb and adb read exactly that. Each semantics node carries an on-screen rectangle, so a
bounds-center tap lands on the widget, and Flutter's own gesture arena routes the delivered OS touch
to the right widget. The renderer (Skia or Impeller) is irrelevant to this path.

Since Flutter 3.19, `SemanticsProperties.identifier` maps straight into that tree — `resource-id` on
Android (via `AccessibilityNodeInfo.setViewIdResourceName`) and `accessibilityIdentifier` on iOS. So
the platform-neutral `id` selector the rest of the system already uses lands on the surfaced
identifier, keeping the selector model, machine assertions, the orchestrator loop, and the reporter
byte-for-byte unchanged. What Flutter support needs is therefore the id convention and the proof that
it holds on device, not a new OS actuator or a framework bridge.

## Detailed design

### Why idb / adb already reach a Flutter UI

Flutter draws its UI into one native view (Android `FlutterSurfaceView`, iOS `FlutterView`) with no
child views, so the OS a11y tree sees one opaque surface by default. What makes the widgets
resolvable is the engine's accessibility bridge, which is independent of rendering: it builds a
semantics tree and pushes it into the OS accessibility APIs the native backends already read. A
`resource-id` / `accessibilityIdentifier` surfaced this way is resolved by the unchanged
`resolve_unique` path; the coordinate tap lands via the semantics node's on-screen rect and Flutter's
own hit-testing. This is why the item adds no backend: the surfacing already exists, and the work is
to make an app use it and to prove the two conditions below hold.

### The two conditions that must hold

The bridge is not unconditional. Whether idb / adb can drive a Flutter widget depends on the
framework's semantics state, not on the renderer:

- **The semantics tree is built lazily.** Flutter constructs it only when an accessibility client
  connects, or when the app calls `SemanticsBinding.instance.ensureSemantics()`. On Android,
  uiautomator connects as an accessibility service and triggers construction. Whether idb's
  accessibility access triggers it on iOS is the open question to settle on device; if it does not,
  the convention documents a one-line `ensureSemantics()` call as the fallback.
- **Only widgets that carry semantics appear.** Standard Material / Cupertino widgets and text carry
  semantics automatically, but a `CustomPaint`-drawn control that is not wrapped in `Semantics` never
  enters the tree and cannot be resolved. Wrapping it in `Semantics(identifier: …)` is the same
  convention that surfaces the id. Whether a custom control is drawn by Skia or Impeller does not
  affect this — only whether the developer attached semantics does.

### The id convention

The convention stated in the docs, alongside the iOS and Android ones:

| `Selector` field | Flutter (via native backend) |
|---|---|
| `id` (primary) | `Semantics(identifier: "…")` → `resource-id` (Android) / `accessibilityIdentifier` (iOS), Flutter 3.19+ |
| `label` (auxiliary) | the widget's semantics label (visible text) |
| `value` | the widget's semantics value (the state-value mirror, SPEC §2.1) |
| `traits` (role filter) | the semantics role surfaced as the platform widget class / trait |

Flutter 3.19 (February 2024) is the stated minimum, because `identifier` surfacing lands there.

### Work breakdown (MECE)

1. **Flutter showcase target.** A Flutter demo under `demos/` whose widgets set
   `Semantics(identifier: …)`, added as a `backend: [ios]` / `backend: [android]` target so the
   existing showcase scenarios (id / tap / type / value) run against it through idb and adb unchanged.
   No Flutter-specific scenario DSL is introduced. Any `Element` normalization tweak a surfaced Flutter
   tree needs to map cleanly lands here.
2. **The id-convention docs.** State the Flutter id convention (the table above), the Flutter 3.19
   minimum, the lazy-semantics precondition and the `ensureSemantics()` fallback, and the explicit
   out-of-scope boundary (below) in `docs/drivers.md` and its ja mirror.
3. **On-device verification.** The item's real technical risk lives here — settle the three hypotheses
   on device and fold the results into the convention:
   - semantics-tree activation timing (Android via uiautomator's a11y connection; iOS via idb — the
     open question, with the `ensureSemantics()` fallback);
   - `MergeSemantics` merging and off-screen (scroll) culling, confirming a selector still resolves
     uniquely and documenting the caveats;
   - that `identifier` surfaces as `resource-id` / `accessibilityIdentifier` on both backends in 3.19+.

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

### Phasing — Phase 3, after the two native trees

Flutter support stays **Phase 3**, taken up after the native trees (iOS via idb and Android via adb)
have proven the abstraction, and it depends on [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)
being solid on device since Flutter-on-Android drives through adb. But the scope is now a demo, a
convention, and verification — a fraction of a new backend — rather than a framework-specific bridge.

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
  tree, bridged into the OS a11y tree, is the right source — and the native backends already read it.
- **Building a bridge before the native trees land.** Deferred with the phasing note above: Flutter
  support leans on the Android (adb) backend, so it follows the two native trees rather than preceding
  them.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Flutter showcase target — a `demos/` Flutter app using `Semantics(identifier: …)`, driven by the existing idb / adb backends over the existing showcase scenarios.
- [ ] The id-convention docs — the Flutter id convention, the 3.19 minimum, the lazy-semantics precondition + `ensureSemantics()` fallback, and the out-of-scope boundary (`docs/drivers.md` + ja).
- [ ] On-device verification — settle the three hypotheses (semantics activation timing, `MergeSemantics`/culling, `identifier` surfacing) and fold the results into the convention.

## References

[DESIGN](../../DESIGN.md), `bajutsu/drivers/`, `bajutsu/backends.py`,
[drivers.md](../../docs/drivers.md),
[BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md),
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)
