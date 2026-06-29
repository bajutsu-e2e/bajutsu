**English** · [日本語](BE-0037-webview-hybrid-support-ja.md)

# BE-0037 — WebView / hybrid support

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0037](BE-0037-webview-hybrid-support.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | (pending) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

The current implementation assumes a native a11y tree. This proposal adds support for accessing the DOM (Document Object Model) inside a WebView.

## Motivation

Many production iOS apps are hybrid: a native shell hosting a `WKWebView` for whole screens
(checkout, help, an embedded web app, an OAuth consent page). Bajutsu's selectors resolve
against the native accessibility (a11y) tree, but a WebView collapses its entire web content
into a small, opaque region of that tree — the HTML buttons, fields, and links inside it are
not addressable as native elements. The result is that a flow which enters a WebView simply
can't be driven or asserted: there is no stable `id` to tap, no element to wait for, and an
ambiguous-or-empty match (correctly) fails the step. Any app with a web-backed step is
effectively un-automatable past the WebView boundary. Reaching the DOM (Document Object Model)
inside the WebView is what makes those flows testable.

## Detailed design

The core challenge is that the DOM and the native a11y tree are two different trees with two
different addressing schemes, and a selector must resolve unambiguously in whichever one it
targets. The proposal keeps the existing selector grammar for native elements and adds an
explicit *web context* for the WebView's DOM, rather than silently blending the two:

- **Context boundary.** A step opts into the web context by scoping to the host WebView, e.g.
  `web: { within: { id: checkout.webview } }`, after which selectors inside that block address
  the DOM. Outside it, selectors resolve natively exactly as today. Making the boundary explicit
  keeps each selector unambiguous about which tree it queries — no guessing.
- **DOM selectors.** Inside the web context, a selector addresses DOM nodes by CSS selector
  (and, where stable, by the element's accessible name), the web analogue of `id`. The
  ambiguity rule is unchanged: a selector that matches zero or more-than-one DOM node fails the
  step rather than acting on whichever matched first.
- **Backend mapping.** Driving the DOM requires a capability idb does not provide: querying and
  acting on DOM nodes via `WKWebView.evaluateJavaScript`. The primary path runs that JavaScript
  through BajutsuKit in the app under test, over the same resident loopback channel the XCUITest
  backend (BE-0019) uses — `network.py` binds `127.0.0.1` and the launch env injects its address
  into the app, carrying the requests in the Python→app direction. This keeps the web context
  **independent of BE-0019 landing first**, so it works with idb as the actuator (which matters
  for headless CI, where a full XCUITest host is awkward). The DOM the bridge returns is
  normalized by the same logic the web (Playwright) backend already uses to turn a page into
  elements, so selector resolution and the "ambiguous fails" rule carry over unchanged. This fits
  the existing `Driver` / capability model: a new `webView` capability gates the web context, and a
  backend lacking it fails the step cleanly (the same contract as the multi-touch gestures today).
  A WebView-capable actuator that arrives later (e.g. via XCUITest) is picked up by the
  [stability-ladder](../../../docs/drivers.md) `backend` list with the scenario and config unchanged.
- **Waits and assertions** carry over unchanged in form (condition waits with a mandatory
  timeout, machine-checkable assertions), evaluated against the DOM query inside the web
  context. No fixed sleeps; the run/CI gate stays AI-free.

Config: which apps use WebViews, and any stable WebView host ids, live in `apps.<name>`; the
tool, drivers, and runner stay app-agnostic.

### Context boundary grammar

A step enters the web context by naming the native WebView host element it scopes to:

```yaml
- web:
    within: { id: checkout.webview }   # the native WKWebView element in the a11y tree
    steps:
      - tap: { id: place-order }       # id resolves against the normalized DOM (the element's data-testid)
      - assert: { exists: { id: order-confirmation } }
```

The `within` here is the existing selector form (`bajutsu/scenario/models/selector.py`,
`Selector.within`) reused at a new altitude: it resolves *natively* to the one `WKWebView`
element, exactly as `resolve_unique` does today. That native resolution is also how the
boundary is **detected** — the host is found by its native `id` in the a11y-tree `query()`, so
entering the web context is an ordinary unique native match (an ambiguous or missing host fails
the step before any DOM is touched, the same determinism rule). Inside the block, the selector
grammar is unchanged in shape (`id` / `idMatches` / `label` / `traits` / `value` / `index`) but
its `id` now resolves against the normalized DOM's `Element.identifier` — the element's
`data-testid`, the web backend's convention (`parse_dom` / `QUERY_JS`) — rather than an
`accessibilityIdentifier`. It is the same stable identifier, not a general CSS selector: the
runner never parses CSS, it matches the normalized `Element` tree exactly as on iOS.

**Single active WebView per step (first slice).** A `web` block scopes to exactly one host, so
exactly one WebView's DOM is in view while its steps run; the boundary opens at the block and
closes at its end. **Nested WebViews (a WebView whose DOM hosts another WebView) are out of the
first slice**: the host selector must resolve to a single native WebView, and a DOM that itself
embeds further web contexts is left for a later iteration. This keeps "which tree does this
selector query" answerable by inspection — never inferred.

### The loopback JS bridge

Driving the DOM needs a capability idb does not have: querying and acting on DOM nodes via
`WKWebView.evaluateJavaScript`. The bridge mirrors the network collector's loopback pattern
(`bajutsu/network.py`): bajutsu binds a small receiver on `127.0.0.1:<port>` and injects the
address into the app via launch env (alongside `BAJUTSU_COLLECTOR`), so the in-app **BajutsuKit**
side and the Python side share the Mac's loopback interface. To service a DOM query, bajutsu
asks BajutsuKit (over the loopback channel) to run the page-walk JavaScript in the scoped
`WKWebView` via `evaluateJavaScript`; BajutsuKit POSTs the resulting node list back as JSON,
which the Python receiver hands to the normalizer. Acting (a tap) follows the same round trip:
bajutsu resolves the unique node from the snapshot, then asks BajutsuKit to dispatch the click
inside the WebView.

This is **independent of BE-0019**: BajutsuKit runs in-process alongside the app under idb today,
so the bridge works with idb as the actuator. That matters for headless CI, where standing up a
full XCUITest host is awkward. An XCUITest path that reaches WebView content natively is an
optional later complement the stability ladder picks up with the scenario and config unchanged,
not a prerequisite.

### DOM → Element normalization

The bridge's JSON is normalized by the **same logic the web (Playwright) backend already uses**
(`bajutsu/drivers/playwright.py`, `parse_dom` / `_to_element`): `data-testid` (developer-set,
non-localized) → `Element.identifier` (the `id` selector targets); ARIA `role` (or the tag) →
normalized `traits` via the role map; accessible name / `aria-label` / text → `label`;
`disabled` / `aria-disabled` and `aria-selected` / `aria-checked` → the `notEnabled` / `selected`
traits. The page-walk JavaScript collects the same fields Playwright's `QUERY_JS` does (visible,
interactive / a11y-relevant nodes with their bounding rects), so the normalized output is the
same `Element` shape the rest of the core consumes. Because the WebView's DOM thus becomes an
ordinary `list[Element]` snapshot, `resolve_unique` / `find_all`, the "ambiguous fails" rule,
assertions, and condition waits are **all unchanged** over it — the determinism core never learns
it is looking at a DOM rather than an a11y tree.

### First slice

The smallest valuable, gate-testable increment:

1. Scope one `web` block to a single native WebView host (resolved by its native `id`).
2. Query that WebView's DOM through the bridge and normalize it to `list[Element]`.
3. Resolve an `id` / CSS selector inside it with the existing `resolve_unique`.
4. Tap the resolved node inside the WebView.

The pure normalization (the bridge's JSON payload → `list[Element]`) is **unit-testable on a
fake DOM payload**, with no Simulator and no JavaScript engine — exactly as `parse_dom` is tested
today — so the valuable core lands inside the deterministic gate. The bridge transport is
exercised against a fake BajutsuKit endpoint (a loopback receiver returning a canned payload),
keeping the on-device round trip out of the gate. Out of the first slice: nested WebViews, typing
into DOM fields, DOM-native condition waits beyond existence, and the a11y-bridge read path.

### Seams

The concrete touch-points the change adds, each small and named:

- **Driver query dispatch** (`bajutsu/drivers/base.py` `Driver.query` / the idb backend): when a
  step is inside a `web` block, the snapshot comes from the bridge's normalized DOM instead of the
  native a11y `query()`. The `Driver` Protocol shape is unchanged; the web context selects the
  source of the `list[Element]`.
- **Context scope in the selector resolver** (`bajutsu/scenario/models/actions.py` /
  `selector.py`): the `web: { within, steps }` step form, whose `within` resolves the native host
  and whose inner steps resolve against the DOM snapshot through the same `resolve_unique`.
- **BajutsuKit's JS-eval channel** (the Swift package, plus the Python loopback receiver modeled
  on `network.py`): the `evaluateJavaScript` round trip that runs the page walk and dispatches the
  click in the WKWebView.

### Prime-directive compliance

- **Deterministic.** An ambiguous or missing host, or an ambiguous / empty DOM match, fails the
  step (`resolve_unique` is reused verbatim). Waits stay condition waits with a mandatory timeout;
  no fixed sleeps are introduced.
- **No LLM.** The bridge, the normalizer, and resolution are pure machine logic — the run / CI
  gate stays AI-free, and nothing here adds an LLM to the verdict.
- **App-agnostic.** Per-app facts (which apps use WebViews, stable host ids) live in
  `apps.<name>`; the tool, drivers, normalizer, and runner are unchanged across apps and across
  the idb / later XCUITest actuators.

## Alternatives considered

- **Drive the WebView purely by screen coordinates.** Tapping pixel positions inside the
  WebView needs no DOM bridge, but it is non-deterministic and non-portable (it breaks on any
  layout, font, or scroll change) — exactly what the coordinate forms are documented to be a
  last resort for. Rejected as the primary mechanism.
- **Rely on the WebView's own a11y bridge (the native tree iOS already exposes for some web
  content).** When present it needs no JavaScript, but coverage is partial and inconsistent
  across content, and it gives accessible names rather than stable `id`s, so selectors would
  often be ambiguous. Kept as a possible read path for assertions, not the addressing model.
- **Gate WebView support on the XCUITest backend (BE-0019) reaching the DOM natively.** XCUITest
  can address some WebView content through the native tree, so the web context could wait for that
  actuator. Rejected as the primary path: it would defer all WebView support until BE-0019 ships
  and would leave headless CI (where the XCUITest host is awkward) unable to drive WebViews at all.
  The BajutsuKit bridge runs in-process alongside idb today; an XCUITest path is a later complement
  the stability ladder picks up, not a prerequisite.
- **Spin up a separate web-automation backend (e.g. a Playwright actuator) and switch to it for
  web screens.** The platform map already reserves `web: (playwright,)`, but mid-flow handoff
  between a native session and a separate browser session is complex and doesn't match a hybrid
  app, where the WebView is embedded in a live native screen. Rejected for the hybrid case in
  favor of reaching the embedded DOM in place; a standalone web backend remains a separate
  track.

## References

[drivers.md](../../../docs/drivers.md)
