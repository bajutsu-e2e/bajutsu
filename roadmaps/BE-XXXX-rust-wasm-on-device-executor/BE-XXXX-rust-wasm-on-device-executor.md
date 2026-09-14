**English** · [日本語](BE-XXXX-rust-wasm-on-device-executor-ja.md)

# BE-XXXX — On-device scenario execution via a shared Rust/WASM core

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-rust-wasm-on-device-executor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md), [BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md), [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md), [BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md), [BE-0365](../BE-0365-in-app-control-channel/BE-0365-in-app-control-channel.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md)
defines a device-side step-execution protocol as the route past the 250–500 millisecond per-step
target [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md)
could not fully reach on its own: a `wait` or a screen-closed `assert` still pays one host-device round
trip per poll, so BE-0408 moves selector resolution and condition evaluation onto the device,
implemented as two independent ports — Swift for the iOS executor
([BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md))
and Kotlin for the Android one
([BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md)).
This item proposes an alternative implementation strategy for the same goal. Write the deterministic
judgment once, in Rust, and compile it both to a native Python extension the host calls (via PyO3) and
to WebAssembly (WASM) the two device runtimes embed, instead of maintaining Swift and Kotlin as two
separate reimplementations of the same rules. This item also widens the scope BE-0408 covers: the
shared core interprets a scenario's control flow (`if`, `for_each`, the alert-guard retry) on the
device too, not only its individual steps, so a whole scenario executes with no per-step round trip at
all.

## Motivation

[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md)
measured a `tap` step at 0.95–1.07 seconds on iOS and 3.25–3.32 seconds on Android against a 250–500
millisecond target, with the host-device round trip as the dominant cost: the host polls a condition
every 50 milliseconds, paying a full HTTP round trip each time. BE-0408 addresses this by moving
selector resolution and wait/assert evaluation onto the device, ported independently to Swift and
Kotlin.

That independent porting carries a determinism risk BE-0408 itself names. Three independent
implementations — the Python host's `find_all`/`resolve_unique`
([`bajutsu/common/drivers/base/_functions.py:152,274`](../../bajutsu/common/drivers/base/_functions.py)),
an iOS Swift port, and an Android Kotlin port — must keep returning the same verdict for the same
selector, forever. BE-0408 plans to catch drift by extending the driver conformance suite
([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)), which detects a
mismatch after the fact rather than removing the possibility of one. Every future change to the
selector-resolution rules pays a three-language tax: the person making the change must port it
correctly to Swift and Kotlin too, and BE-0408's own scope reduction (two backends, not a hypothetical
three) does not remove this recurring cost.

A second cost is how much round-trip reduction the staged rollout achieves before its last stage.
BE-0408 moves `wait` and `assert` evaluation to the device first, and only bundles a whole step
sequence into one `POST /scenario` call at its fourth and final stage. Until then, a cross-step
judgment — the alert-guard's one-time retry after a system alert blocks a step — stays a host
responsibility even at stage 4, because BE-0408 scopes control flow itself to the host. Handing the
whole scenario to the runtime from the start, control flow included, collapses the host-device round
trip to the phase boundaries and the interval-evidence start/stop signals alone: the same end state
BE-0408's stage 4 targets, reached without the intermediate stages.

A later reader can check this item's central claim without new device hardware. Build the Rust crate
this item defines for both the PyO3 target and the `wasm32-unknown-unknown` target from the same
source, and confirm the two targets return identical results for the driver conformance suite's
fixture set, continuously in CI rather than once. That single, continuously-checked crate — not a
structural guarantee the shared source alone provides — is what replaces BE-0408's three separately
maintained implementations.

## Detailed design

### What moves, and what does not

The host keeps exactly the three responsibilities BE-0408 already assigns it, unchanged:

- Deciding pass/fail. The device returns raw evidence — a resolved element's attributes, an
  assertion's observed value — not a verdict, and the host recomputes `ok` from that evidence through
  the same shared core (its PyO3 binding), so the device's own `ok` is never trusted directly. Because
  this item also moves scenario expansion onto the device, recomputing each *reported* step's verdict
  is not enough on its own: the host expands the scenario locally too, through the same binding, and
  requires the reported step sequence to match. A device-side control-flow bug — a wrong `if` branch, a
  `for_each` that iterates zero times — would otherwise let every step the device does report recompute
  to `ok=true`, so the run would go green with the skipped steps never mentioned.
- Receiving evidence for the report. `manifest.json` and the HTML report are written from the same
  `StepOutcome` shape as today.
- Interval evidence. Starting and stopping a video or device-log capture needs host-side OS process
  control (`simctl io recordVideo`, for example) the device cannot invoke on its own, so the host
  still decides when each interval capture starts and stops.

