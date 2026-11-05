**English** · [日本語](BE-0037-webview-hybrid-support-ja.md)

# BE-0037 — WebView / hybrid support

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0037](BE-0037-webview-hybrid-support.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0037") |
| Implementing PR | [#400](https://github.com/bajutsu-e2e/bajutsu/pull/400), [#401](https://github.com/bajutsu-e2e/bajutsu/pull/401) |
| Topic | Platform support |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

The current implementation assumes a native a11y tree. This proposal adds support for accessing the Document Object Model (DOM) inside a WebView.

## Motivation

Many production iOS apps are hybrid. A native shell hosts a `WKWebView` for whole screens, such as checkout, help, an embedded web app, or an OAuth consent page. Bajutsu's selectors resolve against the native accessibility (a11y) tree. A WebView collapses its entire web content into a small, opaque region of that tree. The HTML buttons, fields, and links inside it are not addressable as native elements.

As a result, a flow that enters a WebView cannot be driven or asserted. There is no stable `id` to tap and no element to wait for. An ambiguous or empty match correctly fails the step. Any app with a web-backed step is effectively un-automatable past the WebView boundary. Reaching the DOM inside the WebView makes those flows testable.

## Detailed design

The core challenge is that the DOM and the native a11y tree are two different trees with two different addressing schemes. A selector must resolve unambiguously in whichever tree it targets. The proposal keeps the existing selector grammar for native elements and adds an explicit *web context* for the WebView's DOM. It does not silently blend the two trees.

- **Context boundary.** A step opts into the web context by scoping to the host WebView, for example `web: { within: { id: checkout.webview } }`. Selectors inside that block address the DOM. Outside it, selectors resolve natively exactly as today. The explicit boundary keeps every selector unambiguous about which tree it queries, so nothing is guessed.
- **DOM selectors.** Inside the web context, a selector addresses DOM nodes by CSS selector and, where stable, by the element's accessible name. This is the web analogue of `id`. The ambiguity rule is unchanged: a selector that matches zero DOM nodes, or more than one, fails the step instead of acting on whichever node matched first.
- **Backend mapping.** Driving the DOM requires a capability that idb does not provide: querying and acting on DOM nodes through `WKWebView.evaluateJavaScript`. The primary path runs that JavaScript through BajutsuKit in the app under test. It uses the same resident loopback channel as the XCUITest backend (BE-0019). `network.py` binds `127.0.0.1`, and the launch env injects that address into the app, which carries the requests in the Python→app direction. This keeps the web context **independent of BE-0019 landing first**. It works with idb as the actuator, which matters for headless CI, where a full XCUITest host is awkward. The same logic that the web (Playwright) backend already uses to turn a page into elements normalizes the DOM that the bridge returns. Selector resolution and the "ambiguous fails" rule carry over unchanged. The design fits the existing `Driver` / capability model. A new `webView` capability gates the web context, and a backend without it fails the step cleanly (the same contract as the multi-touch gestures today). A WebView-capable actuator that arrives later, for example through XCUITest, is picked up by the [stability-ladder](../../docs/drivers.md) `backend` list with the scenario and config unchanged.
- **Waits and assertions** carry over unchanged in form: condition waits with a mandatory timeout, and machine-checkable assertions. Each one runs against the DOM query inside the web context. There are no fixed sleeps, and the run/CI gate stays AI-free.

Config: which apps use WebViews, and any stable WebView host ids, live in `apps.<name>`. The tool, drivers, and runner stay app-agnostic.

### Context boundary grammar

A step enters the web context by naming the native WebView host element it scopes to:

```yaml
- web:
    within: { id: checkout.webview }   # the native WKWebView element in the a11y tree
    steps:
      - tap: { id: place-order }       # id resolves against the normalized DOM (the element's data-testid)
      - assert: { exists: { id: order-confirmation } }
```

The `within` here is the existing selector form (`bajutsu/scenario/models/selector.py`, `Selector.within`), reused at a new altitude. It resolves *natively* to the one `WKWebView` element, exactly as `resolve_unique` does today. That native resolution is also how the boundary is **detected**. The host is found by its native `id` in the a11y-tree `query()`, so entering the web context is an ordinary unique native match. An ambiguous or missing host fails the step before any DOM is touched, under the same determinism rule.

Inside the block, the selector grammar is unchanged in shape (`id` / `idMatches` / `label` / `traits` / `value` / `index`). Its `id` now resolves against the normalized DOM's `Element.identifier` instead of an `accessibilityIdentifier`. That identifier is the element's `data-testid`, the web backend's convention (`parse_dom` / `QUERY_JS`). The identifier is the same stable one, not a general CSS selector. The runner never parses CSS. It matches the normalized `Element` tree exactly as on iOS.

**Single active WebView per step (first slice).** A `web` block scopes to exactly one host. A single WebView's DOM is in view while the block's steps run. The boundary opens at the block and closes at its end. **Nested WebViews (a WebView whose DOM hosts another WebView) are out of the first slice.** The host selector must resolve to a single native WebView. A DOM that itself embeds further web contexts is left for a later iteration. This keeps the tree that each selector queries answerable by inspection, never by inference.

### The loopback JS bridge

Driving the DOM needs a capability that idb does not have: querying and acting on DOM nodes through `WKWebView.evaluateJavaScript`. The bridge mirrors the network collector's loopback pattern (`bajutsu/network.py`). Bajutsu binds a small receiver on `127.0.0.1:<port>` and injects the address into the app through launch env, alongside `BAJUTSU_COLLECTOR`. The in-app **BajutsuKit** side and the Python side then share the Mac's loopback interface.

To service a DOM query, bajutsu asks BajutsuKit, over the loopback channel, to run the page-walk JavaScript in the scoped `WKWebView` through `evaluateJavaScript`. BajutsuKit POSTs the resulting node list back as JSON, and the Python receiver hands it to the normalizer. Acting (a tap) follows the same round trip. Bajutsu resolves the unique node from the snapshot, then asks BajutsuKit to dispatch the click inside the WebView.

This bridge is **independent of BE-0019**. BajutsuKit runs in-process alongside the app under idb today, so the bridge works with idb as the actuator. That matters for headless CI, where standing up a full XCUITest host is awkward. An XCUITest path that reaches WebView content natively is an optional later complement. The stability ladder picks it up with the scenario and config unchanged. It is not a prerequisite.

### DOM → Element normalization

The bridge's JSON is normalized by the **same logic the web (Playwright) backend already uses** (`bajutsu/drivers/playwright.py`, `parse_dom` / `_to_element`). The mapping is as follows:

- `data-testid` (developer-set, non-localized) becomes `Element.identifier`, which the `id` selector targets.
- ARIA `role` (or the tag) becomes the normalized `traits`, through the role map.
- Accessible name, `aria-label`, and text become `label`.
- `disabled` / `aria-disabled` and `aria-selected` / `aria-checked` become the `notEnabled` / `selected` traits.

The page-walk JavaScript collects the same fields as Playwright's `QUERY_JS`: visible, interactive, or a11y-relevant nodes with their bounding rects. The normalized output is the same `Element` shape that the rest of the core consumes. The WebView's DOM becomes an ordinary `list[Element]` snapshot. `resolve_unique` / `find_all`, the "ambiguous fails" rule, assertions, and condition waits are **all unchanged** over that snapshot. The determinism core never learns that it is looking at a DOM instead of an a11y tree.

### First slice

The smallest valuable increment that the gate can test:

1. Scope one `web` block to a single native WebView host, resolved by its native `id`.
2. Query that WebView's DOM through the bridge and normalize it to `list[Element]`.
3. Resolve an `id` / CSS selector inside it with the existing `resolve_unique`.
4. Tap the resolved node inside the WebView.

The pure normalization (the bridge's JSON payload to `list[Element]`) is **unit-testable on a fake DOM payload**. It needs no Simulator and no JavaScript engine, exactly as `parse_dom` is tested today. The valuable core lands inside the deterministic gate. The bridge transport is exercised against a fake BajutsuKit endpoint, a loopback receiver that returns a canned payload. This keeps the on-device round trip out of the gate.

Out of the first slice: nested WebViews, typing into DOM fields, DOM-native condition waits beyond existence, and the a11y-bridge read path.

### Seams

The change adds three concrete touch-points, each small and named:

- **Driver query dispatch** (`bajutsu/drivers/base.py` `Driver.query` / the idb backend): when a step is inside a `web` block, the snapshot comes from the bridge's normalized DOM instead of the native a11y `query()`. The `Driver` Protocol shape is unchanged. The web context selects the source of the `list[Element]`.
- **Context scope in the selector resolver** (`bajutsu/scenario/models/actions.py` / `selector.py`): the `web: { within, steps }` step form. Its `within` resolves the native host, and its inner steps resolve against the DOM snapshot through the same `resolve_unique`.
- **BajutsuKit's JS-eval channel** (the Swift package, plus the Python loopback receiver modeled on `network.py`): the `evaluateJavaScript` round trip that runs the page walk and dispatches the click in the WKWebView.

### Prime-directive compliance

- **Deterministic.** An ambiguous or missing host, or an ambiguous or empty DOM match, fails the step (`resolve_unique` is reused verbatim). Waits stay condition waits with a mandatory timeout. No fixed sleeps are introduced.
- **No LLM.** The bridge, the normalizer, and resolution are pure machine logic. The run / CI gate stays AI-free, and nothing here adds an LLM to the verdict.
- **App-agnostic.** Per-app facts (which apps use WebViews, stable host ids) live in `apps.<name>`. The tool, drivers, normalizer, and runner are unchanged across apps and across the idb and later XCUITest actuators.

## Alternatives considered

- **Drive the WebView purely by screen coordinates.** Tapping pixel positions inside the WebView needs no DOM bridge. The approach is non-deterministic and non-portable, because it breaks on any layout, font, or scroll change. That is exactly why the coordinate forms are documented as a last resort. Rejected as the primary mechanism.
- **Rely on the WebView's own a11y bridge (the native tree that iOS already exposes for some web content).** When present, it needs no JavaScript. Coverage is partial and inconsistent across content, though, and the bridge gives accessible names instead of stable `id`s, so selectors would often be ambiguous. Kept as a possible read path for assertions, not as the addressing model.
- **Gate WebView support on the XCUITest backend (BE-0019) reaching the DOM natively.** XCUITest can address some WebView content through the native tree, so the web context could wait for that actuator. Rejected as the primary path. It would defer all WebView support until BE-0019 ships. It would also leave headless CI, where the XCUITest host is awkward, unable to drive WebViews at all. The BajutsuKit bridge runs in-process alongside idb today. An XCUITest path is a later complement that the stability ladder picks up, not a prerequisite.
- **Spin up a separate web-automation backend (for example, a Playwright actuator) and switch to it for web screens.** The platform map already reserves `web: (playwright,)`. A mid-flow handoff between a native session and a separate browser session is complex, though, and does not match a hybrid app, where the WebView is embedded in a live native screen. Rejected for the hybrid case in favor of reaching the embedded DOM in place. A standalone web backend remains a separate track.

## Progress

- [x] WebView hybrid support — the `web: { within, steps }` context, DOM→Element normalization (`bajutsu/dom.py`), `WebViewBridge` / `WebContextDriver`, the `BajutsuWebView.swift` endpoints, the pipeline wiring, in-context `type_text` / `scrollIntoView`, and `Capability.WEBVIEW` — shipped in [#400](https://github.com/bajutsu-e2e/bajutsu/pull/400) (first slice) and [#401](https://github.com/bajutsu-e2e/bajutsu/pull/401) (follow-up).
- Out of scope for this item (separate BEs as the need arises): a DOM condition wait on value/label, nested WebViews, and an a11y-bridge assertion read path.

## References

[drivers.md](../../docs/drivers.md)
