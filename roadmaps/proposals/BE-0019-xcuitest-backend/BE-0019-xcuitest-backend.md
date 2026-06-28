**English** · [日本語](BE-0019-xcuitest-backend-ja.md)

# BE-0019 — XCUITest backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0019](BE-0019-xcuitest-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Backend expansion (iOS actuators) |
<!-- /BE-METADATA -->

## Introduction

A second actuator after idb. Make it registerable at the top of the stability-order ladder (the abstraction is already maintained).

## Motivation

idb is the only iOS actuator today, and it actuates by **frame-center coordinate taps**: it has no semantic tap, so the run loop resolves a unique element via `query()` and taps its centre. That is enough for headless CI and the common case, but it leaves real gaps. idb advertises no `semanticTap`, no native `conditionWait`, and no `multiTouch` (`docs/drivers.md`): two-finger gestures such as pinch and rotate raise `UnsupportedAction`, and those operations are noted as needing codegen → XCUITest. So there are gestures a scenario simply cannot run today on idb, and every tap goes through a coordinate round-trip that is inherently more fragile than tapping an element by its identifier.

The architecture already anticipates this. DESIGN §3 draws "(future) XCUITest backend — deterministic code generation" beside idb, DESIGN §5 keeps the driver abstraction backend-independent specifically so a second iOS actuator can slot in, and `bajutsu/backends.py` already declares the intended ordering in a comment: `"ios": ("idb",),  # later: ("xcuitest", "idb")`. The point of this proposal is to realize that placeholder — add XCUITest as a genuine second actuator that sits **above** idb in the stability ladder, supplying semantic actuation and the multi-touch gestures idb cannot, while idb remains the fallback for headless environments where XCUITest cannot run.

## Detailed design

XCUITest becomes a registered actuator that satisfies the existing `Driver` Protocol, so nothing in the scenario DSL, selector resolution, run loop, evidence subsystem, or reporter changes.

