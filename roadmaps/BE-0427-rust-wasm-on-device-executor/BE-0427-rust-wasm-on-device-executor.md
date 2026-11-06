**English** · [日本語](BE-0427-rust-wasm-on-device-executor-ja.md)

# BE-0427 — On-device scenario execution via a shared Rust/WASM core

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0427](BE-0427-rust-wasm-on-device-executor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0427") |
| Topic | Platform support |
| Related | [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md), [BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md), [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md), [BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md), [BE-0365](../BE-0365-in-app-control-channel/BE-0365-in-app-control-channel.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md)
defines a device-side step-execution protocol. The protocol is the route past the 250–500 millisecond
per-step target that
[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md)
could not fully reach on its own. A `wait` or a screen-closed `assert` still pays one host-device
round trip per poll. BE-0408 thus moves selector resolution and condition evaluation onto the
device. It implements them as two independent ports: Swift for the iOS executor
([BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md))
and Kotlin for the Android one
([BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md)).

This item proposes an alternative implementation strategy for the same goal. We write the
deterministic judgment once, in Rust. We then compile it to two targets: a native Python extension
that the host calls (via PyO3) and WebAssembly (WASM) that the two device runtimes embed. The strategy
replaces Swift and Kotlin as two separate reimplementations of the same rules.

This item also widens the scope BE-0408 covers. The shared core interprets a scenario's control flow
(`if`, `for_each`, the alert-guard retry) on the device too, not merely its individual steps. A whole
scenario then executes with no per-step round trip at all.

## Motivation

[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md)
measured a `tap` step at 0.95–1.07 seconds on iOS and 3.25–3.32 seconds on Android, against a target
of 250–500 milliseconds. The dominant cost is the host-device round trip. The host polls a condition
every 50 milliseconds and pays a full HTTP round trip each time. BE-0408 addresses this by moving
selector resolution and wait/assert evaluation onto the device, ported independently to Swift and
Kotlin.

That independent porting carries a determinism risk that BE-0408 itself names. Three independent
implementations must keep returning the same verdict for the same selector, forever:

- the Python host's `find_all`/`resolve_unique`
  ([`bajutsu/common/drivers/base/_functions.py:152,274`](../../bajutsu/common/drivers/base/_functions.py))
- an iOS Swift port
- an Android Kotlin port

BE-0408 plans to catch drift by extending the driver conformance suite
([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)). That suite
detects a mismatch after the fact. It does not remove the possibility of one. Every future change to
the selector-resolution rules pays a three-language tax: the person making the change must port it
correctly to Swift and Kotlin too. BE-0408's own scope reduction (two backends, not a hypothetical
three) does not remove this recurring cost.

A second cost is how much round-trip reduction the staged rollout achieves before its last stage.
BE-0408 first moves `wait` and `assert` evaluation to the device. Only at its fourth and final stage
does it bundle a whole step sequence into one `POST /scenario` call. Until then, a cross-step
judgment stays a host responsibility, even at stage 4. The alert-guard's one-time retry after a
system alert blocks a step is one such judgment. BE-0408 scopes control flow itself to the host.

Handing the whole scenario to the runtime from the start, control flow included, collapses the
host-device round trip to the phase boundaries and the interval-evidence start/stop signals alone.
This is the end state that BE-0408's stage 4 targets, reached without the intermediate stages.

A later reader can check this item's central claim without new device hardware:

1. Build the Rust crate this item defines for both the PyO3 target and the `wasm32-unknown-unknown`
   target from the same source.
2. Confirm that the two targets return identical results for the driver conformance suite's fixture
   set. The confirmation runs continuously in continuous integration (CI) rather than once.

That single, continuously checked crate replaces BE-0408's three separately maintained
implementations. The shared source alone does not provide a structural guarantee of equivalence.

## Detailed design

### What moves, and what does not

The host keeps precisely the three responsibilities BE-0408 already assigns it, unchanged.

- **Deciding pass/fail.** The device returns raw evidence, not a verdict: a resolved element's
  attributes, or an assertion's observed value. The host recomputes `ok` from that evidence through
  the same shared core (its PyO3 binding). The host never trusts the device's own `ok` directly.

  This item also moves scenario expansion onto the device. Recomputing each *reported* step's verdict
  is thus not enough on its own. The host expands the scenario locally too, through the same
  binding, and requires the reported step sequence to match. A device-side control-flow bug, such as a
  wrong `if` branch or a `for_each` that iterates zero times, would otherwise go unnoticed. Every
  step the device does report would recompute to `ok=true`, and the run would go green with the
  skipped steps never mentioned.
