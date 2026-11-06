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
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

This item adds a driver for the Web (browser) platform, built on **Playwright (Python)**. The driver runs headless and cross-browser. It selects elements by `getByTestId` and `getByRole`, and it **clicks semantically**, with no coordinates.

Adding the driver adds a new triple of [actuator](../../docs/glossary.md#driver-backend-actuator-platform), environment manager, and id convention. The deterministic core stays byte-for-byte the same.

This is the **highest-priority platform item**. Web is the recommended first platform for three reasons:

- It runs on Linux with no Mac and no emulator.
- It fits the existing `make check` and CI gate.
- It exercises the rich end of the capability model.

Together these make Web the lowest-cost proof that the core is platform-neutral.

## Motivation

Web is the **lowest-cost place to prove the abstraction is platform-neutral**. Two reasons apply to Web at once, and no other platform shares both.

1. **It needs no macOS and no device emulator**.
   A `BrowserContext` is the whole "device", so the backend runs on Linux.
   It fits *inside* the current `make check` and CI gate ([ci](../../docs/ci.md)) from day one.
   The first real second platform thus avoids the largest source of friction.
   That friction is provisioning a Mac or an emulator.
2. **It exercises the rich end of the capability model**.
   Playwright provides `semanticTap`, native `conditionWait` (auto-waiting), and emulated `multiTouch`.
   It also provides native `network`.
   The native `network` capability covers request stubbing **and** observation in one API.
   Building against Playwright raises the ceiling of `capabilities()`.
   It also proves that the unmodified capability model spans the whole range.
   The range runs from the lean coordinate backends to the rich semantic end.

These two reasons make Web the **recommended Phase 1** platform.
Web is also the highest-priority item on the multi-platform track.
Web has the lowest friction and the broadest reach.
Web is also the cheapest proof that the core is genuinely platform-neutral.

## Detailed design

### The seam table

| Seam | Choice |
|---|---|
| **Actuator** | **Playwright (Python)** — `playwright` is a Python package, headless, cross-browser. Selects by `getByTestId` / `getByRole`; **clicks semantically** (no coordinates) |
| **Environment** | A **`BrowserContext`**, not a device. Clean state = a fresh incognito `browser.new_context()` (the `erase` equivalent, but ~free). "launch" = `page.goto(url)`; "deeplink" = a URL; launch env = query params / seeded `localStorage` / cookies |
| **id convention** | `data-testid` (non-localized, developer-set). ARIA `role` → `traits`; accessible name → `label` |
| **Evidence providers** | screenshot = `page.screenshot`; video = context video recording; **`network` = native route interception** (first backend with it); `deviceLog` ≈ console logs / page errors |
| **codegen target** | Playwright test (TypeScript) or `pytest-playwright` |

Playwright's capabilities lead the capability gradient.
It provides `semanticTap`, native `conditionWait` (auto-waiting), and emulated `multiTouch`.
It also provides `network`, with request stubbing **and** observation in one API.
These capabilities raise the ceiling of the capability model.
They make Web the lowest-cost place to prove the abstraction.

### The selector mapping

The YAML selector (`{ id: settings.reindex }`) is already platform-neutral. What differs is merely the app-side attribute that the backend reads to match the selector. That attribute lives entirely inside the new Driver. On Web, the `Selector` fields map as follows.

| `Selector` field | iOS | Web |
|---|---|---|
| `id` (primary) | `accessibilityIdentifier` | `data-testid` |
| `label` (auxiliary) | `accessibilityLabel` | accessible name / `aria-label` / text |
| `traits` (role filter) | UI traits (`button`, `link`, …) | ARIA (Accessible Rich Internet Applications) `role` (`button`, `link`, `textbox`) |
| `value` | accessibility value | input `value` / `aria-*` |

### Where it sits in the capability matrix

Web **leads the gradient** as the first backend to provide four capabilities.
The four are `semanticTap`, native `conditionWait` (auto-wait), native `network`, and emulated `multiTouch`.

| Capability | idb (iOS) | adb (Android) | Playwright (Web) | fake |
|---|:--:|:--:|:--:|:--:|
| `query` / `elements` / `screenshot` | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | — | — | ✅ | ✅ |
| `conditionWait` (native) | — | — | ✅ | ✅ |
| `network` (native) | — | — | ✅ | — |
| `multiTouch` | — | — | ✅ (emulated) | ✅ |

idb and Android sit at the lean end, with coordinate actuation and mocked network. Playwright sits at the rich end, with semantic actions and native network. The unmodified capability model spans both extremes, which is evidence that the abstraction holds.

### Phasing: the recommended Phase 1

Web is the **recommended Phase 1** platform: the first real second platform, taken up before Android.

The rationale is decisive.
Web alone needs **no macOS and no device emulator**.
Web thus fits the *existing* Linux `make check` and CI gate ([ci](../../docs/ci.md)) from day one.
Native network, video, and semantic actions exercise the **rich** end of `capabilities()`.
Web offers the lowest friction and the broadest reach.
Web is also the cheapest proof that the core is platform-neutral.

Android ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) follows in Phase 2.
It confirms the lean coordinate path on top of an already-generalized core.

**Why Web comes before Android**.
Android is the closer architectural twin of idb.
Even so, Web alone fits inside the current Linux gate without a Mac or an emulator.
Starting with Web reduces the risk in judging whether the core is truly platform-neutral.
It does so at the lowest possible cost.
Android then confirms the lean coordinate path on an already-generalized core.

## Alternatives considered

- **A coordinate-based browser actuator (Selenium-style screenshot taps).** Rejected.
  Playwright already offers semantic clicks, native auto-waiting, and native network interception.
  These fit the capability model and the determinism principles directly.
  A coordinate path would discard the capabilities making Web the rich-end proof of the abstraction.
- **Building Android first (the closer idb twin).** Rejected for phasing.
  Android needs an emulator (and KVM on CI).
  Web needs neither a Mac nor an emulator, so it fits the current Linux gate at the lowest cost. See [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md).

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN](../../DESIGN.md), `bajutsu/drivers/`, `bajutsu/backends.py`,
[drivers.md](../../docs/drivers.md), [ci.md](../../docs/ci.md), [concepts.md](../../docs/concepts.md),
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0008 — Flutter support](../BE-0008-flutter-support/BE-0008-flutter-support.md)
