import Foundation
import XCTest
@testable import BajutsuRunner

/// The transport is what Unit 4 inserts between the runner's socket and the generated handlers
/// (BE-0381). `TransportParityTests` checks that each endpoint still answers what it always did;
/// this suite checks the properties of the transport itself, which no per-endpoint comparison
/// would notice: that every operation the contract declares actually registered, and that BE-0287's
/// guarantee — `/health` stays answerable — survives the hop through `async` handlers.
///
/// The hand-rolled server's other concurrency guarantee — BE-0323's "no two XCUITest operations run
/// or *re-enter* concurrently", now held by `APIHandler.operations` — is pinned by
/// `APIHandlerConcurrencyTests` instead, which asserts the queue's serialness directly. It does not
/// belong to a live-server test: measured on this stack, a provider probe counting its own overlap
/// reports 1 even with that queue made `attributes: .concurrent` and genuinely running two
/// operations at once, because the second operation's `DispatchQueue.main.sync` cannot be drained
/// while the first's main block is executing. That suite's docstring carries the measurement.
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

    /// BE-0362's invariant on the one hop this transport adds: the task that produces every reply
    /// runs at `.userInitiated` because it *declares* it, not because of who called.
    ///
    /// The declaration is what a test has to check rather than assume. On this stack the caller is
    /// `HTTPServer`'s `connections` queue, which declares `.userInitiated` itself, so deleting the
    /// declaration changes nothing observable — and from Unit 5 the caller is a NIO event-loop thread
    /// at the default QoS, where deleting it silently drops the decode of the request and the encode
    /// of the reply to a lower band, the "scheduling delay indistinguishable from a dead runner"
    /// BE-0362 exists for. Driving `blocking` from a `.utility` queue is what tells the two apart.
    func testTheReplyTaskDeclaresUserInitiatedRatherThanInheriting() {
        let transport = LegacyBackedTransport()
        final class Box: @unchecked Sendable { var priority: TaskPriority? }
        let box = Box()
        let replied = XCTestExpectation(description: "the reply task ran")

        DispatchQueue.global(qos: .utility).async {
            _ = transport.blocking {
                box.priority = Task.currentPriority
                return .error(200, "probe")
            }
            replied.fulfill()
        }
        wait(for: [replied], timeout: 10)

        XCTAssertEqual(
            box.priority, .userInitiated,
            "the reply task must declare its priority, not inherit the caller's (BE-0362)"
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