- **Receiving evidence for the report.** `manifest.json` and the HTML report are written from the
  same `StepOutcome` shape as today.
- **Interval evidence.** Starting and stopping a video or device-log capture needs host-side
  operating system (OS) process control (`simctl io recordVideo`, for example), which the device
  cannot invoke on its own. The host thus still decides when each interval capture starts and
  stops.

Everything else moves into the shared crate:

- scenario expansion (`if`/`for_each`/component expansion/`vars.*` interpolation, today in
  [`bajutsu/common/scenario/expand.py`](../../bajutsu/common/scenario/expand.py))
- selector resolution
- the act/wait/verify step loop
  ([`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py))
- the alert-guard's one-time retry
- evidence-rule firing
  ([`bajutsu/common/orchestrator/evidence_rules.py`](../../bajutsu/common/orchestrator/evidence_rules.py))

BE-0408 keeps scenario expansion and cross-step judgment host-side. A second, independent device-side
port of that logic would only add a fourth place where selector semantics could drift. A single
shared binary removes that reason, so this item does not inherit the same boundary.

### One crate, two build targets

A new crate, `rust/bajutsu_core/`, implements scenario expansion, selector resolution, the step loop,
and evidence-rule firing. It matches today's Python behavior rule for rule. It builds twice from the
same source.

- **A PyO3 binding**, a Python extension module that the host calls to recompute a step's verdict
  from the device's raw evidence. The host does not use this binding to drive Playwright or
  `FakeDriver` directly. Those backends keep running through the existing `run_scenario`. See [`bajutsu/common/orchestrator/loop/_functions.py:571`](../../bajutsu/common/orchestrator/loop/_functions.py).
- **A `wasm32-unknown-unknown` binary**, embedded in the iOS XCTest runner process (`BajutsuKit`) and
  the Android resident UI Automator server (`BajutsuAndroidUIAutomatorServer`). Neither embedding
  calls an OS API directly. Each platform supplies the crate's `Driver` trait as WASM host-function
  imports (tap, type, swipe, tree read, screenshot).

Compiling both targets from the same crate does not by itself guarantee that they return the same
result. The PyO3 binding targets a native architecture, and the WASM binary targets
`wasm32-unknown-unknown`. Target-dependent differences can still separate them wherever order affects
a verdict, such as `resolve_unique`'s duplicate-collapsing step. See [`bajutsu/common/drivers/base/_functions.py:274`](../../bajutsu/common/drivers/base/_functions.py).
Those differences include:

- `usize` width (64-bit vs. 32-bit)
- floating-point rounding and stringification
- `HashMap` iteration order

The crate keeps two safeguards against such drift:

- It confines any order-sensitive logic to a deterministic container (`BTreeMap`, for example).
- The driver conformance suite
  ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) runs
  continuously in CI against both targets. It checks that they return identical results for the same
  fixtures, which it already uses to verify the crate against `FakeDriver` and the other backends.

That cross-target check, not the shared source alone, keeps the two targets from drifting apart the
way BE-0408's three independent implementations could.

### Host-side change

`run_scenario`'s entry point gains one branch. The target `Driver` can declare a new `Capability`
(added to [`bajutsu/common/drivers/base/capability.py`](../../bajutsu/common/drivers/base/capability.py)).
When it does, the host sends the whole scenario to the runtime in a single request, instead of driving
the existing per-step loop. The request carries `before`, `steps`, `expect`, and `after`.

A driver that does not declare the capability sees no change at all. Playwright, `FakeDriver`, and an
xcuitest/adb target that has not built the on-device executor keep running through today's
`_run_step_body`
([`bajutsu/common/orchestrator/loop/_functions.py:302`](../../bajutsu/common/orchestrator/loop/_functions.py))
unmodified.

The runtime streams one result per step back over the same connection, as each step finishes. The
stream is newline-delimited JSON (JavaScript Object Notation). It reuses the chunked-transfer approach
that BE-0409 already specifies for its own `POST /scenario` endpoint.

The host reads each line and converts it to the existing `StepOutcome` shape. It then feeds the result
to the same report-writing, CLI live-output, and `serve` log-bus code that consumes a `StepOutcome`
today. Neither the CLI's live output nor the Web UI's Replay tab changes in appearance. Before the
host treats a step as passed, it recomputes `ok` from the raw evidence in that step's line through the
PyO3 binding.

### iOS and Android runtimes

Both platforms add a `POST /scenario` endpoint, the same name BE-0409 already proposes. The two
designs can then share whatever gets built around that surface, whichever core implementation is
chosen.

On iOS, `APIHandler`
([`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift))
loads the crate's WASM binary into a WASM runtime. The runtime runs on the same main thread onto which
every other XCTest operation already serializes (BE-0323's non-reentrancy). `APIHandler` supplies
`tap`/`snapshot`/`type`/`swipe`/screenshot as host functions backed by
[`XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift).

Which Swift WASM runtime to embed is an open feasibility question: wasmtime's Swift bindings or the
pure-Swift WasmKit. This item's first work unit answers it before any other iOS work proceeds.

The `operations` queue is what `serialized(_:)` funnels every other `APIHandler` call through, via
`DispatchQueue.main.sync`. See [`APIHandler.swift:51,354-366`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift). A
whole-scenario `POST /scenario` holds that queue for its entire duration. Routing `POST
/scenario/cancel` or the interval-evidence start/stop signal through the same queue would leave each
waiting behind the very call it exists to interrupt. Only `health` and `setInterruptionPolicy` bypass
the queue today. The two new endpoints need the same bypass, stated explicitly rather than assumed.

The Android resident UI Automator server embeds a WASM runtime hosted on the Java Virtual Machine
(JVM). Chicory is one candidate. It is a pure-JVM interpreter that avoids a native-library signing step,
which an instrumentation test target would otherwise need. The server supplies the same host
functions, backed by its existing `UiAutomation` session and `AccessibilityNodeInfo` access.

The crate's event-driven wait judgment replaces the fixed `POSTDATE_BUDGET_MS` wait. See [`ResidentServerTest.kt:768`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt).
[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md)
already specifies the same replacement.

### Progress streaming, cancellation, and crash recovery

Progress reaches the CLI and the Web UI through the streaming connection above. This item introduces
no new transport.

Cancellation adds a `POST /scenario/cancel` sibling endpoint. The same per-run authentication token
that every channel in this codebase already uses guards the endpoint. On iOS, the endpoint is routed
outside the serialized `operations` queue, as described above, so a scenario already in progress does
not block it.

The crate's step loop polls for cancellation at each step boundary. This is the same reaction
granularity that
[BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md) already relies on. A
`SIGTERM` or the Web UI's stop button thus still produces `RunResult(ok=False,
failure="cancelled")`.

Backend-crash recovery is unchanged. A runner process dying mid-scenario still surfaces to the host as
a connection failure. The existing `BackendCrashError` path already handles that failure
([BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md),
BE-0407's `recovery.py`).

## Alternatives considered

- **Port independently to Swift and Kotlin, as BE-0408 through BE-0410 already propose.** The smaller
  initial implementation cost is real: nobody has to stand up a Rust toolchain or a WASM runtime on
  either platform first. This item rejects the alternative because the cost it avoids up front
  returns as a standing tax. Every later change to selector-resolution semantics must be ported to
  Swift and to Kotlin as well. Reading the host implementation alone never reveals a mistake in either
  port, and only a conformance-suite run does. This item spends a one-time toolchain cost to remove
  that recurring one.
- **Compile CPython itself to WASM (Pyodide or similar) instead of writing a new Rust core.**
  Rejected. A CPython WASM runtime runs tens of megabytes, too heavy to embed in an XCTest runner
  process or an Android instrumentation server. CPython's global interpreter lock and threading model
  also conflict with `APIHandler`'s existing single-main-thread serialization (BE-0323).
- **Generate the Swift and Kotlin ports from one declarative specification instead of sharing a
  compiled binary.** Rejected. Code generation avoids adding a WASM runtime, but the generated Swift
  and Kotlin remain two independent native artifacts at run time. Equivalence then rests on trusting
  the generator rather than on running the same binary logic. The generator itself becomes a third
  artifact to maintain, at roughly the cost this item spends embedding a WASM runtime instead.
- **Replace `run_scenario` for every backend, Playwright and `FakeDriver` included, with the shared
  core.** Rejected for this item's scope. Unifying every backend's execution path would pull in every
  existing backend's test surface before this item's own question has been answered on real hardware.
  That question is whether the on-device round-trip reduction holds up. A later item can widen the
  replacement once this one's iOS and Android results are in.

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
