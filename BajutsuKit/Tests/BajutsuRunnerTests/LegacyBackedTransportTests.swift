import Foundation
import XCTest
@testable import BajutsuRunner

/// The transport is what Unit 4 inserts between the runner's socket and the generated handlers
/// (BE-0381). `TransportParityTests` checks that each endpoint still answers what it always did;
/// this suite checks the properties of the transport itself, which no per-endpoint comparison
/// would notice: that every operation the contract declares actually registered, and that BE-0287's
/// guarantee — `/health` stays answerable — survives the hop through `async` handlers.
///
/// The hand-rolled server's other concurrency guarantee, BE-0323's "no two XCUITest operations run
/// or *re-enter* concurrently", is deliberately not asserted here even though `APIHandler.operations`
/// is now its sole holder, because in-process it is not observable. Measured on this stack: a probe
/// whose `queryElements` spins the main run loop and records peak overlap, driven over the live
/// socket by two concurrent `/elements`, reports `maxConcurrent == 1` unchanged when `operations` is
/// given `attributes: .concurrent` and when it is dropped for a bare hop to main. Under `swift test`
/// the operation's own hold on the main thread already serializes everything — `RunLoop.run(until:)`
/// does not drain the main dispatch queue, which only XCUITest's run loop does, which is why
/// BE-0323's abort is CI-only in the first place. An assertion here would therefore pass against
/// both regressions; the invariant rests on the on-device lanes plus the argument recorded on
/// `APIHandler.operations`, and `RouterConcurrencyTests` pins only what is observable of it (that
/// the guard is handed on rather than wedging the server).
final class LegacyBackedTransportTests: XCTestCase {
    /// Registration is one generated call covering all sixteen operations, so an operation dropped
    /// from `openapi.yaml` — or a path renamed in it — would leave the driver with a 404 discovered
    /// only on a device. Pinning the table against the contract's own paths catches it here.
    func testEveryContractOperationRegisters() throws {
        let transport = LegacyBackedTransport()
        try APIHandler(provider: FakeElementProvider()).registerHandlers(on: transport)

        XCTAssertEqual(
            transport.registeredRoutes,
            [
                "GET /health", "GET /elements", "GET /screen", "GET /screenshot",
                "POST /tap", "POST /isHittable", "POST /gesture", "POST /swipe", "POST /scroll",
                "POST /setPickerValue",
                "POST /type", "POST /deleteText", "POST /selectAll", "POST /copy",
                "POST /systemAlert/query", "POST /systemAlert/tap",
            ]
        )
    }

    /// BE-0287's invariant, restated on the stack Unit 4 builds: `/health` reports whether the
    /// runner is serving, so it has to stay answerable while a long operation holds the main thread
    /// — that is what lets the driver tell "runner busy" from "runner dead".
    ///
    /// The migration could have lost this in two places at once. `APIHandler` routes every other
    /// operation through one serial queue and deliberately keeps `/health` off it; the transport
    /// blocks a connection thread per request, so it must not serialize them. Only a live server
    /// exercises both together.
    func testHealthIsAnsweredWhileAnOperationHoldsTheMainThread() throws {
        let provider = FakeElementProvider()
        let server = RunnerServer(provider: provider)
        let port = try server.start()
        defer { server.stop() }

        let mainThreadHeld = DispatchSemaphore(value: 0)
        let releaseMainThread = DispatchSemaphore(value: 0)
        provider.beforeQueryElements = {
            mainThreadHeld.signal()
            releaseMainThread.wait()
        }

        // Both requests run off the main thread, because the main thread's job here is to spin the
        // run loop: that is what lets `/elements` reach it and then stay stuck there.
        let probed = XCTestExpectation(description: "health probed during the held operation")
        var healthStatus: Int?
        DispatchQueue.global().async { _ = HTTPTestClient.get(port: port, path: "/elements") }
        DispatchQueue.global().async {
            mainThreadHeld.wait()
            healthStatus = HTTPTestClient.get(port: port, path: "/health").status
            releaseMainThread.signal()
            probed.fulfill()
        }

        wait(for: [probed], timeout: 10)
        XCTAssertEqual(healthStatus, 200, "/health must answer while an operation holds the main thread")
    }
}
