**English** · [日本語](BE-0007-android-backend-ja.md)

# BE-0007 — Android backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0007](BE-0007-android-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0007") |
| Implementing PR | [#658](https://github.com/bajutsu-e2e/bajutsu/pull/658) |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

A driver for the Android emulator, driving the UI via `adb` + UI Automator and mapping
`resource-id` / `content-desc` selectors id-first. Architecturally it is the twin of the existing
iOS `idb` backend: subprocess-driven, coordinate-based actuation, no semantic tap. Adding it means
adding a new triple — actuator + environment manager + id convention — while the deterministic
core stays byte-for-byte the same.

## Motivation

Android is the **architectural twin of idb**: subprocess-driven, coordinate actuation, and a
transiently-empty tree during transitions — so it reuses idb's *resolve-with-retry,
fail-ambiguity-fast* pattern (see [drivers](../../docs/drivers.md)) almost unchanged. Building it
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
| **id convention** | `resource-id` (XML `android:id`; Jetpack Compose `Modifier.testTag` surfaced as `resource-id` via `testTagsAsResourceId`). `text` → `label` (`content-desc` fallback); `content-desc` → `value` (the state-value mirror, SPEC §2.1); widget class → `traits` |
| **Evidence providers** | screenshot = `adb exec-out screencap`; video = `adb shell screenrecord`; `deviceLog` = `adb logcat` (filtered by tag/pid); `network` = no native monitor → same mock story as iOS |
| **codegen target** | Espresso or UI Automator (Kotlin/Java) |

The **environment** row lands as an `AndroidEnvironment` implementing the cross-platform `Environment` Protocol from [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md): its `start` runs the adb sequence (`pm clear` for a clean state → `am start` to launch → `am start -a android.intent.action.VIEW -d <url>` for a deeplink) and returns the `adb` driver. Actuator and environment are thus the Android instances of the same two seams iOS fills with idb + simctl, so Android adds no new shape to the runner — it plugs into the seam BE-0009 extracts.

### The architectural twin of idb

Android is the **architectural twin of idb**: subprocess-driven, coordinate actuation, and a
transiently-empty tree during screen transitions. Because of that shared shape it reuses idb's
*resolve-with-retry, fail-ambiguity-fast* pattern — poll the tree, retry on a transiently-empty
result, and fail immediately if a selector resolves to more than one element rather than "tapping
whatever matched first" (see [drivers](../../docs/drivers.md)). Validating it proves the iOS-specific
parts were truly confined to the three seams, with almost no new shape required from the rest of
the system.

### The selector mapping

The YAML selector (`{ id: settings.reindex }`) is already platform-neutral; only *which app-side
attribute the backend reads to satisfy it* differs, and that lives entirely inside the new Driver.
On Android the `Selector` fields map as:

| `Selector` field | iOS | Android |
|---|---|---|
| `id` (primary) | `accessibilityIdentifier` | `resource-id` (Compose: `Modifier.testTag` + `testTagsAsResourceId`) |
| `label` (auxiliary) | `accessibilityLabel` | `text` (visible; `content-desc` fallback) |
| `traits` (role filter) | UI traits (`button`, `link`, …) | widget class (`android.widget.Button`) |
| `value` | accessibility value | `content-desc` (the state-value mirror, SPEC §2.1) |

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

### The concrete tooling

Everything the backend needs is a subprocess call to `adb` (plus `emulator` for boot), which is why
Android is idb's twin:

- **Tree** — `adb -s <serial> exec-out uiautomator dump /dev/tty` streams the window's XML. Each
  `<node>` carries `resource-id`, `content-desc`, `text`, `class`, and `bounds="[x1,y1][x2,y2]"`; the
  driver parses `bounds` into the `Frame` (x, y, w, h) and taps its centre — the same frame-centre
  round-trip idb performs.
- **Actuation** — `adb shell input tap x y` / `input swipe x1 y1 x2 y2 <ms>` / `input text <s>`.
  Coordinate-based and single-touch — no semantic tap, exactly like idb.
- **The transient-empty tree** — mid-transition, `uiautomator dump` intermittently fails with *"null
  root node returned by UiTestAutomationBridge"* (the animation has not settled). This is the precise
  analogue of idb's near-empty tree during a SwiftUI transition, so the driver reuses idb's
  *resolve-with-retry, fail-ambiguity-fast* discipline unchanged: retry the dump a bounded number of
  times, and still fail immediately on an ambiguous (2+) match rather than tapping whatever matched
  first.
- **Compose ids** — `Modifier.testTag("…")` surfaces as `resource-id` only when the subtree's root
  sets `Modifier.semantics { testTagsAsResourceId = true }` (Compose 1.2.0-alpha08 and later). That is
  the id convention the docs will state for Compose apps.
- **Boot readiness** — an AVD boot is followed by `adb wait-for-device` and then polling `getprop
  sys.boot_completed` for `1` (a bounded condition wait, no fixed sleep) before the app is launched.

### Work breakdown (MECE)

1. **Registry wiring** (`bajutsu/backends.py`). `PLATFORMS["android"] = ("adb",)` and `_EXECUTABLE["adb"]`
   already exist, so the remaining edits just turn the planned token on: add `adb` to `IMPLEMENTED`, a
   `capabilities_for("adb")` branch returning `AdbDriver.CAPABILITIES` (a class constant, read with no
   device, for the BE-0082 preflight), and a `make_driver("adb", serial)` branch.
2. **`AdbDriver` actuator** (`bajutsu/drivers/adb.py`). Parse `uiautomator dump` XML into normalized
   `Element`s (the selector mapping above), coordinate actuation via `input tap/swipe/text`, `screenshot`
   via `screencap`, the transient-empty retry, and `CAPABILITIES = {QUERY, ELEMENTS, SCREENSHOT}`.
   `pinch`/`rotate` raise `UnsupportedAction` (single-touch), as on idb.
3. **`AndroidEnvironment`** (`bajutsu/environment.py`, `environment_for`). `start` runs the adb sequence
   — `pm clear <package>` for a clean state (the `erase` equivalent) → optional AVD boot + boot-readiness
   wait → `am start -n <pkg>/<activity>` to launch → `am start -a android.intent.action.VIEW -d <url>` for
   a deeplink — and returns the `adb` driver. Fill the lease-shaping methods as `_DeviceEnvironment` does:
   device catalog from `adb devices` + `getprop`, a relauncher (`am force-stop` then `am start`), teardown
   (`am force-stop`), and the crawl-lane methods with `has_devices() = True` and `plan_lanes` over serials.
4. **Evidence and device control**. `deviceLog` via `adb logcat` (tag/pid filtered); video via `adb shell
   screenrecord` (device-side, pulled on stop); `network` has no native monitor, so it reuses iOS's mocked
   story (no `NETWORK` capability). Map the device-state steps the emulator supports (`setLocation` via
   `emu geo fix`, clipboard) and raise `UnsupportedAction` for the rest.
5. **doctor and disclosure**. `doctor --target` reports `adb`/emulator availability beside idb's; the run
   manifest records `backend: "adb"`, so the selected actuator is disclosed.
6. **codegen target**. A `to_espresso` (or UI Automator) generator (`bajutsu/codegen_espresso.py`) registered
   alongside the Playwright/XCUITest generators — a structural mapping only, no LLM, so it does not touch the
   run gate. Can land as a follow-up slice after the driver.
7. **Validation**. Fast gate (no device): drive `select_actuator`/`capabilities_for` for the registry flip,
   and the driver against an **injected fake `run`** over captured `uiautomator dump` XML fixtures — asserting
   the selector mapping, frame-centre taps, the transient-empty retry, and ambiguous-fails-fast — plus the
   environment sequence with an injected runner. On-device (e2e): an Android emulator on Linux CI via KVM (the
   `android-emulator-runner` action) running a scenario over `--backend android`; kept off the fast `make check`
   gate, like the idb e2e path.

### Phasing — Phase 2, after Web

Android is **Phase 2**, taken up *after* the Web (Playwright) backend ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)).
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
  lowest cost. See [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Registry wiring — `adb` in `IMPLEMENTED`, `capabilities_for`/`make_driver` branches (`bajutsu/backends.py`).
- [x] `AdbDriver` actuator — `uiautomator dump` parsing, coordinate actuation, transient-empty retry, capabilities (`bajutsu/drivers/adb.py`).
- [x] `AndroidEnvironment` — the boot-readiness wait → `pm clear` → `am start` → deeplink sequence and the lease-shaping methods (`bajutsu/platform_lifecycle.py`, over the new `bajutsu/adb.py` command layer).
- [ ] Evidence and device control — `logcat` deviceLog, `screenrecord` video, and mocked network
  **(done, 2026-07-08)**; supported device-state steps (the `DeviceControl` family) remain a
  follow-up, deferred because Android supports only a subset of the coarse `deviceControl` capability.
- [x] doctor and disclosure — `doctor --target` availability beside idb; manifest records `backend: "adb"`.
- [ ] codegen target — Espresso / UI Automator generator (follow-up slice).
- [ ] Validation — fast-gate driver/registry tests over dump fixtures **(done)**; core scenarios
  driven on a local arm64 emulator **(done, 2026-07-07)**; wiring the on-device e2e into KVM CI
  (follow-up).

Log:

- 2026-07-08 — Interval evidence slice (Unit 4, the evidence half). `video` now records via `adb
  shell screenrecord` and `deviceLog` via `adb logcat`, the twins of the simctl providers
  (`bajutsu/adb.py` command builders + `start_screenrecord` / `start_logcat` in `bajutsu/intervals.py`).
  `screenrecord` records device-side, so its `Interval` finalizes on SIGINT then pulls the mp4 off
  the device and removes the device copy; `logcat` streams to the file and stops on SIGTERM. Android
  routes through the driver-supplied interval seam (`AdbDriver.driver_interval`), which the FileSink
  now dispatches to for any non-simctl backend — the `web_interval` field was generalized to
  `driver_interval`, shared by the Playwright and adb drivers. Mocked network needed no new code:
  the app-side collector URL already reaches the app through the launch env as an intent extra (a
  test now pins this). A failed `screenrecord` pull now drops just that artifact with a warning
  instead of aborting the finalize loop (which would orphan the logcat subprocess) or failing an
  otherwise-passing scenario over evidence I/O. Fast-gate unit tests cover the command builders, both
  interval starters (injected spawn / run, incl. the pull-surfaces / cleanup-suppressed asymmetry),
  the `driver_interval` routing, the FileSink↔`AdbDriver` end-to-end dispatch, the drop-on-failed-stop
  resilience, and the collector-env forwarding. Docs updated (`docs/drivers.md`, `docs/evidence.md`,
  `docs/architecture.md`, all ja mirrors). Two on-device caveats are deferred to the e2e slice: `adb
  screenrecord` caps a single recording at ~180s (documented), and the SIGINT-to-device finalization
  is the standard idiom but is device/adb-version dependent, so it is validated/tuned there. Device
  control (setLocation / clipboard / …) and codegen remain follow-ups, so the item stays
  **In progress**.
- 2026-07-07 — First on-device validation on an arm64 API 34 emulator. Two fixes fell out of it:
  (1) the Android showcase did not build — each module's Gradle `namespace` used the `.android.`
  applicationId instead of the Kotlin source package, so the unqualified `BuildConfig` references
  and the manifest's relative `.MainActivity` failed to resolve; aligning `namespace` to the source
  package (applicationId unchanged) fixes both. (2) the driver's selector mapping read `value` from
  `text` (the visible string), so a `value` assertion saw "Matches: 5" / "Not favorited" instead of
  the mirrored "5" / "off"; the showcase mirrors state into `content-desc` (SPEC §2.1), so `value`
  now reads `content-desc` and `label` reads `text`. With both fixed, the core id/tap/type/value
  scenarios pass on device (smoke, firstlook, search, components, data_driven, modals, relaunch,
  system, evidence-capture). The remaining scenarios exercise the deferred slices — device control
  (honestly capability-gated), multi-touch (`UnsupportedAction`), by-scheme deeplink / system back
  (`BackButton`), mocked network, runtime-permission alerts, and visual/golden baselines — plus
  three borderline cases now diagnosed for the follow-up: `gestures`' long-press passes, but
  double-tap does not register because the gap between two `adb shell input tap` invocations exceeds
  the platform double-tap window (the `input` binary's own startup dominates, so batching them into
  one shell round-trip is not enough); `controls` reaches `log.segment.one` but its `log.segment.value`
  sits just past the scrolled viewport; `notices` needs system back plus a list scroll. All three are
  on-device actuation/scroll tuning. Still **In progress**.
- 2026-07-04 — Core driver slice landed: the `adb` command layer (`bajutsu/adb.py`, the twin of
  `simctl.py`), the `AdbDriver` coordinate actuator (`bajutsu/drivers/adb.py` — `uiautomator dump`
  XML → `Element` selector mapping, frame-centre taps, the transient-empty retry, ambiguity
  fails-fast, `CAPABILITIES = {query, elements, screenshot}`), the `AndroidEnvironment` launch
  sequence (`platform_lifecycle.py`), the registry flip (`backends.py`), and `doctor`/preflight
  reporting (`preflight.py`, `cli/commands/doctor.py`). Fast-gate unit tests over captured dump XML
  fixtures cover the selector mapping, frame-centre taps, the transient-empty retry, and
  ambiguous-fails-fast. Docs updated (`docs/drivers.md`, `docs/architecture.md`, `DESIGN.md`, both
  ja mirrors). Interval evidence (`screenrecord`/`logcat`), device control, codegen, and the
  on-device emulator e2e remain follow-up slices, so the item stays **In progress**.
- 2026-07-03 — Started. The Android showcase fixture the driver will drive landed first
  ([#552](https://github.com/bajutsu-e2e/bajutsu/pull/552)): the Compose + Views twins of
  `demos/showcase` (a11y/noax flavors), exercising the `testTag`/`android:id` → `resource-id`
  conventions and the selector mapping above from the app side, with four `backend: [android]`
  targets wired into `showcase.config.yaml`. This is preparation — it ticks no work-breakdown box
  above; the driver slices (registry wiring onward) are next.

## References

[DESIGN](../../DESIGN.md), `bajutsu/drivers/`, `bajutsu/backends.py`,
[drivers.md](../../docs/drivers.md),
[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md),
[BE-0008 — Flutter support](../BE-0008-flutter-support/BE-0008-flutter-support.md)
