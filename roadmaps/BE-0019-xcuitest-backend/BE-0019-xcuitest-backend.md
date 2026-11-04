**English** · [日本語](BE-0019-xcuitest-backend-ja.md)

# BE-0019 — XCUITest backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0019](BE-0019-xcuitest-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0019") |
| Implementing PR | [#366](https://github.com/bajutsu-e2e/bajutsu/pull/366), [#368](https://github.com/bajutsu-e2e/bajutsu/pull/368), [#374](https://github.com/bajutsu-e2e/bajutsu/pull/374), [#376](https://github.com/bajutsu-e2e/bajutsu/pull/376), [#391](https://github.com/bajutsu-e2e/bajutsu/pull/391), [#394](https://github.com/bajutsu-e2e/bajutsu/pull/394), [#397](https://github.com/bajutsu-e2e/bajutsu/pull/397), [#433](https://github.com/bajutsu-e2e/bajutsu/pull/433), [#672](https://github.com/bajutsu-e2e/bajutsu/pull/672) |
| Topic | Platform support |
| Related | [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) |
<!-- /BE-METADATA -->

## Introduction

XCUITest becomes a second actuator after idb. The registry can place it at the top of the stability-order ladder, because the abstraction is already maintained.

## Motivation

idb is the only iOS actuator today. It actuates by **frame-center coordinate taps**. idb has no semantic tap, so the run loop resolves a unique element through `query()` and taps its center. That is enough for headless CI and the common case, but it leaves real gaps.

idb advertises no `semanticTap`, no native `conditionWait`, and no `multiTouch` (`docs/drivers.md`). Two-finger gestures such as pinch and rotate raise `UnsupportedAction`. Those operations are noted as needing codegen → XCUITest. Some gestures therefore cannot run on idb at all. Every tap also goes through a coordinate round-trip, which is inherently more fragile than tapping an element by its identifier.

The architecture already anticipates a second actuator:

- DESIGN §3 draws "(future) XCUITest backend — deterministic code generation" beside idb.
- DESIGN §5 keeps the driver abstraction backend-independent, so that a second iOS actuator can slot in.
- `bajutsu/backends.py` declares the intended ordering in a comment: `"ios": ("idb",),  # later: ("xcuitest", "idb")`.

This proposal realizes that placeholder. It adds XCUITest as a genuine second actuator that sits **above** idb in the stability ladder. XCUITest supplies the semantic actuation and the multi-touch gestures that idb cannot. idb remains the fallback for headless environments where XCUITest cannot run.

## Detailed design

XCUITest becomes a registered actuator that satisfies the existing `Driver` Protocol. Nothing in the scenario DSL, the selector resolution, the run loop, the evidence subsystem, or the reporter changes.

- **Registry placement.** In `bajutsu/backends.py`, the iOS platform expands to `("xcuitest", "idb")`: XCUITest first, idb second. The registry adds `xcuitest` to `IMPLEMENTED` with its executable availability check. The actuator is the first implemented and available backend in order. `--backend ios` therefore prefers XCUITest when it can run, and falls back to idb when it cannot. No scenario or config changes. This is the forward-compatible behavior the registry was built for.
- **Driving the runner.** idb is a subprocess CLI. XCUITest instead actuates from inside a runner that lives on the Simulator, so Python and that runner need a channel. The runner stays resident for the whole run and serves a small loopback HTTP endpoint. The Python driver sends `query`, `tap`, and gesture requests to it.

  This reuses a loopback pattern that Bajutsu already has. `network.py` binds a `ThreadingHTTPServer` on `127.0.0.1`, and the launch env injects its address into the app under test (`BajutsuNet` in `BajutsuKit`). The same loopback carries the actuation requests, in the Python-to-runner direction. The minimal runner-side server is built in `BajutsuKit`, not adopted as a large external dependency. That keeps the channel under the project's control (see *Alternatives considered*). *Runner delivery and build* below settles how the runner is **delivered and built**: a prebuilt `.xctestrun` named in config, with on-demand `xcodebuild` as the fallback. *The Python ↔ runner channel* defines the protocol.
- **Richer capabilities, same contract.** The XCUITest driver's `capabilities()` returns `semanticTap`, native `conditionWait`, and `multiTouch`, in addition to what idb offers. Selection stays the determinism core, so a `tap` still resolves to exactly one element. XCUITest actuates that element by identifier, not by frame-center coordinates, which removes the coordinate round-trip. `pinch` and `rotate`, which raise `UnsupportedAction` on idb, become directly executable.
- **Determinism is preserved.** XCUITest offers a native condition wait. Even so, the orchestrator's waits remain condition waits with no fixed sleeps, and an ambiguous selector still fails immediately. The new capabilities widen what can be expressed and do not relax the rules. XCUITest is used only as a deterministic actuator at `run` time, and no LLM enters the Tier-2 gate. `codegen` is a separate path: it structurally maps a finished scenario to an XCUITest test source, and this proposal does not affect it.
- **App-agnostic, per-app where needed.** The driver itself is app-independent. Anything an app must provide to be driven by XCUITest (for example, a test host or launch arguments) lives under `targets.<name>`, alongside the existing per-app settings. The tool and the runner stay unchanged across apps. `doctor --target` reports XCUITest availability the same way it reports idb's.
- **Fallback intact.** idb stays second in the ladder. An environment where XCUITest cannot run, such as headless CI without the necessary host, degrades gracefully to coordinate-based idb. A run records the actuator that it selects. The manifest therefore states whether the run took the richer path or the fallback path, which is consistent with the existing degradation-disclosure rule.

The implementation-level shape of each piece follows.

### Registry placement and graceful pre-implementation

`bajutsu/backends.py` already separates *known* from *implemented* actuators. `select_actuator` falls a planned-but-unimplemented token through to the next available one. The ordering flip is therefore safe **before** the driver exists, and it is a one-line change.

Setting `PLATFORMS["ios"] = ("xcuitest", "idb")` makes `xcuitest` known automatically. `KNOWN_ACTUATORS` is derived from `PLATFORMS` at import, so there is no separate list to edit. `xcuitest` stays out of `IMPLEMENTED`, and `--backend ios` keeps resolving to idb, because xcuitest is planned. The day the driver lands, two additions turn it on: `xcuitest` in `IMPLEMENTED`, and an availability check (a `xcodebuild` probe in `_EXECUTABLE`).

One small related change deserves a mention. Today `capabilities_for()` raises `NotImplementedError` for a planned actuator. The BE-0082 preflight can reason about xcuitest *before* the driver exists only if `capabilities_for()` gains a branch that returns `XcuitestDriver.CAPABILITIES`, a class constant read with no device. Without that branch, the capability set arrives together with the driver.

### Runner delivery and build: the open decision, resolved

There are two viable paths. The recommendation is a prebuilt test runner named in config, with an on-demand build as the fallback. This mirrors the existing split between `appPath` (prebuilt) and `build` (on-demand), so XCUITest adds no new authoring model.

- **Prebuilt (primary).** `targets.<name>.xcuitest.testRunner` names a built `*.xctestrun` (or the `.app` test host), the same way `appPath` names the built `.app`. A run launches it directly. This is fast. It is also the only option on a machine that has the Simulator but not the full Xcode toolchain.
- **On-demand (fallback).** `targets.<name>.xcuitest.build` is a shell command that `serve` or `run` executes to produce the `.xctestrun` when it is missing. An example is `xcodebuild build-for-testing -scheme … -destination 'platform=iOS Simulator,…' -derivedDataPath …`. This works the way `build` produces a missing `appPath`.

Both knobs live under `targets.<name>`, so the tool stays app-agnostic. DESIGN §1 says that bajutsu receives a prebuilt artifact and does not build it. The design honors that rule: the prebuilt path is preferred, and `build` is an explicit, opt-in convenience.

The XCUITest **runner code itself** is a tiny, generic XCTest target. It ships in `BajutsuKit` (alongside `BajutsuNet`) and is not per app. It drives `XCUIApplication` for whatever bundle id the launch passes, so one runner serves every target.

### The Python ↔ runner channel

idb is a subprocess CLI. XCUITest actuates from a test process that is resident on the Simulator, so the two sides need a channel for the run's lifetime. The channel reuses the existing loopback HTTP pattern: `bajutsu/network.py` binds a `ThreadingHTTPServer` on `127.0.0.1`, and the app already talks to it through `BAJUTSU_COLLECTOR`. Here the direction is **Python → runner**.

The `BajutsuKit` runner starts a small `127.0.0.1:<port>` server inside its test method and stays resident. The Python `XcuitestDriver` sends actuation requests to it. The port reaches the runner the same way, as a launch or env argument. The channel is confined to loopback and never widens host exposure.

The contract maps one-to-one onto the `Driver` Protocol, so nothing above the driver changes:

| Driver call | request | response |
|---|---|---|
| `query()` | `GET /elements` | The normalized `Element[]` JSON, in the same `identifier`/`label`/`value`/`traits`/`frame` shape that idb produces, so `find_all` and `resolve_unique` are unchanged. **Each element also carries an opaque per-snapshot `handle`**. The runner mints the handle, and Python round-trips it into `tap` and `gesture` (see *Element addressing*). The handle is not a selector field, so matching is unaffected |
| `tap(sel)` | `POST /tap {handle}` | ok / not-found. Python first resolves the unique element from `query()`, so selection stays the determinism core. It then sends that element's **snapshot handle**. XCUITest taps that exact element, not coordinates (see *Element addressing*) |
| `pinch` / `rotate` | `POST /gesture {handle, kind, scale\|radians}` | ok. These are the two-finger gestures that raise `UnsupportedAction` on idb |
| `wait_for(sel)` | Python polling `GET /elements` (the orchestrator's condition wait), or the runner's native expectation behind the same bounded, sleep-free contract | ok / timeout |
| `screenshot(path)` | `GET /screenshot` | PNG bytes |

Errors map to the existing `Driver` exceptions. `ElementNotFound` and `AmbiguousSelector` stay Python-side, because resolution stays Python-side. The minimal server lives in `BajutsuKit`, not in a large external automation dependency. That keeps the channel under the project's control (see *Alternatives considered*).

### Element addressing across the channel

Resolution stays Python-side. The channel must therefore let Python act on **exactly** the element it resolved. The runner must never re-resolve and possibly pick a different match.

The runner mints a **stable per-snapshot handle** for each `Element` it returns from `GET /elements`. It holds the mapping for the life of that snapshot. `POST /tap` and `POST /gesture` carry that handle, not a re-queried predicate. Python resolves the unique element from the snapshot and sends its handle. The runner maps the handle back to the `XCUIElement` it already located, and acts on it.

The accessibility `identifier` alone is **insufficient** as the handle. An identifier need not be unique. Re-resolving by identifier on the runner would reintroduce the very ambiguity that selection just removed.

If the screen has changed and the handle is stale, the runner returns `stale`. The driver then raises the same error that a vanished element raises today. The failure is loud, and the runner never silently re-taps whatever now matches.

### Runner lifecycle: readiness, teardown, and crashes

The resident runner is a process with a startup and a failure mode, so its lifecycle is explicit:

- **Readiness.** The driver sends no actuation request until the runner's loopback server accepts connections. It waits on a bounded, sleep-free readiness probe (`GET /health`), the same condition-wait discipline used elsewhere (no fixed sleep). A timeout fails the run with a clear "runner did not come up", not a hang.
- **Teardown.** Releasing the lease stops the runner and its loopback server, and terminates the app. This mirrors how the device pool already stops a per-device collector on release. Nothing is left listening.
- **Crash.** Suppose the runner exits or stops answering mid-run. The next request fails, and the driver raises a `Driver` error, so the run fails loudly. A crashed runner is never silently read as "element not found". An infrastructure failure stays distinct from a test outcome.

### Parallel runs and the device pool (BE-0020)

The device pool (`runner/pool.py`) leases N Simulators for parallel scenarios and gives each its own per-device resources. XCUITest fits that model unchanged. Each leased device gets **its own resident runner on its own ephemeral loopback port**. The pool already starts one network collector per device on its own port in the same way.

The pool allocates the port per lease and hands it to that device's runner through its launch arguments. Two parallel runs never share a runner or a port. Lease release stops that device's runner alongside its collector. There is no global runner and no shared port, so parallel XCUITest runs stay as isolated as parallel idb runs.

### Network evidence under XCUITest (interaction with BE-0020)

The XCUITest capability set does **not** include `NETWORK`. XCUITest is a richer *actuator*, not a network-native observer.

On iOS, the app-side collector supplies network evidence. `BajutsuNet` POSTs to the loopback receiver named by `BAJUTSU_COLLECTOR`. The collector is **actuator-independent**: it rides on the app's launch environment, not on how the app is driven. Under XCUITest, the runner must therefore inject `BAJUTSU_COLLECTOR` into `XCUIApplication.launchEnvironment`, exactly as idb injects it into its launch env. Network capture then works identically.

The app-side collector still supplies network evidence. Choosing XCUITest therefore does **not** trigger the read-only *sibling* fallback of BE-0020. That fallback engages only for an evidence kind that no eligible same-platform backend can supply, and network is not such a kind.

This makes the BE-0020 boundary concrete: a same-platform read-only provider fills evidence gaps, while *actuation* stays with the single selected backend.

### Capabilities, doctor, and disclosure

`XcuitestDriver.capabilities()` (and `capabilities_for("xcuitest")`) returns `QUERY`, `ELEMENTS`, `SCREENSHOT`, **`SEMANTIC_TAP`**, **`CONDITION_WAIT`**, and **`MULTI_TOUCH`**. The three beyond idb's set unlock identifier taps and pinch/rotate.

`doctor --target` reports XCUITest availability (the toolchain plus the configured or buildable runner) beside idb's. The run manifest already records the selected actuator, so a fallback to idb is disclosed and never silent.

### Validation

Validation splits by what the fast gate can prove without a Simulator and what needs one.

**Fast gate (no device).**

- Registry: `--backend ios` prefers `xcuitest` when it is available and falls back to `idb` when it is not. The test drives `select_actuator` with an injected availability function.
- `capabilities_for("xcuitest")` returns the richer set.
- Driver: the test builds the actuation requests and parses responses against an **injected fake HTTP transport**, the way idb tests inject a fake `run`. It asserts four behaviors:
  - `tap` resolves a unique element, then addresses it by the snapshot handle that element carried.
  - `pinch` and `rotate` emit the gesture request.
  - A `stale` response raises the vanished-element error.
  - An ambiguous selector still fails before any request.
- Lifecycle: the readiness probe waits and then times out cleanly against a fake transport. A pool of two leases hands each driver a distinct port.

No test puts the runner, or any LLM, on the `run`/CI gate.

**On-device (e2e path).** The heavier `e2e.yml` path runs the real `BajutsuKit` runner against a booted Simulator. It covers two cases:

- A scenario that resolves an element Python-side and actuates it by its snapshot handle, and that runs a pinch or rotate which idb cannot.
- A fallback run on a host where XCUITest is unavailable, which confirms graceful degradation to idb.

## Alternatives considered

- **Replace idb with XCUITest outright.** XCUITest is the richer actuator. The headless, coordinate-based operation of idb, however, is valuable in exactly the CI environments where a full XCUITest host is awkward. An ordered ladder keeps the best of each: prefer XCUITest, fall back to idb, and leave the scenario unchanged.
- **Bolt the missing gestures onto idb.** One could try to synthesize pinch and rotate from the single-touch primitives of idb. idb fundamentally exposes single touch, so the result would be an unreliable approximation of a multi-touch gesture. That is the fragile, non-deterministic behavior the project avoids. A real multi-touch backend is the honest fix.
- **Adopt WebDriverAgent as the runner channel.** WebDriverAgent is a proven HTTP and XCTest server. It would supply the Python ↔ runner channel off the shelf. Rejected for the first cut, for two reasons. It is a large dependency to vendor and maintain. It also pulls the project away from its thin-dependency stance (DESIGN §4), where backends are driven by shelling out to a focused tool. A minimal runner-side server in `BajutsuKit` reuses the loopback pattern already in `network.py` and `BajutsuNet`. That keeps the surface small and under the project's control. WebDriverAgent stays a fallback if the minimal server proves insufficient.
- **Route only specific gestures to XCUITest while idb stays the actuator.** Letting two drivers operate one device reintroduces the non-determinism that the single-actuator rule (DESIGN §3.3 and §5) exists to prevent. The actuator is fixed once per run. The read-only fallback design (BE-0020) handles capability gaps in *evidence* separately, and *actuation* stays with one backend.

## Progress

- [x] Backend registration — `PLATFORMS["ios"] = ("xcuitest", "idb")` and `xcuitest` in `IMPLEMENTED`, with idb the fallback (`bajutsu/backends.py`).
- [x] Runner-side server — a generic XCTest target in `BajutsuKit` (`Sources/BajutsuRunner/RunnerServer.swift`) exposing loopback `/elements` / `/tap` / `/gesture` / `/swipe` / `/type` endpoints ([#374](https://github.com/bajutsu-e2e/bajutsu/pull/374), [#376](https://github.com/bajutsu-e2e/bajutsu/pull/376)).
- [x] Python↔runner channel and `XcuitestDriver` — `bajutsu/drivers/xcuitest.py`, acting on a stable per-snapshot element handle ([#366](https://github.com/bajutsu-e2e/bajutsu/pull/366), [#368](https://github.com/bajutsu-e2e/bajutsu/pull/368)).
- [x] Run-lifecycle wiring and preconditions — per-lease runner start/teardown, forwarded preconditions ([#391](https://github.com/bajutsu-e2e/bajutsu/pull/391), [#394](https://github.com/bajutsu-e2e/bajutsu/pull/394)).
- [x] Capabilities, doctor, and disclosure — `doctor --target` merges idb's checks and falls back to idb when xcuitest is selected ([#397](https://github.com/bajutsu-e2e/bajutsu/pull/397)).
- [x] Runner test target — the concrete XCUITest `ElementProviding` that walks `XCUIApplication` into the normalized `Element` shape (identifier / label / value / traits / frame) and actuates by snapshot handle (tap / gesture / swipe / type / screenshot); a thin UI-testing target (`BajutsuKit/Runner/`) that imports XCTest + `BajutsuRunner`, calls `RunnerServer.startFromEnvironment()`, and stays resident for the run.
- [x] App-agnostic launch — the target bundle id is forwarded to the runner (`BAJUTSU_BUNDLE_ID` alongside the existing `BAJUTSU_LAUNCH_*`): `XcuitestEnvironment` sets it, `RunnerServer.forwardedBundleId` exposes it, the target launches `XCUIApplication(bundleIdentifier:)`. A minimal host app in the project satisfies XCUITest's build-time target-app requirement.
- [x] Runner packaging and example config — `make -C demos/showcase runner-build` builds the generic runner into a `*.xctestrun`; the two `-noax` showcase targets set `xcuitest.testRunner` (with `build` as the on-demand fallback).
- [x] On-device validation — a pinch/rotate scenario idb cannot run, through the full `run` path on the `e2e.yml` path. The `xcuitest (multi-touch)` job runs `scenarios/gestures_multitouch.yaml` (`--backend xcuitest`) against a flat, scroll-free `GestureView` (swapped in by the `SHOWCASE_GESTURES` launch env, mirroring BE-0114's `SHOWCASE_CONFORMANCE`) and is green ([#672](https://github.com/bajutsu-e2e/bajutsu/pull/672)). Scoped single-device: the parallel device pool is covered by BE-0020's own tests and the on-device conformance job, and the idb graceful-degradation fallback by the registry's fast-gate unit tests (select_actuator with an injected availability fn) plus the idb `smoke` job — a macOS runner always has xcodebuild, so xcuitest never actually degrades to idb on-device to observe.
- [x] Query performance — replaced the per-element `/elements` reads (each property its own XCUITest round-trip, ~12s on an 84-element screen, which had forced the driver socket timeout up to 30s) with a single `XCUIApplication` snapshot walk ([BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md), [#670](https://github.com/bajutsu-e2e/bajutsu/pull/670)).

The work landed in slices:

- #366 and #368: the driver and the channel.
- #374 and #376: the runner server and its endpoints.
- #391 and #394: the run-lifecycle wiring and the preconditions.
- #397: the doctor fallback.
- #670: the single-snapshot query (BE-0105).

**Investigation (2026-07-02).** The test ran on an iPhone 17 Pro with iOS 26.1 (fb-idb 1.1.7). `idb ui describe-all` surfaces a UIKit `UITabBarController` and a SwiftUI `TabView` only as one opaque "Tab Bar" group with no per-tab children. XCUITest enumerates each tab as a label-addressable button, and `tabBars.buttons["…"].tap()` switches the tab. Both `-noax` showcase apps behaved this way, and neither has accessibility identifiers.

This is the concrete case that the runner test target unlocks. Tabs become selector-addressable where idb cannot see them, so crawl over xcuitest needs no vision tab locator.

**Runner target landed (2026-07-02).** The concrete provider and the UI-testing target are implemented and validated end to end on a device. `bajutsu run --backend ios` on `showcase-uikit-noax` resolved `tap: {label: "Search", traits: [button]}` uniquely and switched the tab. The `exists` assertion passed, and the manifest recorded `backend: "xcuitest"`.

The device run also surfaced a lifecycle gap. `xcodebuild` does not forward its process environment into the Simulator test runner. `XcuitestEnvironment` now patches the run's `BAJUTSU_*` variables into a private copy of the `.xctestrun`'s per-target `TestingEnvironmentVariables`. The copy sits beside the built products, so `__TESTROOT__` still resolves.

**Query performance resolved (2026-07-05).** [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) ([#670](https://github.com/bajutsu-e2e/bajutsu/pull/670)) replaced the per-property `/elements` reads with a single `app.snapshot()` walk. That removed the last blocker in that box. BE-0105 keeps the snapshot-handle addressing of BE-0019 intact:

- Python resolves the unique element.
- The runner acts on exactly that handle.
- An ambiguous selector fails immediately.
- A stale handle fails loudly.

**On-device gesture validation wired (2026-07-05).** Four pieces were added:

- A flat, scroll-free `GestureView` with `log.pinch` and `log.rotate` two-finger targets. The `SHOWCASE_GESTURES` launch env swaps it in. It mirrors the `SHOWCASE_CONFORMANCE` of BE-0114, and it is a SwiftUI-only test affordance.
- The `scenarios/gestures_multitouch.yaml` scenario. It pinches and rotates the targets and asserts that each mirrored a11y value flips.
- The `xcuitest (multi-touch)` job in `e2e.yml`. It runs the scenario through the full `run` path on `--backend xcuitest`.
- A decision to keep the scenario off the idb smoke path. idb cannot run it, because single touch raises `UnsupportedAction`.

The first attempt put the targets deep in a Form on the Log tab. A dedicated screen replaced that placement, because the coordinate swipe of XCUITest did not scroll the SwiftUI Form far enough to reach off-screen rows. That swipe-to-scroll limitation is separate from this actuation proof, and it deserves its own follow-up. The item flips to Implemented once the job is green.

## References

[DESIGN §5 / §3](../../DESIGN.md), `bajutsu/backends.py`
