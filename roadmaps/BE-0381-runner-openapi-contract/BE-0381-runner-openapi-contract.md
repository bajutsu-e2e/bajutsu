**English** · [日本語](BE-0381-runner-openapi-contract-ja.md)

# BE-0381 — Generate the XCUITest runner's HTTP server from an OpenAPI contract instead of hand-rolling it

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0381](BE-0381-runner-openapi-contract.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0381") |
| Implementing PR | [#1606](https://github.com/bajutsu-e2e/bajutsu/pull/1606) (Units 1–3), [#1686](https://github.com/bajutsu-e2e/bajutsu/pull/1686) (Unit 4) |
| Topic | Platform support |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md), [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md), [BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) |
<!-- /BE-METADATA -->

## Introduction

BajutsuKit's XCUITest runner (`BajutsuKit/Sources/BajutsuRunner`) answers every query and actuation
from the Python driver through a small HTTP server. The runner hand-rolls that server from raw BSD
sockets. The hand-rolled layer has four parts:

- a byte-by-byte request parser
- its own JSON encoding
- a routing switch over sixteen paths
- its own connection-lifecycle management

This item replaces the hand-rolled layer with one generated from a checked-in OpenAPI contract.

- An `openapi.yaml` document becomes the single source of truth for the HTTP surface of the runner.
- Swift OpenAPI Generator produces the Swift types and the server protocol from that document.
- A proof of concept (PoC) decides which HTTP framework fills the transport role beneath the protocol.

Adopting the OpenAPI contract and its generated types is fixed. The framework that carries the
contract is open, and the PoC of this item measures the choice instead of assuming it. Two
candidates compete:

- Hummingbird, brought over from server-side Swift, has an official OpenAPI adapter.
- FlyingFox is a zero-dependency socket library built for Apple platforms. It has no such adapter
  today.

The PoC confirmed that framing only in part, and the record below says so. On size alone, the
measurement favored FlyingFox and would have ruled Hummingbird out. Hummingbird failed on a platform
floor, not on a physical limit. The maintainer chose to raise the floor to iOS 18 and to adopt
Hummingbird for its upstream-maintained adapter.

The measurement also showed that the OpenAPI layer carries a larger cost than the share of either
transport. That finding reopens the "fixed" half of the framing above. *Measured result* under Unit
2 gives the figures and the decision taken against them.

The runner binds only to `127.0.0.1`. It talks only to the Python driver on the same host
(`bajutsu/drivers/xcuitest.py`), and it is never exposed to another device over a local-area network
(LAN). This item adapts a circulated migration proposal for the embedded HTTP server of an iOS app.
That proposal spends several sections on LAN exposure, Bonjour service discovery, and bearer-token
authentication. Those sections describe a different exposure surface than the one this runner has,
so this item scopes them out instead of adopting them.

## Motivation

A real server framework would supply every low-level HTTP responsibility. Today the runner maintains
each of them by hand.

- `HTTPServer.swift` reads a request one byte at a time into a header buffer. The buffer is capped
  at a hardcoded 8,192 bytes.
- `HTTPServer.swift` frames the response body with a manually written `Content-Length` header.
- `HTTPServer.swift` writes its own status text for four hardcoded status codes.
- `Router.swift` dispatches on a `switch` over the method and path of the request.
- `Router.swift` builds every JSON reply by hand, as a `[String: Any]` dictionary passed to
  `JSONSerialization`.

None of this work is Bajutsu's own value. It is protocol-following and error-handling work that
carries no product-specific meaning. It still needs continuous upkeep as the surface it serves grows.

The history of this repository shows that the upkeep is real, not hypothetical.

- [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
  raised the listen backlog from 1 to 16 and added an eight-handler semaphore. A burst of driver and
  health-poll connections had exhausted the original backlog outright.
- [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
  added a main-thread serialization lock (`actuationLock`). XCUITest is not re-entrant, and two
  concurrent XCUITest operations reaching the main thread at once re-enter it and abort the test
  host.
- [BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) is a still-open
  proposal to declare an explicit quality of service (QoS) on the dispatch queues of the server.
  Neither queue declares one today. The correct priority is a coincidence of which thread happens to
  start the accept loop.

An HTTP framework settles the first and third concerns by construction. Those concerns are
connection backpressure and scheduling priority. The second concern is different. Serializing
XCUITest operations is an invariant specific to this runner, and it survives any transport (Unit 3
below carries it over).

What BE-0323 shows instead is that the concurrency semantics of the hand-rolled transport are
implicit. The interaction that crashed the host existed because nothing documented what the
connection layer guarantees. A framework at least hosts the same invariant on a defined, documented
concurrency model.

The same hand-maintenance shows up as a second, distinct cost. No compiler checks the contract
between the Swift server and the Python driver.

- `Router.swift` writes reply bodies as literal string values: `"ok"`, `"stale"`, `"not-found"`,
  `"not-hittable"`, and `"value-not-found"`.
- `bajutsu/drivers/xcuitest.py` matches the same five literals against its own constants (`_OK`,
  `_STALE`, `_NOT_FOUND`, `_NOT_HITTABLE`, `_VALUE_NOT_FOUND`). The Python side declares them
  independently.

A change to the literal on either side compiles cleanly, even when it silently stops matching the
other side. Nothing shared enforces agreement. That is the API-drift risk that a contract without a
source of truth invites.

[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) introduced this channel. It chose
deliberately not to pay for a framework. It built "the minimal runner-side server... in
`BajutsuKit` rather than adopting a large external dependency, keeping the channel under the
project's control." That choice was right for the surface it served then: a handful of endpoints,
newly introduced to unblock XCUITest as a second actuator.

The choice is worth revisiting now, for two reasons:

- The surface has tripled to sixteen endpoints. They span handle-based element addressing
  ([BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md)),
  multi-touch gestures, SpringBoard system-alert routing, and screenshots.
- Three further items have each patched a low-level transport concern that the original design left
  implicit.

"Keeping the channel under the project's control" does not require hand-rolling sockets. This item
keeps the app-specific `APIHandler` layer entirely inside `BajutsuKit`, under the control of this
project. It delegates the socket, parsing, and framing layer beneath to a library maintained outside
this project. [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) drew the same
separation. It kept the driver-facing contract in-house. It did not hand-roll the actuation engine
beneath it (XCUITest).

## Detailed design

The design keeps the entire public interface of `RunnerServer` unchanged.

- `BajutsuKit/Runner/Sources/RunnerUITest.swift` consumes the interface through
  `startFromEnvironment()` / `stop()` and the forwarded-launch statics.
- The Python environment (`bajutsu/platform_lifecycle/environments/xcuitest.py`) couples to the
  runner only through environment variables such as `BAJUTSU_RUNNER_PORT`.

Every change below is internal to `BajutsuKit/Sources/BajutsuRunner`. The work splits into five units.
Units 1 and 2 must land in order, because the PoC in Unit 2 measures candidates built against the
contract that Unit 1 defines. Units 3 through 5 are the endpoint-migration work that follows once
Unit 2 picks a transport.

### Unit 1 — Author the OpenAPI contract, independent of transport

Write `openapi.yaml`. It describes the sixteen existing paths and methods of the runner and serves as
the single source of truth for the HTTP surface of the runner. The sixteen break down as follows:

- reads: `GET /health`, `GET /elements`, `GET /screen`, and `GET /screenshot`
- actuation: `POST /tap`, `POST /isHittable`, `POST /gesture`, `POST /swipe`, `POST /scroll`, and
  `POST /setPickerValue`
- text editing: `POST /type`, `POST /deleteText`, `POST /selectAll`, and `POST /copy`
- system alerts: `POST /systemAlert/query` and `POST /systemAlert/tap`

`POST /setPickerValue` and its `value-not-found` status
([BE-0356](../BE-0356-xcuitest-picker-wheel-adjust/BE-0356-xcuitest-picker-wheel-adjust.md)) reached
`main` while this item was in flight, and this item absorbed them. The merge was textually clean and
still broke the build. That is the useful part of the story. `main` added a `TapResult` case, and this
branch added the first exhaustive `switch` over that enum. Only the compiler saw the conflict.

The schema must preserve the exact wire values that the Python driver already matches:

- the five status strings from Motivation
- the element shape (`identifier`, `label`, `value`, `traits`, `frame`, `handle`) that `/elements`
  and `/systemAlert/query` already share

The contract is generated from today's behavior, not from a redesign. That choice keeps the
endpoint-by-endpoint migration of Unit 4 a behavior-preserving port, not a rewrite.

Add the build plugin of Swift OpenAPI Generator to a `BajutsuRunner`-adjacent target. `Types.swift`
and a server protocol then regenerate from `openapi.yaml` at every build. They are not checked in,
so they cannot drift from the contract that produced them.

Swift OpenAPI Generator and Swift OpenAPI Runtime both declare `swift-tools-version:6.1`. That is
above the current `5.9` of `BajutsuKit/Package.swift`. The declaration raises the Swift toolchain
floor for building `BajutsuKit` to 6.1, whichever transport Unit 2 picks. The floor comes from the
OpenAPI tooling itself, not from Hummingbird or FlyingFox. It does not by itself raise the tools
version that the package declares, because a `5.9` manifest may depend on packages that declare `6.1`.

This unit must decide two questions and record the answers here:

- Does the manifest of `BajutsuKit` also move to `6.1`?
- If it does, does `BajutsuRunner` adopt the strict concurrency checking of the Swift 6 language mode,
  or stay on the Swift 5 mode for now?

This item makes both decisions deliberately. It does not let them fall out of whichever transport
candidate happens to require one.

**Decided, and measured.** `BajutsuKit/Package.swift` **stays at `swift-tools-version:5.9`** with
`platforms: [.iOS(.v15), .macOS(.v11)]`. `BajutsuRunner` stays in the Swift 5 language mode. A `5.9`
manifest resolves, runs the plugin, and builds against the `6.1`-tools OpenAPI packages. That was
verified by building, not inferred. The OpenAPI tooling forces neither the manifest bump
nor a floor change. The Hummingbird dependency would force both. It arrives with Unit 5, not here
(see *Measured result* below), so the floor question travels with it.

That ordering is not tidiness. An intermediate revision of this item raised the floor to iOS 18 and
declared Hummingbird in this unit. The iOS end-to-end gate rejected the revision. The showcase apps
declare `iOS: "17.0"`, and they cannot link a `BajutsuKit` at iOS 18
(`compiling for iOS 17.0, but module 'BajutsuKit' has a minimum deployment target of iOS 18.0`).
A dependency's floor is worth paying when the dependency is used. iOS 17 is that floor, the actual
minimum of the adapter, not a release beyond it.

**The plugin is attached to the shipped `BajutsuRunner` library.** That satisfies the sketch above
("a `BajutsuRunner`-adjacent target"). The generated code is `internal` to the module that uses it.
A separate library target would buy a `public` boundary that no second consumer needs. The
`APIHandler` of Unit 3 lives in the same module and implements the generated `APIProtocol`, so this
is the placement the production code needs.

The plugin landed briefly on the test target instead, while Units 3 to 5 awaited a decision. A
contract with no production consumer then did not charge the runner bundle for a runtime that only
the tests used.

That placement carries one integration cost. `xcodebuild` fails the runner build outright with
`Validate plug-in "OpenAPIGenerator" in package "swift-openapi-generator"`. Xcode gates an unapproved
package plugin behind a trust prompt that it cannot show non-interactively. The `runner-build` and
`runner-build-device` targets in [`demos/showcase/Makefile`](../../demos/showcase/Makefile) therefore
both pass `-skipPackagePluginValidation`.

Suppressing a supply-chain prompt is defensible only because the version of the plugin is fixed.

- Both OpenAPI packages are pinned with `exact:` rather than `from:`.
- `Package.resolved` is committed alongside.
- The `exact:` pin governs the `xcodebuild` path in particular. The `.xcodeproj` of the runner is
  regenerated and Git-ignored, so it carries no resolution of its own.

This unit hit one further issue that Unit 3 will re-enter, so it is worth recording: **`swift build`
alone would not have caught the plugin-validation failure.** The Swift gate
([`swift.yml`](../../.github/workflows/swift.yml)) runs `swift build` and `swift test`. Both honor a
plugin with no trust gate at all. Only the `xcodebuild` path fails, and the required iOS end-to-end
gate depends on that path. A change that moves the plugin back onto the shipped library passes the
Swift gate and still breaks the runner build.

### Unit 2 — Run the transport PoC and decide Hummingbird vs. FlyingFox by measurement

Two candidates carry the contract from Unit 1. Neither is assumed to win before the PoC runs.

- **Candidate H — Hummingbird 2.x with the official `swift-openapi-hummingbird` adapter.** This is
  the simplest integration, because an upstream-maintained `ServerTransport` conformance needs no
  code from this project. It is also the heaviest. The `Package.swift` of Hummingbird declares
  fifteen external dependencies: SwiftNIO, NIOSSL, NIOHTTP2, NIOTransportServices, AsyncHTTPClient,
  and ten more. Most serve concerns that this runner has none of: Transport Layer Security (TLS),
  HTTP/2, distributed tracing, and an outbound HTTP client. The risk section of the source proposal
  warns that a SwiftNIO-based stack could bring this dependency graph. Here it is countable, not
  hypothetical.
- **Candidate F — FlyingFox with a Bajutsu-authored `ServerTransport` conformance.** FlyingFox is
  architecturally the closer relative of today's `HTTPServer.swift`. It uses raw BSD sockets, but
  wraps them in the `async`/`await` of Swift Concurrency instead of a blocking `recv()` loop on a
  `DispatchQueue`. It declares zero external dependencies. It carries no official OpenAPI transport
  adapter, so the cost of this candidate is authoring one.
  - `OpenAPIRuntime.ServerTransport` requires exactly one method: `register(_:method:path:)`.
  - The adapter is a bounded piece of glue. It maps a FlyingFox `HTTPRoute` registration
    onto that one call. It translates between the request and response types of FlyingFox and the
    `HTTPRequest`, `HTTPResponse`, and `HTTPBody` of the runtime.
  - The candidate trades an upstream-maintained adapter for a self-maintained one of known, small
    size. In exchange, the dependency graph has one package instead of fifteen.

Build the same minimal server under both candidates. It serves only the generated `GET /health`
operation, inside the real runner process. Measure the axes that the performance-verification
section of the source proposal names:

- IPA (iOS App Store Package) and binary size delta
- clean and incremental build time delta
- idle and active memory

Add one axis that the source proposal does not carry, because the goal of this item is reliability,
not modernization for its own sake. That axis is the health-poll latency under CI-host contention
that [BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) is already chasing.

Run every measurement on real Simulator-hosted end-to-end (E2E) jobs, not a synthetic benchmark. The
Go criteria of the source proposal are written against a contended GitHub-hosted macOS CI runner, not
an idle developer machine.

Apply the Go/No-Go criteria of the source proposal to each candidate independently. Do not apply them
to "adopt an OpenAPI-generated Hummingbird server" as a single bet. A candidate that fails No-Go drops
out without invalidating the contract work of Unit 1. If both candidates fail, this item stops at
Unit 1 with the OpenAPI contract in hand, and the transport question reopens. The question is not
resolved by default to whichever framework a circulated proposal happened to name first.

#### Measured result: Candidate H is adopted against the measurement, and the OpenAPI layer costs more than either transport

Both PoCs were built inside the real runner. `nm` confirmed that their transports were linked
(Hummingbird 10,415 symbols; FlyingFox 8,056). No figure below is an unlinked dead-code
artifact.

The product measured is `BajutsuRunnerUITests.xctest`. It comes from the same
`xcodebuild build-for-testing -destination 'generic/platform=iOS Simulator'` command that
`runner-build` uses. It is the artifact that
[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) bundles into the
wheel, in the Debug configuration that the bundling actually ships.

| Variant | Clean build | `.xctest` bundle | Platform floor |
|---|---:|---:|---|
| Baseline (`main`, hand-rolled server) | 12.15s | **840 KB** | iOS 15 / macOS 11 |
| Unit 1 only (generated types, no transport) | 46.86s | 10,612 KB | iOS 15 / macOS 11 |
| Unit 1 only, Release configuration | 63.67s | 4,892 KB | iOS 15 / macOS 11 |
| Candidate H — Hummingbird 2.26 + official adapter | 85.44s | **62,272 KB** | **iOS 17 / macOS 14** |
| Candidate F — FlyingFox 0.27.1 + a 70-line transport | 40.61s | **15,648 KB** | iOS 15 / macOS 11 |

**Candidate H is No-Go, on a criterion that the proposal above did not list.** `OpenAPIHummingbird`
declares a minimum of **iOS 17** (and macOS 14), against iOS 15 for the runner. Adopting it raises the
floor of the runner that Bajutsu ships. Bajutsu exists to drive whatever Simulator a user targets.
Dropping iOS 15 and 16 is a capability loss, not a housekeeping detail. No
`LocalHTTPServer`-style isolation contains it, because a platform floor propagates to every consumer.
The 60.8 MB bundle, 74 times the baseline, independently fails the size criterion.

**Decided: Candidate H, adopted against the measurement.** The maintainer accepted raising the
platform floor, which clears the criterion that H failed. The maintainer chose H for its
upstream-maintained adapter.

Re-measuring both candidates at the raised floor changed nothing else. H stayed at 62,272 KB and F at
15,648 KB, because the bulk of H is the static linkage of SwiftNIO, which the deployment target does
not govern. The choice trades two things for an upstream-maintained adapter in place of a
self-maintained one:

- roughly 46 MB of runner bundle
- the Simulator versions below the new floor

That call belongs to the maintainer. This item records it as made, alongside the measurement that
argued the other way.

**The dependency itself lands in Unit 5, not here.** `swift-openapi-hummingbird` requires iOS 17.
Nothing imports Hummingbird until the *listener* moves onto it, and Unit 4 does not move the listener.
Unit 4 serves the generated handlers over the socket layer that already exists. Declaring the
dependency any earlier would link SwiftNIO into the wheel-bundled runner for code that nothing calls.
It would also raise the floor of the package for the same nothing.

Unit 5 adds the dependency and raises the floor to iOS 17, the actual minimum of the adapter, not a
release beyond it. Unit 5 pays the 62,272 KB at the point where the runner serves through the
adapter. Two consequences follow there, and the version number hides both:

- iOS 18 would not be expressible below `swift-tools-version:6.0`.
- Tools 6.0 defaults every target to the Swift 6 language mode. The strict concurrency checking of
  that mode rejects the main-thread hop of the runner (`sending 'result' risks causing data races`).
  That step needs `swiftLanguageMode(.v5)`, which keeps the concurrency migration
  deliberate instead of a side effect of a platform bump.

**Candidate F remains the smaller transport.** It is a quarter of the size of H and needs no
platform-floor change. Its `ServerTransport` conformance came to about 70 lines. That confirms the
estimate of the proposal that the missing adapter is bounded glue. Two costs of F stay visible in the
comparison:

- FlyingFox is pre-1.0 (0.27.1), so its API carries no stability guarantee.
- F still multiplies the runner bundle 18-fold.

**The finding that outranks the comparison is that Unit 1 alone costs 10,612 KB against an 840 KB
baseline.** That is a 12.6-fold increase from the OpenAPI runtime and the generated types, before any
transport is chosen. The proposal above treated the OpenAPI layer as the settled decision and the
transport as the thing worth measuring. The measurement inverts that. Of the 15,648 KB of Candidate
F, roughly two thirds arrives with the contract, not with FlyingFox.

[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) ships this artifact
inside the Python wheel. Every install pays the increase, not only an on-device run. Building the
bundled runner in Release roughly halves it (4,892 KB). That is worth pursuing on its own merits, but
it does not change the order of the increase.

Whether a contract that removes the Swift/Python drift risk is worth roughly ten megabytes of wheel
is a judgment about the product. The PoC does not settle it as a fact. The maintainer took that call
together with the transport call. Adopting Hummingbird accepts the cost of the OpenAPI layer and the
cost of the transport on top of it, so Units 3 onward proceed instead of waiting. The figures stay
recorded here because the decision was made against them, not in ignorance of them.

### Unit 3 — Implement `APIHandler` against the winning candidate

Implement the generated server protocol against the transport of the winning candidate. Translate the
generated `Input`/`Output` types of each operation to and from the existing `ElementProviding`-backed
logic that `Router.swift` already contains.

Preserve the two invariants that comments in `Router.swift` and `HTTPServer.swift` document as
load-bearing, not incidental:

- **Serialization through `actuationLock`.** A second XCUITest operation must not enqueue onto the
  main thread while the first is still pumping the run loop. Without the lock, the non-reentrancy of
  XCUITest aborts the host, per
  [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md).
- **A bounded concurrent-handler count.** The bound keeps a burst of driver and health-poll
  connections from piling up during a long gesture, per
  [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md).

Whichever candidate Unit 2 picks exposes its own concurrency primitive for this. The primitive can be
an actor, a lock, or a bounded task group, in place of `NSLock` and `DispatchSemaphore`. The
invariant itself carries over unchanged: one XCUITest operation in flight at a time, and at most
eight requests handled concurrently. This item does not revisit the reasoning behind either bound. It
revisits only the mechanism that enforces the bound.

The two invariants land in different places, which is easy to miss. Serialization belongs to the
handler, and Unit 3 preserves it. The handler bound belongs to whatever accepts connections, so it
stays with the transport. `APIHandler` never sees a connection, and `HTTPServer` still applies its
own semaphore while it is still serving. Unit 4 is where the bound has to be restated on
the new router. A router configured with no bound would drop it with nothing failing.

### Unit 4 — Migrate the sixteen endpoints behind a comparison harness

Port the endpoints from the switch in `Router.swift` to the generated server protocol in four groups,
not in one step. Both stacks share one port throughout.

- A transitional `ServerTransport` conformance, backed by the legacy `HTTPServer`, registers the
  generated handler of each migrated operation into the existing dispatch.
- The behavior of an endpoint migrates server-side, and the Python driver never changes.
- `BajutsuRunnerTests` already has `IntegrationTests` and `RouterTests`. Extend them to run the
  generated handler of each ported endpoint against the same requests that those suites already
  exercise for the legacy path.
- Confirm an identical response shape, including the exact status-string vocabulary from Motivation,
  before the generated handler replaces the legacy case in the dispatch.

The groups go in this order:

1. `/health` comes first, since Unit 2 already builds it.
2. The handle-addressed endpoints `/elements`, `/tap`, `/isHittable`, and `/gesture` come next, as one
   group. They share the `SnapshotStore` handle contract that
   [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md)
   depends on.
3. `/systemAlert/query` and `/systemAlert/tap` form their own group. They read a separate
   `alertStore`, not the app-tree `store`
   ([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)).
4. The remaining single-purpose endpoints come last: `/screen`, `/swipe`, `/scroll`
   ([BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md)), `/type`, `/deleteText`,
   `/selectAll`, `/copy`, and `/screenshot`.

#### Delivered: the staging moved from the dispatch into the tests

`LegacyBackedTransport` is the transitional `ServerTransport` that the plan above describes.
`HTTPServer` still accepts, parses, and frames. Every route it dispatches is now a generated handler.

The four groups did not survive contact with the generated API. Registration is a single call:
`registerHandlers(on:)` covers all sixteen operations at once. Serving a subset would have meant
building a per-path allowlist whose only purpose was to be deleted again. It would not have given
anything downstream a rollback point. All sixteen migrate together.

The groups are still paid for, in the comparison instead of the dispatch.

- `APIHandlerParityTests` (Unit 3) compares the two implementations in isolation.
- `TransportParityTests` compares them over a live socket, endpoint by endpoint, against the same
  provider state. It compares the status code, the content type, and every field of the reply,
  across the whole five-status vocabulary.

**The eight-handler bound of BE-0287 survives by construction.** This answers the warning that Unit 3
left here. No new router accepts connections, so the `DispatchSemaphore(value: 8)` of `HTTPServer`
still bounds exactly what it always bounded. The generated handlers are `async`, so the transport
blocks its connection thread until the task finishes. That costs the bound nothing, because the
synchronous `Router` already held a slot for the whole life of a request. A live-server test now pins
that `/health` stays answerable while an operation holds the main thread. The pin no longer rests on
the separate reasoning of `APIHandler` and the transport about the same invariant.

**The serialization of BE-0323 is pinned by occupancy, not by the overlap count of a provider.**
Once Unit 4 takes `Router` out of production, `APIHandler.operations` is the sole holder of the
invariant. The regression worth catching is an operation that stops routing through that queue. A
queue that someone declares concurrent is a different case.

`APIHandlerConcurrencyTests` takes four steps:

1. It holds one operation on the main thread.
2. It dispatches a probe onto the queue.
3. It requires the probe *not* to run until the operation returns.
4. That requirement holds only while the operation occupies the queue.

The test fails against both regressions, as measured. A read that calls the provider inline leaves
the case of that read red and the other families green. A concurrent queue turns all three red.

The provider-side probe that `RouterConcurrencyTests` uses cannot substitute for this test. Measured
on this stack, that probe reports no overlap while a concurrent queue genuinely runs two operations at
once. The reason is that libdispatch does not run a second main-queue block while the first is
executing. The `DispatchQueue.main.sync` of the second operation waits for the main block of the first,
whatever serialized the two.

That explains two facts:

- The abort that BE-0323 was filed for surfaced only under the run loop of XCUITest itself.
- The counterpart assertion in `RouterConcurrencyTests` passes with `Router.actuationLock` removed
  outright.

Unit 5 has to re-point these assertions at whatever serializes on Hummingbird. It must not
delete them with the `Router` comparisons.

**The declaration of BE-0362 reaches the one hop that this transport adds.** The task that carries
each reply declares `.userInitiated` instead of inheriting it. The work inside that task never passes
through `operations` and its `qos:`. That work is the generated decode of the request and the encode
of the reply, including the PNG of a screenshot.

A test drives that hop from a `.utility` caller. Inheriting looks identical while the own
`.userInitiated` queue of `HTTPServer` is the caller. It stops looking identical the moment the event
loop of Unit 5 is the caller.

**The deliberate wire differences.** The generated serializer sends `application/json; charset=utf-8`
where `Router` sent a bare `application/json`.

- The driver never reads the header. `_decode` in `bajutsu/drivers/xcuitest.py` takes only the status
  code and the body.
- `/screenshot` is the one reply that the driver branches on by path, not by header. It is
  unaffected.
- A test pins the difference, so it stays a recorded decision instead of a surprise.

Two smaller differences ride along. They are recorded here because the tests that pin them are
deleted with the harness in Unit 5.

- A 400 raised by the generated decoder carries the generic reason phrase of the status. `Router`
  used per-route text. A 400 that the handler derives itself keeps that text, which
  `APIHandlerParityTests.testBadRequestParity` pins field for field.
- The decoder rejects bodies that `Router` tolerated: a non-integer `taps` or `count`, a malformed
  `point` beside a valid `handle`, and a non-conforming body on the three operations that require
  none. The decoder answers 400 without actuating. `Router` coerced the value or ignored the body.

The driver sends none of these shapes, and `_decode` reads only `status`.

`Router` outlives Unit 4 unused by production, because the migration is checked *against* it. The
parity suites and the reply-side half of `ContractConformanceTests` read the shipped behavior off
`Router` and compare. Deleting `Router` and deleting those suites are the same change, and they
belong to Unit 5. One test must survive that deletion.

`ContractConformanceTests`' `testGeneratedRequestTypesDecodeTheDriversBodies` constructs no `Router`
at all. It decodes the literal bodies that `bajutsu/drivers/xcuitest.py` sends into the generated
request types. It is the only fast-suite guard that the *request* half of the contract still accepts
what the shipping driver builds.

Unit 4 makes that half load-bearing, because the generated decoder now answers the driver. Without
the test, a regenerated schema that tightened `taps` or renamed `from`/`to` would reach a device
instead of failing `swift test`.

`TransportParityTests` has two assertions of the same kind. `testMalformedBodyIsRejectedAsAJSONError`
and `testScreenshotServesRawPNGOverTheWire` assert absolutely, not against `Router`.

### Unit 5 — Cut over and remove the legacy transport

Once a generated handler serves every endpoint that `bajutsu/drivers/xcuitest.py` calls, swap the
listener from `LegacyBackedTransport` to the one of the winning candidate. Declare the Hummingbird
dependency and raise the platform floor to iOS 17 at that point (see Unit 2). Then delete:

- the raw-socket accept loop and the byte-by-byte parser of `HTTPServer.swift`
- the hand-written switch and the JSON construction of `Router.swift`

Re-point the queue assertion of `APIHandlerConcurrencyTests` at whatever serializes there. It is the
one guard on BE-0323 that survives the deletion of the `Router` comparisons. The listener swap is
exactly the change it is blind to.

Every handler is already the generated one by this point, so the cutover swaps only the socket layer
beneath them. The wire contract is unchanged throughout, and the Python driver needs no change at any
stage.

## Alternatives considered

- **Keep the hand-rolled server and continue patching individual concerns as they surface.** The
  queued [BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) fix would be
  the next one. This is the status quo, and it is not free. Motivation traces three items that
  already paid for this choice, and nothing bounds a fourth. Rejected for two reasons:
  - The pattern is a continuing tax, not a one-time cost.
  - It leaves the Swift/Python contract with no shared schema.
- **Invest in the hand-rolled server proactively: harden it up front instead of patching it
  reactively.** This is the strongest form of the status quo, and it deserves a real hearing,
  because the case for it is genuinely good.
  - The requirements of the runner are tiny and stable: HTTP/1.1 with `Connection: close`, one known
    client on loopback, bounded bodies, no TLS, no HTTP/2, no keep-alive, and no streaming. Most of
    what a framework carries is dead weight here.
  - The current implementation is roughly 230 lines. It has its own parsing and concurrency test
    suites (`HTTPParsingTests`, `HTTPServerConcurrencyTests`, `RouterConcurrencyTests`).
  - A deliberate hardening pass costs no new dependency, no tools-version bump, and no PoC. The pass
    covers explicit QoS, socket timeouts, and `Codable` reply types in place of `[String: Any]`.

  Rejected on two grounds.
  - First, the hardening list above is exactly the list of failures this project has already met. A
    maintained library does not buy those fixes. It buys the fixes for the failures this project has
    *not* met yet: the slow-read, partial-write, `EINTR`, and descriptor-lifecycle edge cases. Many
    users have already debugged a widely used server against those cases. A single-client in-house
    server discovers them one CI flake at a time. Betting that BE-0287, BE-0323, and BE-0362 exhausted
    the list is the same bet that [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
    originally made, three failures ago.
  - Second, hardening the transport does nothing for the contract. The Swift and Python literals stay
    coupled by convention, not by a compiler. Closing that gap requires the OpenAPI contract and the
    generated types regardless. Once those are adopted, the hand-rolled transport beneath them is the
    custom-`ServerTransport`-on-raw-sockets path. A later bullet below evaluates that path and rejects
    it in favor of measuring FlyingFox.
- **Commit to Hummingbird outright, as the circulated source proposal recommends, without a
  comparative PoC.** Rejected for this item. The dependency graph of Hummingbird has fifteen external
  packages, most unrelated to a localhost JSON API. The risk section of the source proposal says this
  class of cost needs measuring before committing. Measuring against a real alternative makes the
  measurement meaningful, instead of a pass/fail check against an assumption.
- **Adopt FlyingFox outright, skipping the comparison,** on the strength of its zero-dependency
  footprint and its architectural kinship with today's implementation. Rejected symmetrically.
  FlyingFox carries no official OpenAPI transport adapter. The cost of this path is a self-maintained
  `ServerTransport` conformance. It deserves the same measurement discipline as the dependency cost
  of Hummingbird, not an assumption in the opposite direction.
- **Write a custom `ServerTransport` directly on raw BSD sockets,** keeping today's transport layer and
  adding only the OpenAPI contract on top (the Telegraph-based fallback of the source proposal).
  Rejected in favor of evaluating FlyingFox instead.
  - The history of `HTTPServer.swift` is the demonstrated cost of maintaining a socket-and-parsing
    layer in-house. That history is the backlog, semaphore, and QoS patches that Motivation traces.
  - FlyingFox already carries that maintenance elsewhere. It also presents the same lightweight,
    Apple-platform-first profile that a from-scratch transport would aim for.
- **Migrate all sixteen endpoints in one step, skipping the comparison harness of Unit 4.** Rejected.
  The string of items in Motivation and Unit 3 (BE-0287, BE-0289, BE-0323) is the history of a
  channel that has broken in production in narrow, specific ways more than once. Replacing the entire
  HTTP transport in a single step reopens that whole surface at once. It leaves no staged point to
  roll one endpoint back if its generated path disagrees with the legacy one.

  What Unit 4 delivered is not this alternative. The transport beneath the handlers is untouched, and
  the comparison harness grew instead of being skipped. The endpoints moved together because
  registration is a single generated call, not because the comparison was dropped. See *Delivered*
  under Unit 4.
- **Extend Bonjour discovery, LAN exposure, and bearer-token authentication per the general sections
  of the source proposal on those topics.** Out of scope for this item, since the channel of the
  resident runner is localhost-only today, as the Introduction states. A future item should propose
  these separately if a real-device or remote-driver use case that needs LAN exposure is ever
  adopted.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — author `openapi.yaml` for the sixteen existing endpoints, wire Swift OpenAPI
      Generator's build plugin into `BajutsuKit`, and record the swift-tools-version and
      language-mode decision: both stay put (tools 5.9, iOS 15, Swift 5 mode), because the OpenAPI
      tooling forces neither — only the Hummingbird dependency would, and that lands with Unit 4.
      The plugin sits on the shipped library, which costs `-skipPackagePluginValidation` on both
      runner builds; `exact:` pins plus a committed `Package.resolved` are what make accepting that
      prompt-suppression defensible.
- [x] Unit 2 — build the `/health`-only PoC under both Hummingbird and FlyingFox and record the
      Go/No-Go outcome for each candidate: **F is the smaller transport** at 15.6 MB with no floor
      change, while **H costs 60.8 MB and an iOS 17 floor** — which the maintainer cleared by
      raising the floor to iOS 18 and adopting H for its upstream-maintained adapter. Size and platform floors are measured; the
      on-device memory and CI-contention health-poll latency axes still need a real E2E job, and
      the build times are single unrepeated runs carrying visible variance.
- [x] Unit 3 — implement `APIHandler` against the winning candidate. The `actuationLock`
      serialization carries over as one serial `operations` queue, which also absorbs the blocking
      main-thread hop so a future event loop is never held; `/health` is the one operation that
      never touches it, and so stays answerable during a long gesture. **BE-0287's eight-handler
      bound does not carry over here** — nothing in `APIHandler` accepts a connection, so the bound
      is the transport's, still enforced by `HTTPServer`'s semaphore. Unit 4 must carry it onto
      whatever router it builds; a server with no bound would drop it silently. Parity tests compare
      every endpoint's generated reply against the legacy `Router`'s for the same input, and
      mutation-testing confirms they fail on a drifted status string or a dropped optional field.
- [x] Unit 4 — serve every endpoint through the generated handlers. `LegacyBackedTransport` carries
      them over `HTTPServer`'s socket layer, so the wire contract holds and the Python driver is
      untouched. All sixteen migrate at once: registration is one generated call, and a per-path
      allowlist would have been machinery built only to be deleted. **BE-0287's bound survives by
      construction** — nothing new accepts connections — and `TransportParityTests` compares every
      endpoint against the legacy `Router` over a live socket. `APIHandlerConcurrencyTests` pins
      BE-0323 by occupancy — a held operation must keep a probe off `APIHandler.operations` — and the
      reply task's declared `.userInitiated` (BE-0362) is pinned from a lower-QoS caller. Three recorded wire differences, none
      observable to the driver: JSON replies carry `charset=utf-8`, a 400 the generated decoder raises
      carries the generic reason phrase where the handler's own 400s keep `Router`'s per-route text,
      and that decoder rejects a few malformed-body shapes `Router` used to tolerate.
- [ ] Unit 5 — swap the listener onto Hummingbird, declaring the dependency and raising the floor to
      iOS 17 there, and remove `LegacyBackedTransport`, `HTTPServer.swift`, and `Router.swift`
      together with the parity suites that compare against them — keeping
      `ContractConformanceTests`' `testGeneratedRequestTypesDecodeTheDriversBodies` and
      `TransportParityTests`' two absolute assertions, which pin the contract rather than `Router`.

## References

- [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — introduced this channel and the
  "keep it under the project's control" rationale this item revisits under a surface three times
  larger.
- [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
  — the backlog and semaphore patch this item's Motivation cites as recurring transport upkeep.
- [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md) —
  the handle-based addressing contract Unit 1's schema and Unit 4's migration grouping must preserve.
- [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) — bundles the runner
  this item changes as a prebuilt wheel artifact, so the new transport ships through the same pipeline.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) — introduced the
  `alertStore` / `/systemAlert` endpoints Unit 4 migrates as their own group.
- [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md) —
  introduced the `actuationLock` invariant Unit 3 must preserve.
- [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) — introduced the `/scroll`
  endpoint Unit 4 migrates among the remaining single-purpose endpoints.
- [BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) — a still-open proposal
  patching the same transport layer this item replaces; its health-poll-latency concern is one of
  Unit 2's measured PoC axes.
- [swift-openapi-generator](https://github.com/apple/swift-openapi-generator) and
  [swift-openapi-runtime](https://github.com/apple/swift-openapi-runtime) — produce the Swift types
  and server protocol Unit 1 wires into the build, and set the `swift-tools-version:6.1` floor.
- [Hummingbird](https://github.com/hummingbird-project/hummingbird) and
  [swift-openapi-hummingbird](https://github.com/hummingbird-project/swift-openapi-hummingbird) —
  Candidate H in Unit 2.
- [FlyingFox](https://github.com/swhitty/FlyingFox) — Candidate F in Unit 2.
