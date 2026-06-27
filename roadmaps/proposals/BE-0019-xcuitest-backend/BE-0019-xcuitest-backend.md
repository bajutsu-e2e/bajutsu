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
- **Driving the runner.** Unlike idb, which is a subprocess CLI, XCUITest actuates from inside a runner that lives on the Simulator, so Python and that runner need a channel. The runner stays resident for the whole run and serves a small loopback HTTP endpoint; the Python driver sends `query` / `tap` / gesture requests to it. This reuses the loopback pattern Bajutsu already has — `network.py` binds a `ThreadingHTTPServer` on `127.0.0.1` and the launch env injects its address into the app under test (`BajutsuKit`'s `BajutsuNet`); the same loopback, in the Python→runner direction, carries the actuation requests. The minimal runner-side server is built in `BajutsuKit` rather than adopting a large external dependency, keeping the channel under the project's control (see *Alternatives considered*). How the runner is **delivered and built** — a prebuilt `.xctestrun` named under `apps.<name>`, versus built on demand with `xcodebuild test` — is left open for the implementation; both fit the per-app model, and the choice is the first thing to settle when work starts.
- **Richer capabilities, same contract.** The XCUITest driver's `capabilities()` returns `semanticTap`, native `conditionWait`, and `multiTouch` in addition to what idb offers. Because selection stays the determinism core, a `tap` still resolves to exactly one element — XCUITest just actuates it by identifier rather than by frame-centre coordinates, removing the coordinate round-trip. `pinch` / `rotate`, which raise `UnsupportedAction` on idb, become directly executable.
- **Determinism is preserved.** Even where XCUITest offers a native condition-wait, the orchestrator's waits remain condition waits with no fixed sleeps, and an ambiguous selector still fails immediately — the new capabilities widen what can be expressed, they do not relax the rules. Crucially, XCUITest is used only as a deterministic actuator at `run` time; no LLM enters the Tier-2 gate. (This is distinct from `codegen`, which structurally maps a finished scenario to an XCUITest test source; that path is unaffected.)
- **App-agnostic, per-app where needed.** The driver itself is app-independent. Anything an app must provide to be driven by XCUITest (e.g. a test host or launch arguments) lives under `apps.<name>`, alongside the existing per-app settings, so the tool and runner stay unchanged across apps. `doctor --app` reports XCUITest availability the same way it reports idb's.
- **Fallback intact.** With idb kept second in the ladder, environments where XCUITest cannot run (headless CI without the necessary host) degrade gracefully to coordinate-based idb. A run records which actuator was selected, so the manifest states whether the richer or the fallback path was taken — consistent with the existing degradation-disclosure rule.

## Alternatives considered

- **Replace idb with XCUITest outright.** XCUITest is the richer actuator, but idb's headless, coordinate-based operation is valuable precisely in CI environments where a full XCUITest host is awkward. Keeping both in an ordered ladder gives the best of each: prefer XCUITest, fall back to idb, with the scenario unchanged.
- **Bolt the missing gestures onto idb.** We could try to synthesize pinch/rotate from idb's single-touch primitives. idb fundamentally exposes single-touch, so this would be an unreliable approximation of a multi-touch gesture — exactly the kind of fragile, non-deterministic behaviour the project avoids. A real multi-touch backend is the honest fix.
- **Adopt WebDriverAgent as the runner channel.** WebDriverAgent is a proven HTTP+XCTest server and would supply the Python↔runner channel off the shelf. Rejected for the first cut: it is a large dependency to vendor and maintain, and it pulls the project away from its thin-dependency stance (DESIGN §4), where backends are driven by shelling out to a focused tool. A minimal runner-side server in `BajutsuKit`, reusing the loopback pattern already in `network.py` / `BajutsuNet`, keeps the surface small and under the project's control; WebDriverAgent stays a fallback if the minimal server proves insufficient.
- **Route only specific gestures to XCUITest while idb stays the actuator.** Letting two drivers operate one device reintroduces the non-determinism the single-actuator rule (DESIGN §3.3 / §5) exists to prevent. The actuator is fixed once per run; capability gaps in *evidence* are handled separately by the read-only fallback design (BE-0020), but *actuation* stays with one backend.

## References

[DESIGN §5 / §3](../../../DESIGN.md), `bajutsu/backends.py`
