import Foundation
import XCTest
@testable import BajutsuRunner

/// BE-0287: a handler blocked mid-request must not stop the server from
/// answering a concurrent one.
///
/// The multi-touch flake was a wedged loopback server, not a dead process: a
/// long two-finger gesture held the single-threaded accept loop, so a `/health`
/// poll issued during the gesture was refused (`Connection refused`) and the
/// driver could not tell "runner busy" from "runner dead". The server must keep
/// servicing connections while one handler is in flight.
final class HTTPServerConcurrencyTests: XCTestCase {
    func testConcurrentRequestServedWhileHandlerBlocked() throws {
        let release = DispatchSemaphore(value: 0)
        let slowEntered = XCTestExpectation(description: "slow handler entered")
        let server = HTTPServer { request in
            if request.path == "/slow" {
                slowEntered.fulfill()
                release.wait()
                return .json(200, ["status": "slow-done"])
            }
            return .json(200, ["status": "fast"])
        }
        let port = try server.start()
        defer {
            release.signal()
            server.stop()
        }

        // Occupy the server with a request that blocks until we release it.
        DispatchQueue.global().async { _ = HTTPTestClient.get(port: port, path: "/slow") }
        wait(for: [slowEntered], timeout: 5)

        // While /slow is blocked, a concurrent /fast must still be answered.
        let status = HTTPTestClient.get(port: port, path: "/fast").status
        XCTAssertEqual(status, 200, "a concurrent request should be served while another handler is blocked")
    }
}
