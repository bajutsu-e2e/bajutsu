**English** · [日本語](BE-0085-shrink-web-codegen-syntax-ja.md)

# BE-0085 — Shrink unsupported web (Playwright) codegen syntax

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0085](BE-0085-shrink-web-codegen-syntax.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Implementing PR | [#287](https://github.com/bajutsu-e2e/bajutsu/pull/287) |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

Reduce the range of constructs the **Playwright (web) codegen emitter** drops to a bare `// TODO` —
the web counterpart of [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md),
which does this for the XCUITest emitter.

## Motivation

`codegen` turns a passing scenario into a native test in a destination framework's idiom — XCUITest
(Swift) and, since [BE-0062](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md),
Playwright (TypeScript), now behind one shared scenario walk
([BE-0083](../../implemented/BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md)).
Any construct an emitter can't translate becomes a `// TODO` rather than failing, so the output
always compiles and is reviewable — but every `// TODO` is a line a human ports by hand.

The XCUITest emitter's fallback handling has been sharpened (BE-0026): compound selectors map
structurally, and the constructs with no faithful form emit a **labeled** `// TODO` naming the
endpoint and the reason. The Playwright emitter (`bajutsu/codegen_playwright.py`) lags on the same
surface:

- The network `request` assertion and the `until: { request }` wait emit a bare `// TODO`
  (`"network 'request' assertion (no bajutsu runtime in the emitted test)"`) that **names neither the
  endpoint nor an accurate reason** — and, crucially, is *defeatist*: Playwright **has** first-class
  network interception, so these are not actually un-mappable on the web (see below).
- `requestSequence` and `responseSchema` assertions fall through to the generic
  `// TODO: unsupported assertion`, naming nothing.
- A selector the locator builder can't render (`within`, an unrenderable glob, a trait combination)
  emits a bare `// TODO: unsupported selector` with no reason a reviewer can act on.

The crux that makes this more than a copy of BE-0026: **the XCUITest emitter could only label the
network constructs (the on-device runner has no network-interception surface), but Playwright can
generate them faithfully.** So the web emitter should *close* gaps the iOS one can only document.

## Detailed design

The same governing rule as BE-0026: a construct leaves the fallback set only when a **faithful,
deterministic, AI-free** structural mapping exists; otherwise it stays a `// TODO`, but a labeled one
naming what and why. No change to the deterministic `run` / CI gate — this is codegen output only.

- **Network `request` / `until: { request }` → map faithfully.** Playwright observes network
  natively. A request matcher (`method` / `url` / `urlMatches` / `path` / `pathMatches` / `status`)
  becomes a predicate over a Playwright `Request` / `Response`, emitted as
  `await page.waitForResponse(r => …)` (or `waitForRequest` when only the request is asserted). The
  `until: { request }` wait is the same `waitForResponse` wrapped to the step's timeout. `status`
  checks `response.status()`; `bodyMatches` checks `response.text()` / `request.postData()`. This is
  a structural translation of the matcher — no inference — so it is in-scope to *generate*, not just
  label.
- **`requestSequence` → ordered `waitForResponse`s.** Emit one awaited matcher per element, in order
  (the sequence assertion is about order, mirroring the runtime check).
- **`responseSchema` → labeled `// TODO`.** Validating a response body against a JSON Schema needs a
  schema library in the emitted test (an external dependency the generated file shouldn't assume), so
  this stays a `// TODO` — but a labeled one naming the endpoint and the schema file, like BE-0026's
  network TODOs, not the bare "unsupported assertion."
- **Unsupported selectors → labeled `// TODO`.** When the locator builder returns None (`within`
  geometric containment, an unrenderable glob, an unsupported trait combination), emit a `// TODO`
  naming *which* selector and *why* it has no Playwright locator, rather than a bare
  "unsupported selector" — the same honest-gap treatment BE-0026 gave the XCUITest side.

Endpoint descriptions reuse `bajutsu.assertions.request_label` (the matcher description the runner,
coverage, and the XCUITest emitter already share), so a generated comment reads identically across
backends.

The work is incremental — one construct per small PR, like BE-0026 — and each slice ships with golden
codegen tests on the Linux gate (codegen is pure; no browser needed to test the emitted text).

## Alternatives considered

- **Treat the web emitter like XCUITest and only *label* the network constructs.** Rejected: it
  throws away Playwright's native network interception, which is exactly the capability that lets the
  web emitter generate a real assertion where the iOS one cannot. Labeling is the fallback for the
  genuinely un-mappable (`responseSchema`, unrenderable selectors), not for `request` / `until`.
- **Fail generation on an unsupported construct instead of emitting `// TODO`.** Rejected for the
  same reason as in BE-0026: it breaks codegen's promise that the output always compiles and a single
  unmapped construct would block emitting the rest of a flow.
- **Auto-fill every gap with a best-effort guess** (e.g. approximate `within` by the nearest
  renderable locator). Rejected: it violates determinism and would produce a test that passes for the
  wrong reason — the same prime-directive concern BE-0026 records.
- **Fold this into BE-0026 rather than a separate item.** BE-0026's Detailed design is XCUITest-
  specific (NSPredicate / `simctl`); the web mapping is genuinely different (it *generates* the
  network constructs Playwright supports). A sibling item under the same `codegen coverage` topic
  keeps each emitter's design legible while sharing the governing rule.

## Progress

- [x] Exchange recorder before navigation — `page.on('requestfinished', …)` pushes `{ method, url, status, body }` into an `exchanges` array (the same event the runtime web collector hooks), and the generated assertions read that list.
- [x] Assertion mappings — `request` → a point-in-time check (`toBe(count)` or `some(...)`); `until: { request }` → an `expect.poll` lower bound; `requestSequence` → an in-order forward scan; `responseSchema` → a labeled `// TODO`.
- [x] Recorded-exchange predicate — `path` / `pathMatches` over `new URL(e.url).pathname`, regex via `RegExp.test`, `status` over `e.status`, with `bodyMatches` guarding `e.body !== null`.

## References

- [`bajutsu/codegen_playwright.py`](../../../bajutsu/codegen_playwright.py) — the web emitter and its
  current `// TODO` sites; `bajutsu/assertions.py` `request_label` (the shared matcher description).
- [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)
  (the XCUITest counterpart and the governing rule),
  [BE-0062](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md) (the Playwright
  target), [BE-0083](../../implemented/BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md)
  (the shared emitter walk).
- [docs/codegen.md](../../../docs/codegen.md).
