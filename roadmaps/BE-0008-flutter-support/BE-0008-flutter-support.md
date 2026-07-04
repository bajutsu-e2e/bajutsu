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

Support for cross-rendered UIs — Flutter, and the related React Native / WebView hybrid cases — by
reaching into the framework's own semantics tree rather than adding a new OS-level actuator. These
UIs draw their own pixels (Flutter) or embed a WebView (hybrids), so their elements often do not
surface in the OS accessibility (a11y) tree the native backends rely on. The answer is a
**semantics bridge**, not a new OS actuator.

## Motivation

The native iOS and Android backends read the OS accessibility tree to resolve selectors. Flutter
draws its own pixels and does not, by default, surface its widgets into that tree; WebView-based
hybrids embed web content whose DOM is invisible to the OS a11y layer. So a coordinate or
a11y-tree actuator alone cannot reliably resolve `id` selectors against a cross-rendered UI. What
these frameworks expose instead is their *own* semantics tree, reachable through the framework's
tooling. Bridging to that tree — rather than inventing yet another OS actuator — keeps the
selector model, machine assertions, and the orchestrator loop unchanged while extending coverage to
cross-rendered apps.

## Detailed design

Cross-rendered UIs (Flutter draws its own pixels; hybrids embed a WebView) often don't surface
elements in the OS a11y tree. These need a **semantics bridge** rather than a new OS actuator:

- **Flutter** — read the framework's semantics tree via `integration_test` / the VM Service /
  Flutter Driver, and normalize it into the common `Element` tree the rest of the system already
  consumes.
- **WebView / embedded-web hybrids** — a WebView→DOM (Document Object Model) bridge for the
  embedded-web case, so DOM nodes inside the WebView become resolvable elements. This overlaps with
  the dedicated WebView/hybrid item [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md);
  the two should be designed together so the bridge story is shared rather than duplicated.

The key design stance: this is a **semantics bridge layered onto the existing native backends**,
not a new OS-level actuator. The selector model (`resolve_unique`), machine assertions, the
orchestrator loop, and the reporter all stay unchanged — only the source the backend reads to
build the normalized `Element` tree changes.

### The cheapest path: native identifier surfacing

Since Flutter 3.19, `SemanticsProperties.identifier` maps straight into the OS accessibility tree —
`resource-id` on Android (via `AccessibilityNodeInfo.setViewIdResourceName`) and
`accessibilityIdentifier` on iOS. So a Flutter app whose widgets set `identifier` is **already
resolvable through the existing idb / adb backends**, with no bridge and no new actuator: the `id`
selector the rest of the system uses lands on the surfaced `resource-id` / `accessibilityIdentifier`.
This is the recommended primary path and the cheapest slice — it needs an example app and an id
convention in the docs, not a new backend — and it is why the semantics bridge below is a *fallback*
for the apps that cannot surface an identifier, not the first thing built.

### The fallback: a Dart VM Service semantics bridge

For apps that do not (or cannot) surface `identifier`, or that need richer widget querying, read the
framework's own tree over the **Dart VM Service** — the WebSocket the `integration_test` / Flutter
Driver extension exposes (the observatory URL) — and normalize its semantics/widget nodes into the
common `Element` tree the rest of the system already consumes. The design decision here mirrors
BE-0019's runner-channel choice: build a thin in-repo VM Service client over that WebSocket rather
than vendoring `appium-flutter-integration-driver`, keeping the thin-dependency stance (DESIGN §4).
Resolution stays Python-side (`resolve_unique`), so the bridge is a read source only — it changes
*where the `Element` tree comes from*, never how a selector resolves.

### Work breakdown (MECE)

1. **Native identifier path** — a Flutter example/showcase target whose widgets set
   `SemanticsProperties.identifier`, plus the id-convention docs, proving a Flutter app is driven by the
   existing idb / adb backends unchanged. Any normalization tweak needed so a surfaced Flutter tree maps
   cleanly onto `Element` lands here.
2. **VM Service semantics bridge** — a thin in-repo Dart VM Service client (`bajutsu/flutter.py`, mirroring
   `bajutsu/webview.py`) that connects to the observatory WebSocket, reads the semantics/widget tree, and
   normalizes it into `Element`s; wired as a read source behind the selected native actuator, not a new
   actuator.
3. **WebView / embedded-web hybrid bridge** — reuse the existing WebView→DOM bridge (`bajutsu/webview.py`
   + the BajutsuKit bridge server, [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md))
   so DOM nodes inside a WebView become resolvable, extended to the Android WebView case; designed together
   with BE-0037 so the bridge story is shared, not duplicated.
4. **Capabilities and disclosure** — the bridge advertises no new actuator capability; the selector model,
   machine assertions, orchestrator loop, and reporter stay unchanged, and the run manifest records which
   source (native tree / VM Service / WebView) supplied the elements.
5. **Validation** — fast gate (no device): normalize captured semantics-tree and DOM fixtures into `Element`s
   and assert resolve/ambiguity behavior over them. On-device (e2e): a Flutter demo driven both through native
   `identifier` (slice 1) and through the VM Service bridge (slice 2), plus a WebView-hybrid scenario.

### Phasing — Phase 3, after the two native trees

Treat cross-rendered support as the **third phase**, taken up only after the two native trees
(iOS via idb and Android via adb) have proven the abstraction. The native backends are the cheapest,
most direct way to confirm that the iOS-specific assumptions were really confined to the three seams
(actuator, environment manager, id convention); a semantics bridge is a harder, framework-specific
problem that is best attempted once the core has been shown to be genuinely platform-neutral. Defer
until the two native trees are solid.

## Alternatives considered

- **A new OS-level actuator for Flutter (coordinate taps over rendered pixels).** Rejected: without
  a stable, developer-assigned id surfaced into *some* tree, coordinate actuation cannot resolve
  selectors deterministically, and ambiguous-fails-fast cannot be honored. The framework's own
  semantics tree is the right source.
- **Building the bridge before the native trees land.** Deferred to Phase 3: cross-rendered support
  is framework-specific and harder; attempting it before the two native trees prove the abstraction
  would conflate two risks. See the phasing note above.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Native identifier path — Flutter example target using `SemanticsProperties.identifier`, driven through the existing idb / adb backends; id-convention docs.
- [ ] VM Service semantics bridge — thin in-repo Dart VM Service client normalizing the semantics tree into `Element`s (`bajutsu/flutter.py`).
- [ ] WebView / hybrid bridge — reuse the BE-0037 WebView→DOM bridge, extended to Android; designed with BE-0037, not duplicated.
- [ ] Capabilities and disclosure — bridge as a read source only; manifest records the element source.
- [ ] Validation — fast-gate normalization/resolve tests over fixtures; on-device Flutter (native id + VM Service) and WebView-hybrid e2e.

## References

[DESIGN](../../../DESIGN.md), `bajutsu/drivers/`, `bajutsu/backends.py`,
[BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md),
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)
