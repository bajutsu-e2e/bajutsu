**English** · [日本語](BE-0037-webview-hybrid-support-ja.md)

# BE-0037 — WebView / hybrid support

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0037](BE-0037-webview-hybrid-support.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
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
- **Backend mapping.** Driving the DOM requires a capability idb does not provide. It maps to
  `WKWebView.evaluateJavaScript` to query and act on DOM nodes — surfaced through BajutsuKit
  in the app under test, or through a WebView-capable actuator. This fits the existing
  `Driver` / capability model: a new `webView` capability gates the web context, and a backend
  lacking it fails the step cleanly (the same contract as the multi-touch gestures today). The
  scenario and config are unchanged when a richer actuator lands — the
  [stability-ladder](../../../docs/drivers.md) `backend` list picks it up.
- **Waits and assertions** carry over unchanged in form (condition waits with a mandatory
  timeout, machine-checkable assertions), evaluated against the DOM query inside the web
  context. No fixed sleeps; the run/CI gate stays AI-free.

Config: which apps use WebViews, and any stable WebView host ids, live in `apps.<name>`; the
tool, drivers, and runner stay app-agnostic.

## Alternatives considered

- **Drive the WebView purely by screen coordinates.** Tapping pixel positions inside the
  WebView needs no DOM bridge, but it is non-deterministic and non-portable (it breaks on any
  layout, font, or scroll change) — exactly what the coordinate forms are documented to be a
  last resort for. Rejected as the primary mechanism.
- **Rely on the WebView's own a11y bridge (the native tree iOS already exposes for some web
  content).** When present it needs no JavaScript, but coverage is partial and inconsistent
  across content, and it gives accessible names rather than stable `id`s, so selectors would
  often be ambiguous. Kept as a possible read path for assertions, not the addressing model.
- **Spin up a separate web-automation backend (e.g. a Playwright actuator) and switch to it for
  web screens.** The platform map already reserves `web: (playwright,)`, but mid-flow handoff
  between a native session and a separate browser session is complex and doesn't match a hybrid
  app, where the WebView is embedded in a live native screen. Rejected for the hybrid case in
  favor of reaching the embedded DOM in place; a standalone web backend remains a separate
  track.

## References

[drivers.md](../../../docs/drivers.md)
