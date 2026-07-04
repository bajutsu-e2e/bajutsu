**English** · [日本語](BE-0041-web-playwright-backend-ja.md)

# BE-0041 — Web (Playwright) backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0041](BE-0041-web-playwright-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0041") |
| Implementing PR | [#158](https://github.com/bajutsu-e2e/bajutsu/pull/158) |
| Topic | Platform expansion (landed slices) |
<!-- /BE-METADATA -->

## Introduction

A driver for the Web (browser) platform built on **Playwright (Python)**: headless, cross-browser,
selecting by `getByTestId` / `getByRole` and **clicking semantically** (no coordinates). Adding it
means adding a new triple — actuator + environment manager + id convention — while the
deterministic core stays byte-for-byte the same. This is the **highest-priority platform item**:
Web is the recommended first platform because it runs on Linux with no Mac and no emulator, fitting
the existing `make check` / CI gate, and it exercises the rich end of the capability model — the
lowest-cost proof that the core is platform-neutral.

## Motivation

Web is the **lowest-cost place to prove the abstraction is platform-neutral**, for two reasons that
no other platform shares at once:

1. **It needs no macOS and no device emulator.** A `BrowserContext` is the whole "device", so the
   backend runs on Linux and fits *inside* the current `make check` / CI gate ([ci](../../../docs/ci.md))
   from day one. That removes the largest source of friction — provisioning a Mac or an emulator —
   from the first real second platform.
2. **It exercises the rich end of the capability model.** Playwright provides `semanticTap`, native
   `conditionWait` (auto-waiting), native `network` (request stubbing **and** observation in one
   API), and emulated `multiTouch`. Building against it raises the ceiling of `capabilities()` and
   proves the unmodified capability model spans from the lean coordinate backends all the way to the
   rich semantic end.

Together these make Web the **recommended Phase 1** platform and the highest-priority item on the
platform-expansion track: lowest friction, broadest reach, cheapest proof that the core is genuinely
platform-neutral.

## Detailed design

### The seam table

| Seam | Choice |
|---|---|
| **Actuator** | **Playwright (Python)** — `playwright` is a Python package, headless, cross-browser. Selects by `getByTestId` / `getByRole`; **clicks semantically** (no coordinates) |
| **Environment** | A **`BrowserContext`**, not a device. Clean state = a fresh incognito `browser.new_context()` (the `erase` equivalent, but ~free). "launch" = `page.goto(url)`; "deeplink" = a URL; launch env = query params / seeded `localStorage` / cookies |
| **id convention** | `data-testid` (non-localized, developer-set). ARIA `role` → `traits`; accessible name → `label` |
| **Evidence providers** | screenshot = `page.screenshot`; video = context video recording; **`network` = native route interception** (first backend with it); `deviceLog` ≈ console logs / page errors |
| **codegen target** | Playwright test (TypeScript) or `pytest-playwright` |

Playwright's capabilities lead the capability gradient: it provides `semanticTap`, native
`conditionWait` (auto-waiting), `network` (request stubbing **and** observation in one API), and
emulated `multiTouch`. This raises the ceiling of the capability model and makes Web the lowest-cost
place to prove the abstraction.

### The selector mapping

The YAML selector (`{ id: settings.reindex }`) is already platform-neutral; only *which app-side
attribute the backend reads to satisfy it* differs, and that lives entirely inside the new Driver.
On Web the `Selector` fields map as:

| `Selector` field | iOS | Web |
|---|---|---|
| `id` (primary) | `accessibilityIdentifier` | `data-testid` |
| `label` (auxiliary) | `accessibilityLabel` | accessible name / `aria-label` / text |
| `traits` (role filter) | UI traits (`button`, `link`, …) | ARIA (Accessible Rich Internet Applications) `role` (`button`, `link`, `textbox`) |
| `value` | accessibility value | input `value` / `aria-*` |

### Where it sits in the capability matrix

Web **leads the gradient**: it is the first backend to provide `semanticTap`, native
`conditionWait` (auto-wait), native `network`, and emulated `multiTouch`.

| Capability | idb (iOS) | adb (Android) | Playwright (Web) | fake |
|---|:--:|:--:|:--:|:--:|
| `query` / `elements` / `screenshot` | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | — | — | ✅ | ✅ |
| `conditionWait` (native) | — | — | ✅ | ✅ |
| `network` (native) | — | — | ✅ | — |
| `multiTouch` | — | — | ✅ (emulated) | ✅ |

idb and Android sit at the lean end (coordinate actuation, mocked network); Playwright at the rich
end (semantic, native network). That an unmodified capability model spans both extremes is evidence
the abstraction holds.

### Phasing — recommended Phase 1

Web is the **recommended Phase 1** platform — the first real second platform, taken up before
Android. The rationale is decisive: Web is the only platform that needs **no macOS and no device
emulator**, so it fits the *existing* Linux `make check` / CI gate ([ci](../../../docs/ci.md)) from day one.
Native network + video + semantic actions exercise the **rich** end of `capabilities()`. It is the
lowest friction, broadest reach, and cheapest proof that the core is platform-neutral. Android
([BE-0007](../in-progress/BE-0007-android-backend/BE-0007-android-backend.md)) follows in Phase 2, confirming
the lean / coordinate path on top of an already-generalized core.

**Why Web before Android**, even though Android is the closer architectural twin of idb: Web is the
only platform that fits inside the current Linux gate without a Mac or an emulator, so it reduces
the risk of the question "is the core truly platform-neutral?" at the lowest possible cost. Android
then confirms the lean/coordinate path on an already-generalized core.

## Alternatives considered

- **A coordinate-based browser actuator (Selenium-style screenshot taps).** Rejected: Playwright
  already offers semantic clicks, native auto-waiting, and native network interception, which fit the
  capability model and the determinism principles directly. A coordinate path would throw away the
  exact capabilities that make Web the rich-end proof of the abstraction.
- **Building Android first (the closer idb twin).** Rejected for phasing: Android needs an emulator
  (and KVM on CI), whereas Web needs neither a Mac nor an emulator and so fits the current Linux gate
  at the lowest cost. See [BE-0007](../in-progress/BE-0007-android-backend/BE-0007-android-backend.md).

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN](../../../DESIGN.md), `bajutsu/drivers/`, `bajutsu/backends.py`,
[drivers.md](../../../docs/drivers.md), [ci.md](../../../docs/ci.md), [concepts.md](../../../docs/concepts.md),
[BE-0007 — Android backend](../in-progress/BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0008 — Flutter support](../proposals/BE-0008-flutter-support/BE-0008-flutter-support.md)
