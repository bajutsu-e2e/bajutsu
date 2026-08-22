import Darwin
import Foundation
import XCTest

@testable import BajutsuKit

/// Unit tests for the in-app `nativeZ` responder's gate and its token check (BE-0355).
///
/// The responder is a listener inside the app under test, and iOS loopback is not isolated between
/// apps, so "answers only the host that launched this run" is a property worth a test rather than a
/// comment. What it measures needs UIKit and a rendered screen, so that half is covered on device.
final class BajutsuZOrderTests: XCTestCase {
    override func tearDown() {
        BajutsuZOrder.stop()
        super.tearDown()
    }

    func testItStaysOffWithoutAPort() throws {
        let port = try freePort()
        BajutsuZOrder.startIfEnabled(environment: ["BAJUTSU_ZORDER_TOKEN": "t"])
        XCTAssertNil(status(port: port, token: "t"), "nothing should be listening")
    }

    func testItStaysOffWithoutAToken() throws {
        let port = try freePort()
        BajutsuZOrder.startIfEnabled(environment: ["BAJUTSU_ZORDER_PORT": String(port)])
        XCTAssertNil(status(port: port, token: nil), "an unauthenticated responder must not start")
    }

    func testItStaysOffForAnEmptyToken() throws {
        let port = try freePort()
        BajutsuZOrder.startIfEnabled(
            environment: ["BAJUTSU_ZORDER_PORT": String(port), "BAJUTSU_ZORDER_TOKEN": ""])
        XCTAssertNil(status(port: port, token: ""), "an empty secret is no secret")
    }

    func testItAnswersOnlyTheTokenItWasLaunchedWith() throws {
        let port = try freePort()
        BajutsuZOrder.startIfEnabled(
            environment: ["BAJUTSU_ZORDER_PORT": String(port), "BAJUTSU_ZORDER_TOKEN": "secret"])
        XCTAssertEqual(status(port: port, token: "secret"), 200)
        XCTAssertEqual(status(port: port, token: "wrong"), 401)
        XCTAssertEqual(status(port: port, token: nil), 401)
    }

    func testItRefusesAPathItDoesNotServe() throws {
        let port = try freePort()
        BajutsuZOrder.startIfEnabled(
            environment: ["BAJUTSU_ZORDER_PORT": String(port), "BAJUTSU_ZORDER_TOKEN": "secret"])
        XCTAssertEqual(status(port: port, token: "secret", path: "/elements"), 404)
    }

    // MARK: - Helpers

    /// The HTTP status of one `/zorder` request, or nil when nothing is listening on *port*.
    private func status(port: UInt16, token: String?, path: String = "/zorder") -> Int? {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)\(path)")!)
        request.timeoutInterval = 5
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let done = expectation(description: "responded")
        var code: Int?
        URLSession.shared.dataTask(with: request) { _, response, _ in
            code = (response as? HTTPURLResponse)?.statusCode
            done.fulfill()
        }.resume()
        wait(for: [done], timeout: 10)
        return code
    }

    /// An ephemeral loopback port, closed again so the responder can bind it.
    private func freePort() throws -> UInt16 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        try XCTSkipIf(fd < 0, "no socket available")
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = 0
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        try XCTSkipIf(bound != 0, "could not bind a loopback port")
        var assigned = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &assigned) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getsockname(fd, $0, &length)
            }
        }
        return assigned.sin_port.bigEndian
    }
}
