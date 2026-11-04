**English** · [日本語](BE-0008-flutter-support-ja.md)

# BE-0008 — Flutter support

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0008](BE-0008-flutter-support.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0008") |
| Implementing PR | [#1339](https://github.com/bajutsu-e2e/bajutsu/pull/1339) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

This item drives Flutter apps with the **existing XCUITest / adb backends unchanged**. It treats Flutter as an id convention plus a verification task, not as a new backend or a semantics bridge.

Flutter renders its own pixels through Skia / Impeller, but neither native backend reads pixels. XCUITest reads the XCTest automation snapshot, and adb reads the UI Automator tree. Flutter already bridges its own semantics tree into both.

A Flutter widget that sets `SemanticsProperties.identifier` thus surfaces as a resolvable element on both [backends](../../docs/glossary.md#driver-backend-actuator-platform) with no new code.

The item ships three deliverables: the id convention, an example app, and the on-device verification that the surfacing actually holds. It does not ship a new actuator.

## Motivation

The native iOS ([XCUITest](../../docs/drivers.md#xcuitest-ios)) and Android ([adb](../../docs/drivers.md#adb-android)) backends resolve [selectors](../../docs/glossary.md#scenario-authoring) by reading an accessibility tree. They never analyze rendered pixels, and this holds for a Flutter app as for a native one.

The two backends then actuate differently:

- XCUITest taps the resolved element directly by its accessibility identifier. This is a semantic tap with no coordinates.
- adb, the one coordinate backend, computes a tap from the element's frame center.

Either way, the tap lands on the widget without ever touching a pixel. The premise "Flutter draws its own pixels, therefore it needs a new actuator" conflates two independent layers.

Flutter does draw a single opaque surface via Skia / Impeller. **Separately**, it maintains a semantics tree and translates that tree into the operating system (OS) accessibility APIs.

- Android's `AccessibilityBridge` turns each `SemanticsNode` into a virtual `AccessibilityNodeInfo`.
- The iOS engine exposes `SemanticsObject`s (`UIAccessibility` elements).

XCUITest's automation snapshot and adb's UI Automator dump read that same tree.

Each semantics node also carries an on-screen rectangle. On Android, a frame-center tap lands on the widget, and Flutter's own gesture arena routes the delivered OS touch to the right widget. On iOS, XCUITest resolves the same node by identifier and taps it directly, with no coordinate math at all. The renderer (Skia or Impeller) is irrelevant to either path.

Since Flutter 3.19, `SemanticsProperties.identifier` maps straight into that tree. It becomes `resource-id` on Android (via `AccessibilityNodeInfo.setViewIdResourceName`) and `accessibilityIdentifier` on iOS.

The platform-neutral `id` selector that the rest of the system already uses thus lands on the surfaced identifier. The selector model, the machine assertions, the orchestrator loop, and the reporter stay byte-for-byte unchanged.

Flutter support needs the id convention and the proof that it holds on a device. It does not need a new OS actuator or a framework bridge.

## Detailed design

### Why XCUITest / adb already reach a Flutter UI

Flutter draws its UI into one native view (Android `FlutterSurfaceView`, iOS `FlutterView`) with no child views. The OS accessibility tree sees one opaque surface by default.

The engine's accessibility bridge makes the widgets resolvable, independently of rendering. The bridge builds a semantics tree and pushes it into the OS accessibility APIs that the native backends already read.

- adb's unchanged `resolve_unique` path resolves a `resource-id` surfaced this way, and adb actuates it by a frame-center coordinate tap.
- XCUITest's automation snapshot resolves an `accessibilityIdentifier` surfaced this way, and XCUITest actuates it by its native semantic tap, with no coordinates involved.

This is why the item adds no backend. The surfacing already exists on both platforms. The work is to make an app use it and to prove that the two conditions below hold.

### The two conditions that must hold

The bridge is not unconditional. Whether XCUITest / adb can drive a Flutter widget depends on the framework's semantics state, not on the renderer. Two conditions apply:

- **The semantics tree is built lazily.** Flutter constructs the tree when an accessibility client connects, or when the app calls `SemanticsBinding.instance.ensureSemantics()`.
  - On Android, UI Automator connects as an accessibility service and triggers construction.
  - On iOS, the open question is whether reading the XCTest automation snapshot (through BajutsuKit's resident runner) triggers construction the same way. The answer is to settle on a device. If it does not, the convention documents a one-line `ensureSemantics()` call as the fallback.
- **Widgets without semantics do not appear.** Standard Material / Cupertino widgets and text carry semantics automatically. A `CustomPaint`-drawn control that is not wrapped in `Semantics` never enters the tree and cannot be resolved. Wrapping the control in `Semantics(identifier: …)` is the same convention that surfaces the id. Whether Skia or Impeller draws a custom control does not affect this. Only whether the developer attached semantics does.

### The id convention

The docs state the convention alongside the iOS and Android ones:

| `Selector` field | Flutter (via XCUITest / adb) |
|---|---|
| `id` (primary) | `Semantics(identifier: "…")` → `accessibilityIdentifier` (iOS) / `resource-id` (Android), Flutter 3.19+ |
| `label` (auxiliary) | the widget's semantics label (visible text) |
| `value` | the widget's semantics value (the state-value mirror, SPEC §2.1) |
| `traits` (role filter) | the semantics role surfaced as the platform widget class / trait |

Flutter 3.19 (February 2024) is the stated minimum, because `identifier` surfacing lands in that version.

### Work breakdown (MECE)

1. **Flutter showcase target.** A Flutter demo under `demos/` has widgets that set `Semantics(identifier: …)`. It is added as a `backend: [ios]` / `backend: [android]` target, so the existing showcase scenarios (id / tap / type / value) run against it through XCUITest and adb unchanged. No Flutter-specific scenario DSL (domain-specific language) is introduced. Any `Element` normalization tweak that a surfaced Flutter tree needs to map cleanly lands in this unit.
2. **The id-convention docs.** `docs/drivers.md` and its ja mirror state five things: the Flutter id convention (the table above), the Flutter 3.19 minimum, the lazy-semantics precondition, the `ensureSemantics()` fallback, and the explicit out-of-scope boundary (below).
3. **On-device verification.** The item's real technical risk lives here. Three hypotheses are settled on a device, and the results are folded into the convention:
   - Semantics-tree activation timing. Android activates through UI Automator's accessibility-service connection. iOS activates through XCUITest's automation snapshot via BajutsuKit's resident runner. The iOS case is the open question, with the `ensureSemantics()` fallback.
   - `MergeSemantics` merging and off-screen (scroll) culling. The check confirms that a selector still resolves uniquely, and the caveats are documented.
   - `identifier` surfaces as `accessibilityIdentifier` / `resource-id` on both backends in Flutter 3.19+.

### Out of scope

- **A Dart VM Service semantics bridge.** Not built (see *Alternatives considered*). The bridge does not serve the app it is meant to save, and its preconditions conflict with the end-to-end (E2E) use case.
- **The WebView / embedded-web hybrid case.** [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) owns it, and that item is **Implemented** (`bajutsu/webview.py` plus the BajutsuKit bridge). Extending it to the Android WebView is a BE-0037 follow-up, unrelated to Flutter's own rendering. It is not part of this item.
- **Flutter Web (CanvasKit).** Flutter for the web paints to a canvas and does not surface elements into the Document Object Model (DOM), so the Playwright backend cannot resolve them. The search-engine-optimization (SEO) and semantics DOM overlay is a separate concern and is out of scope.
- **Apps that neither surface `identifier` nor can be modified.** Without a developer-assigned id in *some* tree, coordinate actuation cannot resolve selectors deterministically, and ambiguous-fails-fast cannot be honored. Such apps are honestly out of scope, rather than served by a fragile bridge.

### Phasing — the last remaining platform

iOS ([XCUITest](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)), [Android](../BE-0007-android-backend/BE-0007-android-backend.md) (adb), and [Web](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) (Playwright) have all landed and been validated on a device or on the gate. This item no longer waits on a predecessor landing. Flutter is the one remaining platform on the reach axis (see [docs/vision.md §1](../../docs/vision.md#1-reach--more-platforms-and-surfaces)).

The remaining scope is this item's own: a demo, a convention, and the on-device verification above. That is a fraction of a new backend, and it is not a framework-specific bridge.

## Alternatives considered

- **A Dart VM Service semantics bridge (the earlier primary fallback).** Rejected as built work, and demoted to a documented option to revisit only on clear demand. Three facts support the rejection:
  - Reading the semantics tree over the Dart VM Service requires a debug or profile build. The VM Service is disabled in release builds, which the E2E use case wants.
  - It also requires app-side `integration_test` / Flutter Driver instrumentation. If the app can be instrumented, setting `identifier` is cheaper and needs no bridge. The fallback's intended beneficiary, an app that cannot surface an id, is essentially empty.
  - Flutter Driver is itself deprecated. Maintaining an in-repo VM Service client would track the framework for little reach.

  Revisit only if a concrete need appears for an app that cannot set `identifier` yet may be driven in a debug build.
- **A new OS-level actuator for Flutter (coordinate taps over rendered pixels).** Rejected. Without a stable, developer-assigned id surfaced into *some* tree, coordinate actuation cannot resolve selectors deterministically, and ambiguous-fails-fast cannot be honored. Flutter's own semantics tree, bridged into the OS accessibility tree, is the right source, and the native backends already read it.
- **Building a bridge before the native trees land.** Deferred with the phasing note above, while iOS and Android were still being built out. Both have since landed (BE-0290, BE-0007), which is what unblocks this item's on-device verification work now.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Flutter showcase target — the `demos/showcase/flutter` Flutter app using `Semantics(identifier: …)`, a twin of the native showcase apps, driven by the existing XCUITest / adb backends over the shared showcase scenarios (`showcase-flutter` / `showcase-flutter-android[-noax]` targets).
- [x] The id-convention docs — the Flutter id convention, the 3.19 minimum, the lazy-semantics precondition + `ensureSemantics()` fallback, and the out-of-scope boundary (`docs/drivers.md` + ja).
- [x] On-device verification — settled the three hypotheses on device (see the log) and folded the results into the convention.

Log:

- Implemented in [#1339](https://github.com/bajutsu-e2e/bajutsu/pull/1339). The three hypotheses were settled on device (iPhone 17 Pro / iOS 26.5 Simulator via XCUITest; `bajutsu_api34` API 34 AVD via adb): (1) **semantics activation** — the backend's own accessibility query triggers Flutter's lazy semantics tree on **both** backends, so no `ensureSemantics()` call is needed on the driven path (kept behind an off-by-default `--dart-define=ENSURE_SEMANTICS` fallback); (2) **culling** — the scroll-to-element flow over the lazily-built Notices list resolves uniquely; (3) **`identifier` surfacing** — `Semantics(identifier:)` resolves as `accessibilityIdentifier` / `resource-id` on 3.19+. On **iOS/XCUITest** the full shared `scenarios/` suite passes except `network*` (out of scope) and the AI-path `permission` alert grant (as on the native apps). On **Android/adb** the same on-device set the native Compose lane runs passes (a rooted emulator for the `gestures_multitouch` two-finger sweep, as on the native lane); the one Flutter-specific gap is `device.yaml`'s device-control **clipboard**, which needs the in-app `BajutsuAndroid` receiver the plugin-free Flutter app does not link — the same app-cooperation class as `network`, folded into the `drivers.md` out-of-scope note (`text_editing` and the `push` half stay iOS-only in the native suite too).

## References

[DESIGN](../../DESIGN.md), `bajutsu/drivers/`, `bajutsu/backends.py`,
[drivers.md](../../docs/drivers.md), [vision.md](../../docs/vision.md),
[BE-0290 — Make XCUITest the default iOS backend](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md),
[BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md),
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)
