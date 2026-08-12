import Darwin
import Dispatch
import Foundation
import XCTest
@testable import BajutsuRunner

/// BE-0362: the server's two queues declare their quality of service instead of
/// inheriting it.
///
/// The driver decides whether the runner is alive by polling `/health`, so a
/// scheduling delay on the request path is indistinguishable from a dead runner.
/// Propagation already supplies `.userInitiated` when the server is started from
/// the XCUITest test method's main thread, which is exactly why the declaration
/// needs a test: without one, removing it costs nothing today and moves every
/// handler's priority the moment someone starts the server from somewhere else.
final class HTTPServerQoSTests: XCTestCase {
    /// Each queue is pinned separately, because a handler's priority stays
    /// `.userInitiated` as long as *either* queue declares it — losing one
    /// declaration is invisible to the behavioural test below.
    func testBothQueuesDeclareUserInitiated() {
        let server = HTTPServer { _ in .json(200, ["status": "ok"]) }

        XCTAssertEqual(server.queue.qos.qosClass, .userInitiated, "the accept loop's queue should declare .userInitiated")
        XCTAssertEqual(
            server.connections.qos.qosClass, .userInitiated,
            "the connection queue should declare .userInitiated"
        )
    }

    /// The guarantee the declaration buys: a start from a lower-QoS context no
    /// longer drags the handlers down with it.
    func testHandlerRunsAtUserInitiatedWhenStartedFromALowerQoSContext() throws {
        let handled = XCTestExpectation(description: "handler ran")
        var handlerQoS: DispatchQoS.QoSClass?
        let server = HTTPServer { _ in
            handlerQoS = DispatchQoS.QoSClass(rawValue: qos_class_self())
            handled.fulfill()
            return .json(200, ["status": "ok"])
        }
        defer { server.stop() }

        var port: UInt16 = 0
        var startFailure: Error?
        let started = DispatchSemaphore(value: 0)
        DispatchQueue.global(qos: .utility).async {
            do { port = try server.start() } catch { startFailure = error }
            started.signal()
        }
        XCTAssertEqual(started.wait(timeout: .now() + 5), .success, "the server should have started")
        if let startFailure { throw startFailure }

        _ = HTTPTestClient.get(port: port, path: "/health")
        wait(for: [handled], timeout: 5)

        XCTAssertEqual(
            handlerQoS, .userInitiated,
            "a handler should run at the queue's declared class, not the class its start inherited"
        )
    }
}
