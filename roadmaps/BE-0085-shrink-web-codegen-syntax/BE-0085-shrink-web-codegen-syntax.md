**English** · [日本語](BE-0085-shrink-web-codegen-syntax-ja.md)

# BE-0085 — Shrink unsupported web (Playwright) codegen syntax

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0085](BE-0085-shrink-web-codegen-syntax.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0085") |
| Implementing PR | [#287](https://github.com/bajutsu-e2e/bajutsu/pull/287) |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

This item reduces the range of constructs that the **Playwright (web) codegen emitter** drops to a
bare `// TODO`. It is the web counterpart of
[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md), which does the
same for the XCUITest emitter.

## Motivation

`codegen` turns a passing scenario into a native test in the idiom of a destination framework. The
frameworks are XCUITest (Swift) and, since [BE-0062](../BE-0062-playwright-codegen/BE-0062-playwright-codegen.md),
Playwright (TypeScript). Both now sit behind one shared scenario walk
([BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md)).
Any construct that an emitter can't translate becomes a `// TODO` rather than a failure, so the
output always compiles and is reviewable. Every `// TODO` is still a line that a human ports by hand.

BE-0026 sharpened the XCUITest emitter's fallback handling. Compound selectors map structurally, and
the constructs with no faithful form emit a **labeled** `// TODO` naming the endpoint and the
reason. The Playwright emitter (`bajutsu/codegen_playwright.py`) lags on the same surface:

- The network `request` assertion and the `until: { request }` wait emit a bare `// TODO`
  (`"network 'request' assertion (no bajutsu runtime in the emitted test)"`). The `// TODO` **names
  neither the endpoint nor an accurate reason**. The text also gives up: Playwright **has**
  first-class network interception, so these constructs are not actually un-mappable on the web (see
  below).
- `requestSequence` and `responseSchema` assertions fall through to the generic
  `// TODO: unsupported assertion`, which names nothing.
- A selector that the locator builder can't render (`within`, an unrenderable glob, a trait
  combination) emits a bare `// TODO: unsupported selector`. A reviewer cannot act on it, because it
  gives no reason.

The XCUITest emitter could only label the network constructs, because the on-device runner has no
network-interception surface. Playwright can generate those constructs faithfully. That difference
makes this item more than a copy of BE-0026: the web emitter should *close* gaps that the iOS
emitter can only document.

## Detailed design

The governing rule is the same as in BE-0026. A construct leaves the fallback set only when a
**faithful, deterministic, AI-free** structural mapping exists. Otherwise the construct stays a
`// TODO`, but a labeled one that names what and why. The deterministic `run` / CI gate does not
change, because this item affects codegen output alone.

- **Network `request` / `until: { request }` → map faithfully.** Playwright observes network
  natively. A request matcher (`method` / `url` / `urlMatches` / `path` / `pathMatches` / `status`)
  becomes a predicate over a Playwright `Request` / `Response`, emitted as
  `await page.waitForResponse(r => …)` (or `waitForRequest` when only the request is asserted). The
  `until: { request }` wait is the same `waitForResponse` wrapped to the step's timeout. `status`
  checks `response.status()`, and `bodyMatches` checks `response.text()` / `request.postData()`. The
  mapping is a structural translation of the matcher, with no inference. So the mapping is in scope to
  *generate*, not just label.
- **`requestSequence` → ordered `waitForResponse`s.** Emit one awaited matcher per element, in order. The
  sequence assertion is about order, which mirrors the runtime check.
- **`responseSchema` → labeled `// TODO`.** Validating a response body against a JSON Schema needs a
  schema library in the emitted test, which is an external dependency that the generated file
  shouldn't assume. The construct stays a `// TODO`. Like BE-0026's network TODOs, it is a
  labeled one that names the endpoint and the schema file, not the bare "unsupported assertion."
- **Unsupported selectors → labeled `// TODO`.** When the locator builder returns None (`within`
  geometric containment, an unrenderable glob, an unsupported trait combination), emit a `// TODO`
  that names *which* selector has no Playwright locator and *why*, rather than a bare
  "unsupported selector". BE-0026 gave the XCUITest side the same honest-gap treatment.

Endpoint descriptions reuse `bajutsu.assertions.request_label`, the matcher description that the
runner, coverage, and the XCUITest emitter already share. A generated comment reads
identically across backends.

The work is incremental, with one construct per small PR, as in BE-0026. Each slice ships with golden
codegen tests on the Linux gate. Codegen is pure, so testing the emitted text needs no browser.

## Alternatives considered

- **Treat the web emitter like XCUITest and only *label* the network constructs.** Rejected: it
  throws away Playwright's native network interception. That capability lets the web emitter
  generate a real assertion where the iOS emitter cannot. Labeling is the fallback for the
  genuinely un-mappable (`responseSchema`, unrenderable selectors), not for `request` / `until`.
- **Fail generation on an unsupported construct instead of emitting `// TODO`.** Rejected for the
  same reason as in BE-0026: failing breaks codegen's promise that the output always compiles, and a
  single unmapped construct would block emitting the rest of a flow.
- **Auto-fill every gap with a best-effort guess** (for example, approximate `within` by the nearest
  renderable locator). Rejected: a guess violates determinism and would produce a test that passes
  for the wrong reason. BE-0026 records the same prime-directive concern.
- **Fold this into BE-0026 rather than a separate item.** BE-0026's Detailed design is XCUITest-
  specific (NSPredicate / `simctl`), and the web mapping is genuinely different, because it
  *generates* the network constructs that Playwright supports. A sibling item under the same
  `codegen coverage` topic keeps each emitter's design legible while sharing the governing rule.

## Progress

- [x] Exchange recorder before navigation — `page.on('requestfinished', …)` pushes `{ method, url, status, body }` into an `exchanges` array (the same event the runtime web collector hooks), and the generated assertions read that list.
- [x] Assertion mappings — `request` → a point-in-time check (`toBe(count)` or `some(...)`); `until: { request }` → an `expect.poll` lower bound; `requestSequence` → an in-order forward scan; `responseSchema` → a labeled `// TODO`.
- [x] Recorded-exchange predicate — `path` / `pathMatches` over `new URL(e.url).pathname`, regex via `RegExp.test`, `status` over `e.status`, with `bodyMatches` guarding `e.body !== null`.

## References

- [`bajutsu/codegen_playwright.py`](../../bajutsu/codegen_playwright.py) : the web emitter and its
  current `// TODO` sites. `bajutsu/assertions.py` holds `request_label`, the shared matcher
  description.
- [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)
  (the XCUITest counterpart and the governing rule),
  [BE-0062](../BE-0062-playwright-codegen/BE-0062-playwright-codegen.md) (the Playwright
  target), [BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md)
  (the shared emitter walk).
- [docs/codegen.md](../../docs/codegen.md).