Everything else moves into the shared crate: scenario expansion (`if`/`for_each`/component
expansion/`vars.*` interpolation, today in
[`bajutsu/common/scenario/expand.py`](../../bajutsu/common/scenario/expand.py)), selector resolution,
the act/wait/verify step loop
([`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py)),
the alert-guard's one-time retry, and evidence-rule firing
([`bajutsu/common/orchestrator/evidence_rules.py`](../../bajutsu/common/orchestrator/evidence_rules.py)).
BE-0408 keeps scenario expansion and cross-step judgment host-side because a second, independent
device-side port of that logic would only add a fourth place selector semantics could drift. A single
shared binary removes that reason, so this item does not inherit the same boundary.

### One crate, two build targets

A new crate, `rust/bajutsu_core/`, implements scenario expansion, selector resolution, the step loop,
and evidence-rule firing, matching today's Python behavior rule for rule. It builds twice from the same
source.

- **A PyO3 binding**, a Python extension module the host calls to recompute a step's verdict from the
  device's raw evidence. The host does not use this binding to drive Playwright or `FakeDriver`
  directly; those backends keep running through the existing `run_scenario`
  ([`bajutsu/common/orchestrator/loop/_functions.py:571`](../../bajutsu/common/orchestrator/loop/_functions.py)).
- **A `wasm32-unknown-unknown` binary**, embedded in the iOS XCTest runner process (`BajutsuKit`) and
  the Android resident UI Automator server (`BajutsuAndroidUIAutomatorServer`). Neither embedding
  calls an OS API directly; each platform supplies the crate's `Driver` trait as WASM host-function
  imports (tap, type, swipe, tree read, screenshot).

Compiling both targets from the same crate does not by itself guarantee they return the same result.
The PyO3 binding targets a native architecture and the WASM binary targets
`wasm32-unknown-unknown`, and target-dependent differences — `usize` width (64-bit vs. 32-bit),
floating-point rounding and stringification, `HashMap` iteration order — can still separate them
wherever order affects a verdict, such as `resolve_unique`'s duplicate-collapsing step
([`bajutsu/common/drivers/base/_functions.py:274`](../../bajutsu/common/drivers/base/_functions.py)).
The crate confines any order-sensitive logic to a deterministic container (`BTreeMap`, for example) and
the driver conformance suite
([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) runs continuously
in CI against both targets, checking that they return identical results for the same fixtures — the
same fixtures it already uses to verify the crate against `FakeDriver` and the other backends. That
cross-target check, not the shared source alone, is what keeps the two targets from drifting apart the
way BE-0408's three independent implementations could.

### Host-side change

`run_scenario`'s entry point gains one branch. When the target `Driver` declares a new `Capability`
(added to [`bajutsu/common/drivers/base/capability.py`](../../bajutsu/common/drivers/base/capability.py)),
the host sends the whole scenario — `before`, `steps`, `expect`, and `after` — to the runtime in a
single request instead of driving the existing per-step loop. A driver that does not declare the
capability sees no change at all: Playwright, `FakeDriver`, and an xcuitest/adb target that has not
built the on-device executor keep running through today's `_run_step_body`
([`bajutsu/common/orchestrator/loop/_functions.py:302`](../../bajutsu/common/orchestrator/loop/_functions.py))
unmodified.

The runtime streams one result per step back over the same connection, as newline-delimited JSON, as
each step finishes, reusing the chunked-transfer approach BE-0409 already specifies for its own
`POST /scenario` endpoint. The host reads each line, converts it to the existing `StepOutcome` shape,
and feeds it to the same report-writing, CLI live-output, and `serve` log-bus code that consumes a
`StepOutcome` today, so neither the CLI's live output nor the Web UI's Replay tab changes in
appearance. The host recomputes `ok` from the raw evidence in each line through the PyO3 binding before
treating a step as passed.

### iOS and Android runtimes

Both platforms add a `POST /scenario` endpoint, the same name BE-0409 already proposes, so the two
designs can share whatever gets built around that surface regardless of which core implementation is
chosen. `APIHandler`
([`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift))
loads the crate's WASM binary into a WASM runtime running on the same main thread every other XCTest
operation already serializes onto (BE-0323's non-reentrancy), and supplies `tap`/`snapshot`/`type`/
`swipe`/screenshot as host functions backed by
[`XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift). Which
Swift WASM runtime to embed — wasmtime's Swift bindings or the pure-Swift WasmKit — is an open
feasibility question this item's first work unit answers before any other iOS work proceeds.

That same `operations` queue is what `serialized(_:)` funnels every other `APIHandler` call through,
via `DispatchQueue.main.sync`
([`APIHandler.swift:51,354-366`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift)). A
whole-scenario `POST /scenario` holds that queue for its entire duration, so routing
`POST /scenario/cancel` or the interval-evidence start/stop signal through the same queue would leave
each waiting behind the very call it exists to interrupt. Only `health` and `setInterruptionPolicy`
bypass the queue today; the two new endpoints need the same bypass, stated explicitly rather than
assumed.

The Android resident UI Automator server embeds a JVM-hosted WASM runtime. Chicory, a pure-JVM
interpreter that avoids a native-library signing step an instrumentation test target would otherwise
need, is one candidate. The server supplies the same host functions backed by its existing
`UiAutomation` session and `AccessibilityNodeInfo` access. The fixed `POSTDATE_BUDGET_MS` wait
([`ResidentServerTest.kt:768`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt))
is replaced by the crate's event-driven wait judgment, the same replacement
[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md)
already specifies.

### Progress streaming, cancellation, and crash recovery

Progress reaches the CLI and the Web UI through the streaming connection above; this item introduces no
new transport. Cancellation adds a `POST /scenario/cancel` sibling endpoint, guarded by the same
per-run authentication token every channel in this codebase already uses, and — on iOS — routed outside
the serialized `operations` queue as described above, so a scenario already in progress does not block
it. It is polled by the crate's step loop at each step boundary — the same reaction granularity
[BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md) already
relies on, so a `SIGTERM` or the Web UI's stop button still produces `RunResult(ok=False,
failure="cancelled")`. Backend-crash recovery is unchanged: a runner process dying mid-scenario still
surfaces to the host as a connection failure the existing `BackendCrashError` path already handles
([BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md),
BE-0407's `recovery.py`).

## Alternatives considered

- **Port independently to Swift and Kotlin, as BE-0408 through BE-0410 already propose.** The smaller
  initial implementation cost is real: nobody has to stand up a Rust toolchain or a WASM runtime on
  either platform first. This item rejects that alternative because the cost it avoids up front
  returns as a standing tax: every later change to selector-resolution semantics must be ported to
  Swift and to Kotlin as well, and reading the host implementation alone never reveals a mistake in
  either port — only a conformance-suite run does. This item spends a one-time toolchain cost to
  remove that recurring one.
- **Compile CPython itself to WASM (Pyodide or similar) instead of writing a new Rust core.** Rejected.
  A CPython WASM runtime runs tens of megabytes, too heavy to embed in an XCTest runner process or an
  Android instrumentation server, and CPython's global interpreter lock and threading model conflict
  with `APIHandler`'s existing single-main-thread serialization (BE-0323).
- **Generate the Swift and Kotlin ports from one declarative specification instead of sharing a
  compiled binary.** Rejected. Code generation avoids adding a WASM runtime, but the generated Swift
  and Kotlin remain two independent native artifacts at run time. Equivalence then rests on trusting
  the generator rather than on running the same binary logic, and the generator itself becomes a third
  artifact to maintain, at roughly the cost this item spends embedding a WASM runtime instead.
- **Replace `run_scenario` for every backend, Playwright and `FakeDriver` included, with the shared
  core.** Rejected for this item's scope. Unifying every backend's execution path would pull in every
  existing backend's test surface before this item's own question, whether the on-device round-trip
  reduction holds up, has been answered on real hardware. A later item can widen the replacement once
  this one's iOS and Android results are in.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Verify WASM runtime feasibility on iOS (wasmtime's Swift bindings vs. WasmKit) and Android
  (Chicory or another pure-JVM interpreter) before any other work unit begins.
- [ ] Port scenario expansion (`expand_components`/`expand_data`/`apply_setups`) to the new
  `rust/bajutsu_core/` crate, verified against existing Python fixtures by golden-file comparison.
- [ ] Port selector resolution (`find_all`/`resolve_unique`) into the crate, verified against the
  driver conformance suite's fixtures
  ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)).
- [ ] Port the step loop, the alert-guard one-time retry, and evidence-rule firing into the crate,
  verified against a Rust-side fake driver.
- [ ] Build the PyO3 binding and the new `Capability`, wiring the host's verdict-recomputation path —
  including the local re-expansion check against the device's reported step sequence — into
  `run_scenario` without changing behavior for any driver that does not declare the capability.
- [ ] Build the WASM target and wire it into the iOS `APIHandler`'s new `POST /scenario` endpoint,
  running one simple scenario end to end.
- [ ] Wire newline-delimited-JSON progress streaming into the CLI live output and the `serve` log bus.
- [ ] Add `POST /scenario/cancel`, routed outside the serialized `operations` queue so it reaches the
  runner while a scenario is still in progress, and verify it against
  [BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md)'s existing
  cancellation behavior.
- [ ] Repeat the WASM integration for the Android resident UI Automator server.
- [ ] Trace `controls.yaml` (the same yardstick BE-0407/BE-0409/BE-0410 use) through the new path on
  both platforms, and record the result against BE-0409/BE-0410's own per-tap targets (200–350ms on
  iOS, 150–300ms on Android).
- [ ] Decide, from the traced results, whether this item's approach or BE-0408 through BE-0410's should
  ship, and update both items' `Status` accordingly.
- [ ] Once the `roadmap-id` workflow allocates this item's real `BE-NNNN` on `main`, backfill a
  reciprocal `Related` link from BE-0407, BE-0408, BE-0409, and BE-0410.

## References

[BE-0407 — Cut step latency by deduplicating evidence reads and tuning driver internals](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md),
[BE-0408 — Add a device-side step-execution protocol](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md),
[BE-0409 — iOS on-device step executor](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md),
[BE-0410 — Android on-device step executor](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[`docs/specs/rust-wasm-on-device-executor.md`](../../docs/specs/rust-wasm-on-device-executor.md) — the
full design write-up this item summarizes,
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py),
[`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py)