- **Registry placement.** In `bajutsu/backends.py` the iOS platform expands to `("xcuitest", "idb")` — XCUITest first, idb second — and `xcuitest` is added to `IMPLEMENTED` with its executable availability check. Because the actuator is "the first implemented and available backend in order," `--backend ios` automatically prefers XCUITest when it can run and falls back to idb when it cannot, with no change to any scenario or config. This is exactly the forward-compatible behaviour the registry was built for.
- **Driving the runner.** Unlike idb, which is a subprocess CLI, XCUITest actuates from inside a runner that lives on the Simulator, so Python and that runner need a channel. The runner stays resident for the whole run and serves a small loopback HTTP endpoint; the Python driver sends `query` / `tap` / gesture requests to it. This reuses the loopback pattern Bajutsu already has — `network.py` binds a `ThreadingHTTPServer` on `127.0.0.1` and the launch env injects its address into the app under test (`BajutsuKit`'s `BajutsuNet`); the same loopback, in the Python→runner direction, carries the actuation requests. The minimal runner-side server is built in `BajutsuKit` rather than adopting a large external dependency, keeping the channel under the project's control (see *Alternatives considered*). How the runner is **delivered and built** is settled under *Runner delivery and build* below (prebuilt `.xctestrun` named in config, with on-demand `xcodebuild` as the fallback), and the channel protocol under *The Python ↔ runner channel*.
- **Richer capabilities, same contract.** The XCUITest driver's `capabilities()` returns `semanticTap`, native `conditionWait`, and `multiTouch` in addition to what idb offers. Because selection stays the determinism core, a `tap` still resolves to exactly one element — XCUITest just actuates it by identifier rather than by frame-centre coordinates, removing the coordinate round-trip. `pinch` / `rotate`, which raise `UnsupportedAction` on idb, become directly executable.
- **Determinism is preserved.** Even where XCUITest offers a native condition-wait, the orchestrator's waits remain condition waits with no fixed sleeps, and an ambiguous selector still fails immediately — the new capabilities widen what can be expressed, they do not relax the rules. Crucially, XCUITest is used only as a deterministic actuator at `run` time; no LLM enters the Tier-2 gate. (This is distinct from `codegen`, which structurally maps a finished scenario to an XCUITest test source; that path is unaffected.)
- **App-agnostic, per-app where needed.** The driver itself is app-independent. Anything an app must provide to be driven by XCUITest (e.g. a test host or launch arguments) lives under `targets.<name>`, alongside the existing per-app settings, so the tool and runner stay unchanged across apps. `doctor --target` reports XCUITest availability the same way it reports idb's.
- **Fallback intact.** With idb kept second in the ladder, environments where XCUITest cannot run (headless CI without the necessary host) degrade gracefully to coordinate-based idb. A run records which actuator was selected, so the manifest states whether the richer or the fallback path was taken — consistent with the existing degradation-disclosure rule.

The implementation-level shape of each piece follows.

### Registry placement and graceful pre-implementation

`bajutsu/backends.py` already separates *known* from *implemented* actuators, and `select_actuator` falls a planned-but-unimplemented token through to the next available one. So the ordering flip is safe **before** the driver exists: set `PLATFORMS["ios"] = ("xcuitest", "idb")` and add `xcuitest` to `KNOWN_ACTUATORS` without adding it to `IMPLEMENTED` — `--backend ios` keeps resolving to idb (xcuitest is "planned"), and the day the driver lands, adding `xcuitest` to `IMPLEMENTED` plus an availability check (`_EXECUTABLE`/a `xcodebuild` probe) is what turns it on. `capabilities_for("xcuitest")` returns its capability set without constructing a driver, so the BE-0082 preflight reasons about the richer actuator with no device.

### Runner delivery and build — the open decision, resolved

Two viable paths; **recommend a prebuilt test runner named in config, with on-demand build as the fallback**, mirroring the existing `appPath` (prebuilt) + `build` (on-demand) split so XCUITest carries no new authoring model:

- **Prebuilt (primary).** `targets.<name>.xcuitest.testRunner` names a built `*.xctestrun` (or the `.app` test host) the same way `appPath` names the built `.app`. A run launches it directly — fast, and the only option on a machine without the full Xcode toolchain beyond the Simulator.
- **On-demand (fallback).** `targets.<name>.xcuitest.build` is a shell command (e.g. `xcodebuild build-for-testing -scheme … -destination 'platform=iOS Simulator,…' -derivedDataPath …`) that `serve`/`run` runs to produce the `.xctestrun` when it is missing — exactly how `build` produces a missing `appPath`. Both knobs live under `targets.<name>`, so the tool stays app-agnostic; DESIGN §1 (bajutsu receives a prebuilt artifact, does not build it) is honored by preferring the prebuilt path and treating `build` as an explicit, opt-in convenience.

The XCUITest **runner code itself** is a tiny, generic XCTest target shipped in `BajutsuKit` (alongside `BajutsuNet`), not per app — it drives `XCUIApplication` for whatever bundle id the launch passes, so one runner serves every target.

### The Python ↔ runner channel

idb is a subprocess CLI; XCUITest actuates from a test process resident on the Simulator, so the two need a channel for the run's lifetime. Reuse the existing loopback HTTP pattern (`bajutsu/network.py` binds a `ThreadingHTTPServer` on `127.0.0.1`; the app already talks to it via `BAJUTSU_COLLECTOR`), but in the **Python → runner** direction: the `BajutsuKit` runner starts a small `127.0.0.1:<port>` server inside its test method and stays resident; the Python `XcuitestDriver` sends actuation requests to it. The port is handed to the runner the same way — a launch/env argument — and is loopback-confined (never widening host exposure). The contract maps one-to-one onto the `Driver` Protocol so nothing above the driver changes:

| Driver call | request | response |
|---|---|---|
| `query()` | `GET /elements` | the normalized `Element[]` JSON (same `identifier`/`label`/`value`/`traits`/`frame` shape idb produces, so `find_all` / `resolve_unique` are unchanged) |
| `tap(sel)` | `POST /tap {elementId}` | ok / not-found — Python resolves the unique element from `query()` first (selection stays the determinism core), then names it; XCUITest taps **by identifier**, not coordinates |
| `pinch` / `rotate` | `POST /gesture {elementId, kind, scale\|radians}` | ok — the two-finger gestures idb raises `UnsupportedAction` for |
| `wait_for(sel)` | served by Python polling `GET /elements` (the orchestrator's condition wait), or the runner's native expectation behind the same bounded, sleep-free contract | ok/timeout |
| `screenshot(path)` | `GET /screenshot` | PNG bytes |

Errors map to the existing `Driver` exceptions (`ElementNotFound` / `AmbiguousSelector` stay Python-side because resolution stays Python-side). Keeping the minimal server in `BajutsuKit` (rather than a large external automation dependency) keeps the channel under the project's control — see *Alternatives considered*.

### Capabilities, doctor, and disclosure

`XcuitestDriver.capabilities()` (and `capabilities_for("xcuitest")`) returns `QUERY`, `ELEMENTS`, `SCREENSHOT`, **`SEMANTIC_TAP`**, **`CONDITION_WAIT`**, **`MULTI_TOUCH`** — the three beyond idb are what unlock identifier taps and pinch/rotate. `doctor --target` reports XCUITest availability (toolchain + the configured/buildable runner) beside idb's, and the run manifest already records the selected actuator, so a fallback to idb is disclosed, not silent.

### Validation

Split by what the fast gate can prove without a Simulator vs. what needs one:

- **Fast gate (no device).** Registry: `--backend ios` prefers `xcuitest` when available and falls back to `idb` when not (drive `select_actuator` with an injected availability function); `capabilities_for("xcuitest")` returns the richer set. Driver: build the actuation requests and parse responses against an **injected fake HTTP transport** (mirroring how idb tests inject a fake `run`), asserting `tap` resolves a unique element then addresses it by identifier, `pinch`/`rotate` emit the gesture request, and an ambiguous selector still fails before any request. No test puts the runner — or any LLM — on the `run`/CI gate.
- **On-device (e2e path).** The real `BajutsuKit` runner against a booted Simulator on the heavier `e2e.yml` path: a scenario that taps by identifier and runs a pinch/rotate that idb cannot, plus a fallback run on a host where XCUITest is unavailable, confirming graceful degradation to idb.

## Alternatives considered

- **Replace idb with XCUITest outright.** XCUITest is the richer actuator, but idb's headless, coordinate-based operation is valuable precisely in CI environments where a full XCUITest host is awkward. Keeping both in an ordered ladder gives the best of each: prefer XCUITest, fall back to idb, with the scenario unchanged.
- **Bolt the missing gestures onto idb.** We could try to synthesize pinch/rotate from idb's single-touch primitives. idb fundamentally exposes single-touch, so this would be an unreliable approximation of a multi-touch gesture — exactly the kind of fragile, non-deterministic behaviour the project avoids. A real multi-touch backend is the honest fix.
- **Adopt WebDriverAgent as the runner channel.** WebDriverAgent is a proven HTTP+XCTest server and would supply the Python↔runner channel off the shelf. Rejected for the first cut: it is a large dependency to vendor and maintain, and it pulls the project away from its thin-dependency stance (DESIGN §4), where backends are driven by shelling out to a focused tool. A minimal runner-side server in `BajutsuKit`, reusing the loopback pattern already in `network.py` / `BajutsuNet`, keeps the surface small and under the project's control; WebDriverAgent stays a fallback if the minimal server proves insufficient.
- **Route only specific gestures to XCUITest while idb stays the actuator.** Letting two drivers operate one device reintroduces the non-determinism the single-actuator rule (DESIGN §3.3 / §5) exists to prevent. The actuator is fixed once per run; capability gaps in *evidence* are handled separately by the read-only fallback design (BE-0020), but *actuation* stays with one backend.

## References

[DESIGN §5 / §3](../../../DESIGN.md), `bajutsu/backends.py`
