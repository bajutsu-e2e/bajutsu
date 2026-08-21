**English** · [日本語](BE-0381-runner-openapi-contract-ja.md)

# BE-0381 — Generate the XCUITest runner's HTTP server from an OpenAPI contract instead of hand-rolling it

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0381](BE-0381-runner-openapi-contract.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0381") |
| Implementing PR | [#1606](https://github.com/bajutsu-e2e/bajutsu/pull/1606) |
| Topic | Platform support |
| Related | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md), [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md), [BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) |
<!-- /BE-METADATA -->

## Introduction

BajutsuKit's XCUITest runner (`BajutsuKit/Sources/BajutsuRunner`) answers every query and actuation
the Python driver sends it through a small HTTP server the runner hand-rolls from raw BSD sockets:
its own byte-by-byte request parser, its own JSON encoding, its own routing switch over sixteen
paths, and its own connection-lifecycle management. This item replaces that hand-rolled layer with
one generated from a checked-in OpenAPI contract: an `openapi.yaml` document becomes the single
source of truth for the runner's HTTP surface, Swift OpenAPI Generator produces the Swift types and
server protocol from it, and a proof of concept (PoC) decides which HTTP framework fills the
transport role beneath that protocol. Adopting the OpenAPI contract and its generated types is fixed;
which framework carries it — Hummingbird, brought over from server-side Swift with an official
OpenAPI adapter, or FlyingFox, a zero-dependency socket library built for Apple platforms with no such
adapter today — is a measured decision this item's PoC makes rather than assumes.

That framing survived the PoC only in part, and the record below says so. On size alone the
measurement favoured FlyingFox and would have ruled Hummingbird out, but the criterion Hummingbird
failed was a platform floor rather than a physical limit, and the maintainer chose to raise the floor
to iOS 18 and adopt Hummingbird for its upstream-maintained adapter. The measurement also showed the
supposedly settled OpenAPI layer carrying a larger cost than either transport's own share, which
reopens the "fixed" half of the sentence above. See *Measured result* under Unit 2 for both the
figures and the decision taken against them.

The runner this item touches binds only to `127.0.0.1` and talks only to the same-host Python driver
(`bajutsu/drivers/xcuitest.py`); it is never exposed to another device over a local-area network
(LAN). A circulated migration proposal for an iOS app's own embedded HTTP server — the source this
item adapts — spends several sections on LAN exposure, Bonjour service discovery, and bearer-token
authentication for exactly that reason: those sections describe a different exposure surface than
this runner has, so this item scopes them out rather than adopting them.

## Motivation

Every low-level HTTP responsibility a real server framework would supply is instead maintained by
hand today. `HTTPServer.swift` reads a request one byte at a time into a header buffer capped at a
hardcoded 8,192 bytes, frames the response body with a manually written `Content-Length` header, and
writes its own status text for four hardcoded status codes. `Router.swift` dispatches on a `switch`
over the request's method and path, and builds every JSON reply by hand as a `[String: Any]`
dictionary passed to `JSONSerialization`. None of this is Bajutsu's own value — it is exactly the
protocol-following, error-handling work that carries no product-specific meaning but still needs
continuous upkeep as the surface it serves grows.

This repository's own history is the evidence that the upkeep is real, not hypothetical.
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
raised the listen backlog from 1 to 16 and added an eight-handler semaphore after a burst of driver
and health-poll connections exhausted the original backlog outright.
[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
added a main-thread serialization lock (`actuationLock`) because two concurrent XCUITest operations
reaching the main thread at once re-enter XCUITest, which is not re-entrant, and abort the test host.
[BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) is a still-open proposal
to declare an explicit quality of service (QoS) on the server's dispatch queues, because neither queue
declares one today and the correct priority is only a coincidence of which thread happens to start the
accept loop. The first and third of these are concerns — connection backpressure, scheduling priority
— that an HTTP framework settles by construction. The second is not: serializing XCUITest operations
is an invariant specific to this runner, and it survives any transport (Unit 3 below carries it over).
What BE-0323 shows instead is that the hand-rolled transport's concurrency semantics are implicit —
the interaction that crashed the host existed because nothing documented what the connection layer
guarantees — where a framework at least hosts the same invariant on a defined, documented concurrency
model.

The same hand-maintenance shows up as a second, distinct cost: no compiler checks the contract between
the Swift server and the Python driver. `Router.swift` writes reply bodies as literal string values —
`"ok"`, `"stale"`, `"not-found"`, `"not-hittable"`, `"value-not-found"` — and
`bajutsu/drivers/xcuitest.py` matches those same five literals against its own constants (`_OK`,
`_STALE`, `_NOT_FOUND`, `_NOT_HITTABLE`, `_VALUE_NOT_FOUND`) declared independently on the Python
side. A change to either side's literal compiles cleanly even when it
silently stops matching the other, because nothing shared enforces agreement — the exact API-drift
risk a source-of-truth-less contract invites.

[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md), which introduced this channel,
chose deliberately not to pay for a framework: it built "the minimal runner-side server... in
`BajutsuKit` rather than adopting a large external dependency, keeping the channel under the
project's control." That choice was right for the surface it served then — a handful of endpoints
newly introduced to unblock XCUITest as a second actuator. It is worth revisiting now because the
surface has tripled to sixteen endpoints, spanning handle-based element addressing
([BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md)),
multi-touch gestures, SpringBoard system-alert routing, and screenshots, and three further items have
each patched a low-level transport concern the original design left implicit. "Keeping the channel
under the project's control" also does not require hand-rolling sockets: this item's design keeps the
app-specific `APIHandler` layer entirely inside `BajutsuKit`, under this project's own control, while
delegating the socket, parsing, and framing layer beneath it to a library maintained outside this
project — the same separation [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
itself drew between the driver-facing contract (kept in-house) and the actuation engine underneath it
(XCUITest, not hand-rolled).

## Detailed design

The design keeps `RunnerServer`'s entire public interface unchanged:
`BajutsuKit/Runner/Sources/RunnerUITest.swift` consumes it through `startFromEnvironment()` / `stop()`
and the forwarded-launch statics, and the Python environment
(`bajutsu/platform_lifecycle/environments/xcuitest.py`) couples to the runner only through
environment variables such as `BAJUTSU_RUNNER_PORT`. Every change below is internal to
`BajutsuKit/Sources/BajutsuRunner`. The work splits into five units. Units 1 and 2 must
land in order — the PoC in Unit 2 measures candidates built against the contract Unit 1 defines — but
Units 3 through 5 are the endpoint-migration work that follows once Unit 2 picks a transport.

### Unit 1 — Author the OpenAPI contract, independent of transport

Write `openapi.yaml` describing the runner's sixteen existing paths and methods as the single source
of truth for the runner's HTTP surface. The sixteen break down as follows:

- reads: `GET /health`, `GET /elements`, `GET /screen`, and `GET /screenshot`
- actuation: `POST /tap`, `POST /isHittable`, `POST /gesture`, `POST /swipe`, `POST /scroll`, and
  `POST /setPickerValue`
- text editing: `POST /type`, `POST /deleteText`, `POST /selectAll`, and `POST /copy`
- system alerts: `POST /systemAlert/query` and `POST /systemAlert/tap`

`POST /setPickerValue` and its `value-not-found` status
([BE-0356](../BE-0356-xcuitest-picker-wheel-adjust/BE-0356-xcuitest-picker-wheel-adjust.md)) reached
`main` while this item was in flight, and were absorbed here. The merge was textually clean and still
broke the build, which is the useful part of the story: `main` added a `TapResult` case, this branch
added the first exhaustive `switch` over that enum, and only the compiler saw the conflict.

The schema must preserve the exact wire values the Python driver
already matches — the five status strings from Motivation, and the element shape (`identifier`,
`label`, `value`, `traits`, `frame`, `handle`) that `/elements` and `/systemAlert/query` already
share — so that generating the contract from today's behavior, rather than from a redesign, is what
keeps Unit 4's endpoint-by-endpoint migration a behavior-preserving port rather than a rewrite.

Add Swift OpenAPI Generator's build plugin to a `BajutsuRunner`-adjacent target so `Types.swift` and
a server protocol regenerate from `openapi.yaml` at every build, rather than being checked in and
left to drift from the contract that produced them. Both Swift OpenAPI Generator and Swift OpenAPI
Runtime declare `swift-tools-version:6.1`, above `BajutsuKit/Package.swift`'s current `5.9`. That
raises the Swift toolchain floor for building `BajutsuKit` to 6.1 regardless of which transport Unit 2
picks — the floor comes from the OpenAPI tooling itself, not from Hummingbird or FlyingFox — but it
does not by itself force the package's own declared tools version up, because a `5.9` manifest may
depend on packages declaring `6.1`. This unit must therefore decide, and record here, whether
`BajutsuKit`'s own manifest also moves to `6.1`, and if it does, whether `BajutsuRunner` adopts the
Swift 6 language mode's strict concurrency checking or stays on the Swift 5 mode for now — decisions
this item makes deliberately rather than letting them fall out of whichever transport candidate
happens to require one.

**Decided, and measured.** `BajutsuKit/Package.swift` **stays at `swift-tools-version:5.9`** with
`platforms: [.iOS(.v15), .macOS(.v11)]`, and `BajutsuRunner` stays in the Swift 5 language mode. A
`5.9` manifest resolves, runs the plugin, and builds against the `6.1`-tools OpenAPI packages —
verified by building rather than inferred — so the OpenAPI tooling forces neither the manifest bump
nor a floor change. What would force both is the Hummingbird dependency, and that arrives with Unit
5 rather than here (see *Measured result* below), so the floor question travels with it.

That ordering is not tidiness. An intermediate revision of this item raised the floor to iOS 18 and
declared Hummingbird here, and the iOS end-to-end gate rejected it: the showcase apps declare
`iOS: "17.0"`, and a `BajutsuKit` at iOS 18 cannot be linked by them
(`compiling for iOS 17.0, but module 'BajutsuKit' has a minimum deployment target of iOS 18.0`).
The floor a dependency needs is worth paying when the dependency is used, and iOS 17 is that floor
— the adapter's actual minimum — not a release beyond it.


**The plugin is attached to the shipped `BajutsuRunner` library**, which the sketch above satisfies
as "a `BajutsuRunner`-adjacent target": the generated code is `internal` to the module that uses it,
and a separate library target would buy only a `public` boundary no second consumer needs. Unit 3's
`APIHandler` lives in that same module and implements the generated `APIProtocol`, so this is the
placement the production code needs. (It landed briefly on the test target instead, while Units 3 to
5 were still pending a decision, so that a contract with no production consumer would not yet charge
the runner bundle for a runtime only the tests used.)

That placement carries one integration cost. `xcodebuild` fails the runner build outright with
`Validate plug-in "OpenAPIGenerator" in package "swift-openapi-generator"` — Xcode gates an
unapproved package plugin behind a trust prompt it cannot show non-interactively — so `runner-build`
and `runner-build-device` in [`demos/showcase/Makefile`](../../demos/showcase/Makefile) both pass
`-skipPackagePluginValidation`. Suppressing a supply-chain prompt is defensible only because the
plugin's version is fixed: both OpenAPI packages are pinned with `exact:` rather than `from:`, and
`Package.resolved` is committed alongside. The `exact:` pin is what governs the `xcodebuild` path in
particular, since the runner's `.xcodeproj` is regenerated and Git-ignored and so carries no
resolution of its own.

One further trap this unit hit is worth recording for Unit 3, which will re-enter it: **`swift build`
alone would not have caught the plugin-validation failure.** The Swift gate
([`swift.yml`](../../.github/workflows/swift.yml)) runs `swift build`/`swift test`, which honour a
plugin with no trust gate at all; only the `xcodebuild` path the required iOS end-to-end gate depends
on fails. A change that moves the plugin back onto the shipped library will pass the Swift gate and
still break the runner build.

### Unit 2 — Run the transport PoC and decide Hummingbird vs. FlyingFox by measurement

Two candidates carry the contract from Unit 1, and neither is assumed to win before the PoC runs:

- **Candidate H — Hummingbird 2.x with the official `swift-openapi-hummingbird` adapter.** The
  simplest integration: an upstream-maintained `ServerTransport` conformance needs no code from this
  project. It is also the heaviest. Hummingbird's own `Package.swift` declares fifteen external
  dependencies — SwiftNIO, NIOSSL, NIOHTTP2, NIOTransportServices, AsyncHTTPClient, and ten more —
  most bound to concerns this runner has none of: Transport Layer Security (TLS), HTTP/2, distributed
  tracing, an outbound HTTP client. This is the dependency graph the source proposal's own risk
  section warns a SwiftNIO-based stack could bring, now countable rather than hypothetical.
- **Candidate F — FlyingFox with a Bajutsu-authored `ServerTransport` conformance.** FlyingFox is
  architecturally the closer relative of today's `HTTPServer.swift`: raw BSD sockets, but wrapped in
  Swift Concurrency's `async`/`await` instead of a blocking `recv()` loop on a `DispatchQueue`, and it
  declares zero external dependencies. It carries no official OpenAPI transport adapter, so this
  candidate's own cost is authoring one. `OpenAPIRuntime.ServerTransport` requires exactly one method
  — `register(_:method:path:)` — so the adapter is a bounded piece of glue: it maps a FlyingFox
  `HTTPRoute` registration onto that one call and translates between FlyingFox's request/response
  types and the runtime's `HTTPRequest`/`HTTPResponse`/`HTTPBody`. This candidate trades an
  upstream-maintained adapter for a self-maintained one of known, small size, in exchange for a
  dependency graph of one package instead of fifteen.

Build the same minimal server under both candidates — serving only the generated `GET /health`
operation, inside the real runner process — and measure the axes the source proposal's own
performance-verification section names: IPA (iOS App Store Package)/binary size delta, clean and
incremental build time delta, and idle and active memory. Add one axis the source proposal does not
carry, because this item's own goal is reliability rather than modernization for its own sake: the
health-poll latency under CI-host contention that
[BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) is already chasing. Run
every measurement on real Simulator-hosted end-to-end (E2E) jobs, not a synthetic benchmark, since a
contended GitHub-hosted macOS CI runner — not an idle developer machine — is the condition the source
proposal's own Go criteria are written against.

Apply the source proposal's Go/No-Go criteria to each candidate independently, rather than to "adopt
an OpenAPI-generated Hummingbird server" as a single bet. A candidate that fails No-Go drops out
without invalidating Unit 1's contract work; if both candidates fail, this item stops at Unit 1 with
the OpenAPI contract in hand and the transport question reopened, rather than resolved by default to
whichever framework a circulated proposal happened to name first.

#### Measured result: Candidate H is adopted against the measurement, and the OpenAPI layer costs more than either transport

Both PoCs were built inside the real runner and their transports confirmed linked with `nm`
(Hummingbird 10,415 symbols; FlyingFox 8,056), so no figure below is an unlinked dead-code artifact.
The product measured is `BajutsuRunnerUITests.xctest` from the same
`xcodebuild build-for-testing -destination 'generic/platform=iOS Simulator'` command
`runner-build` uses — that is, the artifact
[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) bundles into the
wheel, in the Debug configuration that bundling actually ships.

| Variant | Clean build | `.xctest` bundle | Platform floor |
|---|---:|---:|---|
| Baseline (`main`, hand-rolled server) | 12.15s | **840 KB** | iOS 15 / macOS 11 |
| Unit 1 only (generated types, no transport) | 46.86s | 10,612 KB | iOS 15 / macOS 11 |
| Unit 1 only, Release configuration | 63.67s | 4,892 KB | iOS 15 / macOS 11 |
| Candidate H — Hummingbird 2.26 + official adapter | 85.44s | **62,272 KB** | **iOS 17 / macOS 14** |
| Candidate F — FlyingFox 0.27.1 + a 70-line transport | 40.61s | **15,648 KB** | iOS 15 / macOS 11 |

**Candidate H is No-Go, on a criterion the proposal above did not list.** `OpenAPIHummingbird`
declares a minimum of **iOS 17** (and macOS 14), against the runner's iOS 15, so adopting it raises
the floor of the runner Bajutsu ships. For a tool whose purpose is driving whatever Simulator a user
targets, dropping iOS 15 and 16 is a capability loss rather than a housekeeping detail, and no
`LocalHTTPServer`-style isolation contains it — a platform floor propagates to every consumer. Its
60.8 MB bundle, 74 times the baseline, independently fails the size criterion.

**Decided: Candidate H, adopted against the measurement.** The maintainer accepted raising the
platform floor, which clears the criterion H failed, and chose H for its upstream-maintained
adapter. Re-measuring both candidates at the raised floor changed nothing else — H stayed at
62,272 KB and F at 15,648 KB, because H's bulk is SwiftNIO's static linkage rather than anything the
deployment target governs — so the choice trades roughly 46 MB of runner bundle, and the Simulator
versions below the new floor, for an upstream-maintained adapter in place of a self-maintained one.
That is the maintainer's call to make, and it is recorded here as made, alongside the measurement
that argued the other way.

**The dependency itself lands in Unit 5, not here.** `swift-openapi-hummingbird` requires iOS 17,
and nothing imports Hummingbird until the *listener* moves onto it — which Unit 4 does not do, since
it serves the generated handlers over the socket layer that already exists. Declaring it any earlier
would link SwiftNIO into the wheel-bundled runner for code nothing calls and raise the package's
floor for the same nothing. Unit 5 adds the dependency, raises the floor to iOS 17 —
the adapter's actual minimum, not a release beyond it — and pays the 62,272 KB at the point the
runner serves through it. Two consequences to expect there, neither obvious from the version number:
iOS 18 would not be expressible below `swift-tools-version:6.0`, and tools 6.0 defaults every target
to the Swift 6 language mode, whose strict concurrency checking the runner's main-thread hop does not
pass (`sending 'result' risks causing data races`), so that step needs `swiftLanguageMode(.v5)` to
keep the concurrency migration deliberate rather than a side effect of a platform bump.

**Candidate F remains the smaller transport**: a quarter of H's size, no platform-floor change, and a
`ServerTransport` conformance that came to about 70 lines, confirming the proposal's estimate that the
missing adapter is bounded glue. Two costs the comparison above should not hide: FlyingFox is
pre-1.0 (0.27.1), so its API carries no stability guarantee, and it still multiplies the runner
bundle 18-fold.

**The finding that outranks the comparison, though, is that Unit 1 alone costs 10,612 KB against an
840 KB baseline — a 12.6-fold increase from the OpenAPI runtime and generated types, before any
transport is chosen.** The proposal above treated the OpenAPI layer as the settled decision and the
transport as the thing worth measuring; the measurement inverts that. Of Candidate F's 15,648 KB,
roughly two thirds arrives with the contract rather than with FlyingFox. Because
[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) ships this artifact
inside the Python wheel, that increase is paid by every install, not only by an on-device run.
Building the bundled runner in Release roughly halves it (4,892 KB) and is worth pursuing on its own
merits, but it does not change the order of the increase. Whether a contract that removes the
Swift/Python drift risk is worth roughly ten megabytes of wheel is a judgement about the product
rather than a fact the PoC settles. The maintainer took that call together with the transport one:
adopting Hummingbird accepts both the OpenAPI layer's own cost and the transport's on top of it, so
Units 3 onward proceed rather than waiting. The figures stay recorded here because the decision was
made against them, not in ignorance of them.

### Unit 3 — Implement `APIHandler` against the winning candidate

Implement the generated server protocol against the winning candidate's transport, translating each
operation's generated `Input`/`Output` types to and from the existing `ElementProviding`-backed logic
`Router.swift` already contains. Preserve the two invariants `Router.swift`'s and `HTTPServer.swift`'s own comments document as
load-bearing rather than incidental: the `actuationLock` serialization that keeps a second XCUITest
operation from enqueuing onto the main thread while the first is still pumping the run loop (without
it, XCUITest's non-reentrancy aborts the host, per
[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)),
and the bounded concurrent-handler count that keeps a burst of driver and health-poll connections from
piling up during a long gesture (per
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)).
Whichever candidate Unit 2 picks exposes its own concurrency primitive for this — an actor, a lock, or
a bounded task group, rather than `NSLock` and `DispatchSemaphore` — but the invariant itself, one
XCUITest operation in flight at a time and at most eight requests handled concurrently, carries over
unchanged. This item does not revisit the reasoning behind either bound, only the mechanism that
enforces it.

The two land in different places, though, which is easy to miss. Serialization belongs to the
handler and is Unit 3's to preserve. The handler bound belongs to whatever accepts connections, so
it stays with the transport: `APIHandler` never sees a connection, and while `HTTPServer` is still
serving, its own semaphore still applies. Unit 4 is therefore where the bound has to be restated on
the new router — and a router configured with no bound would drop it with nothing failing.

### Unit 4 — Migrate the sixteen endpoints behind a comparison harness

Port the endpoints from `Router.swift`'s switch to the generated server protocol in four groups,
rather than in one step. Both stacks share one port throughout: a transitional `ServerTransport`
conformance backed by the legacy `HTTPServer` registers each migrated operation's generated handler
into the existing dispatch, so an endpoint's behavior migrates server-side and the Python driver
never changes. Extend `BajutsuRunnerTests`'s existing `IntegrationTests` and `RouterTests` to run
each ported endpoint's generated handler against the same requests those suites already exercise for
the legacy path, confirming an identical response shape — including the exact status-string
vocabulary from Motivation — before that endpoint's generated handler replaces the legacy case in the
dispatch. `/health` comes first, since Unit 2 already builds it. The handle-addressed
endpoints — `/elements`, `/tap`, `/isHittable`, `/gesture` — come next, as one group, since they share
the `SnapshotStore` handle contract
[BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md)
depends on. `/systemAlert/query` and `/systemAlert/tap` form their own group, since they read a
separate `alertStore` rather than the app-tree `store`
([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)). The remaining
single-purpose endpoints — `/screen`, `/swipe`, `/scroll`
([BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md)), `/type`, `/deleteText`,
`/selectAll`, `/copy`, `/screenshot` — come last.

#### Delivered: the staging moved from the dispatch into the tests

`LegacyBackedTransport` is the transitional `ServerTransport` the plan above describes: `HTTPServer`
still accepts, parses, and frames, and every route it dispatches is now a generated handler. The four
groups, though, did not survive contact with the generated API. Registration is a single call —
`registerHandlers(on:)` covers all sixteen operations at once — so serving a subset would have meant
building a per-path allowlist whose only purpose was to be deleted again, rather than a rollback
point anything downstream could use. All sixteen therefore migrate together.

What the groups were *for* is still paid for, in the comparison rather than in the dispatch.
`APIHandlerParityTests` (Unit 3) compares the two implementations in isolation; `TransportParityTests`
now compares them over a live socket, endpoint by endpoint, against the same provider state — status
code, content type, and every field of the reply, across the whole five-status vocabulary.

**BE-0287's eight-handler bound survives by construction**, which answers the warning Unit 3 left
here: no new router accepts connections, so `HTTPServer`'s `DispatchSemaphore(value: 8)` still bounds
exactly what it always bounded. The generated handlers are `async`, so the transport blocks its
connection thread until the task finishes — which costs the bound nothing, because a slot was already
held for the whole life of a request under the synchronous `Router`. That `/health` stays answerable
while an operation holds the main thread is now pinned by a live-server test, rather than left to
`APIHandler`'s and the transport's separate reasoning about the same invariant.

**The deliberate wire differences.** The generated serializer sends `application/json; charset=utf-8`
where `Router` sent a bare `application/json`. The driver never reads the header — `_decode` in
`bajutsu/drivers/xcuitest.py` takes only the status code and the body — and `/screenshot`, the one
reply it branches on by path rather than by header, is unaffected. A test pins the difference so it
stays a recorded decision rather than a surprise. Two smaller differences ride along, recorded here
because the tests that pin them are deleted with the harness in Unit 5: a 400 reply's `message` is now
the status's generic reason phrase rather than `Router`'s per-route text, and the generated decoder
rejects bodies `Router` tolerated — a non-integer `taps` or `count`, a malformed `point` beside a valid
`handle`, or a non-conforming body on the three operations that require none — answering 400 without
actuating where `Router` coerced the value or ignored the body. The driver sends none of these shapes,
and `_decode` reads only `status`.

`Router` outlives Unit 4 unused by production, because it is what the migration is checked *against*:
the parity and conformance suites read the shipped behavior off it and compare. Its deletion and
theirs are the same change, and belong to Unit 5.

### Unit 5 — Cut over and remove the legacy transport

Once every endpoint `bajutsu/drivers/xcuitest.py` calls is served by a generated handler, swap the
listener from `LegacyBackedTransport` to the winning candidate's, declaring the Hummingbird
dependency and raising the platform floor to iOS 17 at that point (see Unit 2), then delete
`HTTPServer.swift`'s raw-socket accept loop and byte-by-byte parser, and `Router.swift`'s hand-written
switch and JSON construction. Every handler is already the generated one by this point, so the
cutover swaps only the socket layer beneath them; the wire contract is unchanged throughout, and the
Python driver needs no change at any stage.

## Alternatives considered

- **Keep the hand-rolled server and continue patching individual concerns as they surface** (the
  queued [BE-0362](../BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md) fix would be the
  next one). This is the status quo, and it is not free: Motivation traces three items already paid
  for this choice, and nothing bounds a fourth. Rejected because the pattern is a continuing tax
  rather than a one-time cost, and it leaves the Swift/Python contract with no shared schema.
- **Invest in the hand-rolled server proactively — harden it up front rather than patching it
  reactively.** The strongest form of the status quo, and it deserves a real hearing, because the
  case for it is genuinely good. The runner's requirements are tiny and stable: HTTP/1.1 with
  `Connection: close`, one known client on loopback, bounded bodies, no TLS, no HTTP/2, no
  keep-alive, no streaming — so most of what a framework carries is dead weight here. The current
  implementation is roughly 230 lines with its own parsing and concurrency test suites
  (`HTTPParsingTests`, `HTTPServerConcurrencyTests`, `RouterConcurrencyTests`), and a deliberate
  hardening pass — explicit QoS, socket timeouts, `Codable` reply types in place of `[String: Any]`
  — costs no new dependency, no tools-version bump, and no PoC. Rejected on two grounds. First, the
  hardening list above is exactly the list of failures this project has already met; what a
  maintained library buys is not those fixes but the ones this project has *not* met yet — the
  slow-read, partial-write, `EINTR`, and descriptor-lifecycle edge cases that a server exercised by
  many users has already been debugged against, and that a single-client in-house server discovers
  one CI flake at a time. Betting that BE-0287, BE-0323, and BE-0362 exhausted the list is the same
  bet [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) originally made, three
  failures ago. Second, hardening the transport does nothing for the contract: the Swift and Python
  literals stay coupled by convention, not by a compiler. Closing that gap requires the OpenAPI
  contract and generated types regardless — and once those are adopted, the hand-rolled transport
  beneath them is the custom-`ServerTransport`-on-raw-sockets path evaluated, and rejected in favor
  of measuring FlyingFox, in its own bullet below.
- **Commit to Hummingbird outright, as the circulated source proposal recommends, without a
  comparative PoC.** Rejected for this item: Hummingbird's own dependency graph — fifteen
  external packages, most unrelated to a localhost JSON API — is exactly the class of cost the source
  proposal's own risk section says needs measuring before committing. Measuring it against a real
  alternative makes that measurement meaningful instead of a pass/fail check against an assumption.
- **Adopt FlyingFox outright, skipping the comparison,** on the strength of its zero-dependency
  footprint and its architectural kinship with today's implementation. Rejected symmetrically:
  FlyingFox carries no official OpenAPI transport adapter, so this path's own cost — a self-maintained
  `ServerTransport` conformance — deserves the same measurement discipline as Hummingbird's dependency
  cost, not an assumption in the opposite direction.
- **Write a custom `ServerTransport` directly on raw BSD sockets,** keeping today's transport layer and
  adding only the OpenAPI contract on top (the source proposal's own Telegraph-based fallback).
  Rejected in favor of evaluating FlyingFox instead: `HTTPServer.swift`'s own history — the backlog,
  semaphore, and QoS patches Motivation traces — is the demonstrated cost of maintaining a
  socket-and-parsing layer in-house, and FlyingFox already carries that maintenance elsewhere while
  presenting the same lightweight, Apple-platform-first profile a from-scratch transport would aim
  for.
- **Migrate all sixteen endpoints in one step, skipping Unit 4's comparison harness.** Rejected: the
  string of items in Motivation and Unit 3 — BE-0287, BE-0289, BE-0323 — is the history of a channel
  that has broken in production in narrow, specific ways more than once; replacing its entire HTTP
  transport in a single step reopens that whole surface at once, with no staged point to roll one
  endpoint back if its generated path disagrees with the legacy one. What Unit 4 delivered is not
  this alternative: the transport beneath the handlers is untouched, and the comparison harness grew
  rather than being skipped. The endpoints moved together because registration is a single generated
  call, not because the comparison was dropped — see *Delivered* under Unit 4.
- **Extend Bonjour discovery, LAN exposure, and bearer-token authentication per the source proposal's
  general sections on those topics.** Out of scope for this item, since the resident runner's channel
  is localhost-only today, as the Introduction states; a future item should propose these separately
  if a real-device or remote-driver use case that needs LAN exposure is ever adopted.

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
      endpoint against the legacy `Router` over a live socket. Three recorded wire differences, none
      observable to the driver: JSON replies carry `charset=utf-8`, a 400's `message` is the generic
      reason phrase, and the generated decoder rejects a few malformed-body shapes `Router` used to
      tolerate.
- [ ] Unit 5 — swap the listener onto Hummingbird, declaring the dependency and raising the floor to
      iOS 17 there, and remove `LegacyBackedTransport`, `HTTPServer.swift`, and `Router.swift`
      together with the parity suites that compare against them.

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
