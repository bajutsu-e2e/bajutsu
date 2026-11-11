**English** · [日本語](BE-0396-ios-sfsafariviewcontroller-tree-ja.md)

# BE-0396 — Read SFSafariViewController's element tree from the process that draws it

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0396](BE-0396-ios-sfsafariviewcontroller-tree.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0396") |
| Implementing PR | [#1742](https://github.com/bajutsu-e2e/bajutsu/pull/1742) |
| Topic | Platform support |
| Related | [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md), [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) |
<!-- /BE-METADATA -->

## Introduction

`SFSafariViewController` is the in-app browser an iOS app presents for a sign-in page, a terms document, or a help article. Apple draws it in a separate process, `com.apple.SafariViewService`. From iOS 26, the accessibility snapshot that the XCUITest runner takes of the app under test stops at that process boundary.

A scenario that opens the in-app browser therefore sees an empty screen. It sees no page content and no browser chrome, with nothing to wait for and nothing to tap. The flow cannot be driven at all.

This item reads the browser's subtree from the application handle of the process that owns it. [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) already reads a SpringBoard permission prompt the same way. The runner merges that subtree into the `/elements` reply, so the browser's controls and its rendered page reach a scenario as ordinary elements.

The item also normalizes the one piece of browser chrome that the iOS versions name differently, so a single scenario file runs against both. The protocol and the host side stay unchanged. The runner reports one tree, and every existing action and assertion works on the browser's elements unmodified.

## Motivation

The gap is not that XCUITest cannot see the browser. The *one capture path* the runner uses stopped crossing the process boundary. We measured on a probe app that presents `SFSafariViewController` on a local page, with Xcode 26.6:

| Capture path | iOS 18.6 | iOS 26.5 |
|---|---|---|
| `app.snapshot()` (the runner's query, BE-0105) | whole browser subtree | **stops at the remote-view boundary** |
| `app.debugDescription` | whole browser subtree | whole browser subtree |
| `XCUIApplication(bundleIdentifier: "com.apple.SafariViewService").snapshot()` | whole browser subtree | whole browser subtree |

[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) made the query one `app.snapshot()` per screen instead of one round-trip per attribute. That cut 600 round-trips for a single screen down to one, and giving that up is not an option. The browser's tree has to come from somewhere else. The service's own handle answers with the complete tree on both iOS versions, in one round-trip, in the app's own coordinate space.

Two further facts decide the design. First, the browser exposes its rendered page as an accessibility tree, not as a Document Object Model (DOM). The page's headings, links, and buttons therefore arrive as ordinary elements addressable by their visible text.

The in-app `WKWebView` bridge of [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) reaches a DOM by running JavaScript inside the app's own web view. No such reach exists into another process. The browser needs none, because its page is already in the tree.

Second, the browser's own chrome silently drops `XCUIElement.tap()`. A resolved, hittable Close button does not dismiss the browser, while the same call on the page content works. Actuating a browser element at its frame's centre point lands on both.

A later reader can tell the contribution arrived by running the showcase's own scenario. The scenario waits for a heading rendered inside the browser, clicks a button on that page, waits for the click's effect, and leaves through the browser's dismiss control. Before this change, the scenario times out on iOS 26 waiting for the heading. After it, the one file passes on iOS 18.6 and iOS 26.5, against both the SwiftUI and the UIKit showcase apps.

## Detailed design

### Unit 1 — Read the browser's tree from the process that owns it

`XcuitestElementProvider` gains a lazily built handle for `com.apple.SafariViewService`, beside the SpringBoard handle that BE-0316 added. `queryElements()` checks the `state` of that handle first. The check costs nothing measurable: 0.00 ms, against the 4.18 ms that the `app.snapshot()` in the same query already costs. A run that never opens a browser is unaffected. When the service is not in the foreground, the query is what it was.

When the service *is* in the foreground, the reply is the app's tree plus the browser's tree. The app's own snapshot still needs one repair. Through iOS 18 it mirrors the whole browser subtree, which would report every browser element twice and read as an ambiguous selector.

`flattenSnapshot` therefore takes a `prune` predicate, and the app's walk drops the subtree rooted at the browser's `BrowserView…` node. A pruned child still consumes its own index, so its siblings keep the position paths from which a later actuation re-derives them.

The runner must also re-derive and actuate an element read from the service's tree against that tree. `ElementRoot` (`.app` / `.safariViewService`) is recorded on every `PositionPathBacking`. Both recovery steps, the position-path descent and the flat identity query, run against the application handle that the backing names. Without that, resolving a browser element against the app's handle would report a perfectly live control as stale on iOS 26.

### Unit 2 — Actuate a browser element at its own point

`tap` picks its target once: the element itself for everything in the app, or the coordinate at the live frame centre for a browser element. `XCUIElement` and `XCUICoordinate` both offer `tap()` / `doubleTap()` / `press(forDuration:)`, so a small `Tappable` protocol keeps the taps and duration branch single-copy.

The runner reads the frame live, not from the snapshot, so a browser still animating in is tapped where it now is. The existing hittability guard is unchanged and still runs first. A covered control is still refused, not tapped without a check.

### Unit 3 — Normalize the browser's chrome across iOS versions

We measured both runtimes. The browser reports one identifier for every control the two versions share: `URL`, `BackButton`, `ShareButton`, `ReloadButton`, `OpenInSafariButton`, and `PageFormatMenuButton` all travel unchanged. Two differences remain:

- iOS 26 identifies the dismiss control `Close`. iOS 18 gives it no identifier at all, and only the label `Done`.
- iOS 18 has a `ForwardButton` that iOS 26 does not.

`normalizeBrowserChrome` repairs the first difference and leaves the second alone. It reports the dismiss control under the identifier iOS 26 already uses, so `id: Close` addresses it on both versions. Adopting the platform's own name, not inventing one, follows `OS_BACK_BUTTON` in `bajutsu/drivers/base.py`, where the iOS convention `BackButton` is likewise the cross-platform vocabulary.

Recognition is structural, not textual. The dismiss control is the one button directly under `TopBrowserBar` that carries no identifier of its own. A label test would break on a Japanese-locale device, which reports neither `Done` nor `Close`. Where more than one unidentified button sits in that bar, the repair does nothing. A later iOS adding a second one must not have one of them silently renamed into the control a scenario taps to leave the browser.

The repair changes the reported identity alone. The element's backing passes through untouched, so the runner still re-derives and actuates the control by what the platform says about it. The label stays as well. The label is the localized string a screen reader announces, and rewriting `Done` to `Close` would make the tree, and the evidence captured from it, disagree with the screen. A `ForwardButton` that does not exist cannot be reported, so a scenario must not depend on it.

### Unit 4 — The showcase fixture and its lane

The showcase's Permissions tab already owns the deliberate out-of-process UI, and its System section already mirrors device state that an app-scoped query cannot see. The in-app browser joins it there, with the same identifiers in the SwiftUI and the UIKit app:

- `sys.openBrowser` presents the browser on `SHOWCASE_BROWSER_URL`.
- `sys.browser.value` mirrors the one fact the app itself observes about the browser: whether the page finished loading, as `SFSafariViewControllerDelegate` reports it.

`scenarios/browser.yaml` drives the flow end to end. It waits for a heading rendered inside the browser and asserts the browser's own address field. It then clicks a button on the page and waits for the click's effect. Finally it dismisses the browser through `id: Close` and reads the app's mirror once the app's own screen is back.

The scenario's own lane, `make -C demos/showcase e2e-browser`, serves the page from `demos/showcase/browser/`, so the assertions rest on fixed content, not on a live site's. The lane polls the server for readiness instead of sleeping, matching the runner's own discipline, and runs the one file against both toolkits.

The scenario is tagged `browser` and excluded from the discovery lanes, which serve no fixture page. That is the same "this file has its own lane" reason that already excludes `visual` and `systemalert`. The page is served over the development machine's loopback interface, so the lane is Simulator-only.

## Alternatives considered

- **Parse `app.debugDescription`.** The string crosses the process boundary on both versions. It is a debugging string with no stability contract, though, and parsing it would put every future Xcode's formatting changes on the run path.
- **Query per element instead of snapshotting.** `app.buttons` and `app.staticTexts` also cross the boundary. Building the tree that way is the roughly 600-round-trip cost that BE-0105 removed, so paying it again for every screen is a regression the whole suite would feel.
- **A separate endpoint, as BE-0316 gave the system alert.** A permission prompt is modal system UI that interrupts a scenario, so addressing it apart is right. The browser is part of the app's own flow: a scenario taps a link, reads the page, and comes back. Its elements belong in `/elements`, where every existing action and assertion already works on them. A separate endpoint would also cost an OpenAPI change, a host-side client, and new scenario surface for no gain in expressiveness.
- **Screenshot plus optical character recognition (OCR).** It would work anywhere. A verdict that depends on pixels and recognition is not the deterministic, machine-checkable assertion that the `run` gate rests on, though, and putting a model on that path is what prime directive 1 forbids.
- **Normalize the browser's label as well as its identifier.** Rejected. The label is what the screen announces, and a normalized `Close` would contradict the captured evidence. It would not travel anyway, since a localized device reports neither `Done` nor `Close`.
- **Report the app-side delegate signal alone.** `SFSafariViewControllerDelegate` tells the app whether the page loaded, and a scenario could assert that signal alone. It proves a load, not what the page shows, and it drives nothing. The showcase keeps the signal as `sys.browser.value`, one fact beside the tree, not a substitute for it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — read the browser's tree from `com.apple.SafariViewService`, prune the app's mirror of
  it, and record the element root on every backing.
- [x] Unit 2 — actuate a browser element at its own point, which the browser's chrome accepts and
  `XCUIElement.tap()` does not.
- [x] Unit 3 — normalize the dismiss control's identifier across iOS versions, structurally and
  without touching the label.
- [x] Unit 4 — the showcase screen in both toolkits, `scenarios/browser.yaml`, its fixture page and
  `e2e-browser` lane, and the bilingual SPEC / README updates.

Log:

- 2026-08-25 — all four units landed together. Verified on Xcode 26.6 against iOS 18.6 (iPhone 16
  Pro) and iOS 26.5 (iPhone 17 Pro): `make -C demos/showcase e2e-browser` passes on both runtimes
  for both the SwiftUI and the UIKit showcase app. The same scenario fails on iOS 26 with the
  provider change reverted, which is what makes the change load-bearing rather than incidental.

## References

- `BajutsuKit/Sources/BajutsuRunner/BrowserChrome.swift` — the chrome normalization and the
  identifiers it keys on, with the measured per-version differences recorded beside them.
- `BajutsuKit/Sources/BajutsuRunner/PositionPath.swift` — `ElementRoot`, and `flattenSnapshot`'s
  `prune` predicate.
- `BajutsuKit/Runner/Sources/XcuitestElementProvider.swift` — the merged query and the
  root-aware resolution and actuation.
- `demos/showcase/SPEC.md` §5.4 — the showcase contract for `sys.openBrowser` /
  `sys.browser.value`.
- [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) — the in-app
  `WKWebView` bridge, which reaches a DOM inside the app's own process.
- [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) — the
  one-`snapshot()` query this item extends to a second application handle.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) — the
  SpringBoard handle this item's second handle follows.
- [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the resident XCUITest runner
  that owns the provider.
