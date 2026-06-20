**English** · [日本語](BE-0007-android-backend-ja.md)

# BE-0007 — Android backend

* Proposal: [BE-0007](BE-0007-android-backend.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Platform expansion (Android / Web / Flutter)

## Introduction

A driver for the Android emulator, driving the UI via `adb` + UI Automator and mapping
`resource-id` / `content-desc` selectors id-first. Architecturally it is the twin of the existing
iOS `idb` backend: subprocess-driven, coordinate-based actuation, no semantic tap. Adding it means
adding a new triple — actuator + environment manager + id convention — while the deterministic
core stays byte-for-byte the same.

## Motivation

Android is the **architectural twin of idb**: subprocess-driven, coordinate actuation, and a
transiently-empty tree during transitions — so it reuses idb's *resolve-with-retry,
fail-ambiguity-fast* pattern (see [drivers](../../../docs/drivers.md)) almost unchanged. Building it
validates that the iOS-specific parts were really isolated to the three seams (actuator,
environment manager, stable-id convention), with almost no new shape introduced into the
"unchanged" core. It also extends the product's reach to the second-most-common mobile target
without touching the scenario DSL, the selector model, machine assertions, the orchestrator loop,
or the reporter.

## Detailed design

### The seam table

| Seam | Choice |
|---|---|
| **Actuator** | **`adb` + `uiautomator dump`** — `uiautomator dump` yields an XML tree; actuation is `adb shell input tap x y` at the element's bounds center. **Coordinate-based, no semantic tap — a near-exact twin of idb.** (A richer Appium UiAutomator2 path could add semantic actions later) |
| **Environment** | `adb`: clean state = `pm clear <package>` (the `erase` equivalent); boot via emulator/AVD (Android Virtual Device); launch = `am start`; deeplink = `am start -a android.intent.action.VIEW -d <url>`; launch args = intent extras |
| **id convention** | `resource-id` (XML `android:id`; Jetpack Compose `Modifier.testTag` surfaced as `resource-id` via `testTagsAsResourceId`). `content-desc`/`text` → `label`; widget class → `traits` |
| **Evidence providers** | screenshot = `adb exec-out screencap`; video = `adb shell screenrecord`; `deviceLog` = `adb logcat` (filtered by tag/pid); `network` = no native monitor → same mock story as iOS |
| **codegen target** | Espresso or UI Automator (Kotlin/Java) |

### The architectural twin of idb

Android is the **architectural twin of idb**: subprocess-driven, coordinate actuation, and a
transiently-empty tree during screen transitions. Because of that shared shape it reuses idb's
*resolve-with-retry, fail-ambiguity-fast* pattern — poll the tree, retry on a transiently-empty
result, and fail immediately if a selector resolves to more than one element rather than "tapping
whatever matched first" (see [drivers](../../../docs/drivers.md)). Validating it proves the iOS-specific
parts were truly confined to the three seams, with almost no new shape required from the rest of
the system.

### The selector mapping

The YAML selector (`{ id: settings.reindex }`) is already platform-neutral; only *which app-side
attribute the backend reads to satisfy it* differs, and that lives entirely inside the new Driver.
On Android the `Selector` fields map as:

| `Selector` field | iOS | Android |
|---|---|---|
| `id` (primary) | `accessibilityIdentifier` | `resource-id` (Compose: `Modifier.testTag` + `testTagsAsResourceId`) |
| `label` (auxiliary) | `accessibilityLabel` | `content-desc` / `text` |
| `traits` (role filter) | UI traits (`button`, `link`, …) | widget class (`android.widget.Button`) |
| `value` | accessibility value | `text` / checked state |

### Where it sits in the capability matrix

The capability tokens already express the spread — Android slots in at the lean end without
inventing concepts:

| Capability | idb (iOS) | adb (Android) | Playwright (Web) | fake |
|---|:--:|:--:|:--:|:--:|
| `query` / `elements` / `screenshot` | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | — | — | ✅ | ✅ |
| `conditionWait` (native) | — | — | ✅ | ✅ |
| `network` (native) | — | — | ✅ | — |
| `multiTouch` | — | — | ✅ (emulated) | ✅ |

idb and Android sit at the lean end (coordinate actuation, mocked network); Playwright at the rich
end (semantic, native network). That an unmodified capability model spans both extremes is
evidence the abstraction holds.

### Phasing — Phase 2, after Web

Android is **Phase 2**, taken up *after* the Web (Playwright) backend ([BE-0041](../../proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)).
Web goes first because it is the only platform that needs no macOS and no device emulator, so it
fits the existing Linux `make check` / CI gate from day one and proves the core is platform-neutral
at the lowest cost. Android then confirms the *lean / coordinate* path on top of an
already-generalized core: its coordinate model mirrors idb almost exactly, so it introduces little
new shape, and the emulator runs on Linux CI via KVM (the Linux Kernel Virtual Machine), exercising
the **lean** end of `capabilities()`.

## Alternatives considered

- **Appium UiAutomator2 as the primary actuator.** A richer path that could add semantic actions
  (select-and-act by resource-id without computing bounds-center coordinates). Deferred: the `adb` +
  `uiautomator dump` path is the closest twin of idb, so it proves the abstraction with the least new
  shape; a semantic Android actuator can be added later as a second backend, just as XCUITest is
  envisaged alongside idb on iOS.
- **Building Android before Web.** Rejected for phasing reasons: although Android is the closer
  architectural twin, Web needs no macOS and no emulator and so fits the current Linux gate at the
  lowest cost. See [BE-0041](../../proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md).

## References

[DESIGN](../../../DESIGN.md), `bajutsu/drivers/`, `bajutsu/backends.py`,
[drivers.md](../../../docs/drivers.md),
[BE-0041 — Web (Playwright) backend](../../proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md),
[BE-0008 — Flutter support](../../proposals/BE-0008-flutter-support/BE-0008-flutter-support.md)
