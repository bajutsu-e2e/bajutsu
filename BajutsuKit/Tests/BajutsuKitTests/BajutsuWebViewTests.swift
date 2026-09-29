import Darwin
import Foundation
import XCTest

@testable import BajutsuKit

/// Unit tests for the in-app WebView bridge's connection hardening (BE-0037).
///
/// The bridge is a listener inside the app under test, so a misbehaving peer must cost at most one
/// connection — never the app (SIGPIPE) or the bridge itself (a stalled `recv`). The DOM endpoints
/// need a rendered `WKWebView`, so they are covered on device.
final class BajutsuWebViewTests: XCTestCase {
    override func tearDown() {
        BajutsuWebView.stop()
        super.tearDown()
    }

    func testItStaysOffWithoutAPort() throws {
        let port = try freePort()
        BajutsuWebView.startIfEnabled(environment: [:])
        XCTAssertNil(status(port: port), "nothing should be listening")
    }

    func testItAnswersHealth() throws {
        let port = try start()
        XCTAssertEqual(status(port: port), 200)
    }

    func testAPeerThatResetsBeforeTheReplyDoesNotKillTheProcess() throws {
        let port = try start()
        // An abortive close (linger 0) resets the connection, so the bridge's 400 reply is written
        // to a dead peer — the write that raises SIGPIPE, and so ends this test process, without
        // SO_NOSIGPIPE. The bridge serves one connection at a time, so the next answer proves the
        // reset one was already handled.
        let fd = try connect(port: port)
        var linger = Darwin.linger(l_onoff: 1, l_linger: 0)
        setsockopt(fd, SOL_SOCKET, SO_LINGER, &linger, socklen_t(MemoryLayout<Darwin.linger>.size))
        close(fd)
        XCTAssertEqual(status(port: port), 200)
    }

    func testAStalledPeerDoesNotWedgeTheBridge() throws {
        let port = try start()
        // Held open and silent: without a receive timeout the bridge's `recv` waits on it forever
        // and every later request goes unanswered.
        let fd = try connect(port: port)
        defer { close(fd) }
        XCTAssertEqual(status(port: port), 200)
    }

    // MARK: - Helpers

    private func start() throws -> UInt16 {
        let port = try freePort()
        BajutsuWebView.startIfEnabled(environment: ["BAJUTSU_WEBVIEW_PORT": String(port)])
        return port
    }

    /// The HTTP status of one request, or nil when nothing answered on *port*.
    private func status(port: UInt16, path: String = "/health") -> Int? {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)\(path)")!)
        request.timeoutInterval = 5
        let done = expectation(description: "responded")
        var code: Int?
        URLSession.shared.dataTask(with: request) { _, response, _ in
            code = (response as? HTTPURLResponse)?.statusCode
            done.fulfill()
        }.resume()
        wait(for: [done], timeout: 10)
        return code
    }

    /// A raw loopback connection to *port*, for peers `URLSession` will not impersonate.
    private func connect(port: UInt16) throws -> Int32 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        try XCTSkipIf(fd < 0, "no socket available")
        var addr = loopback(port: port)
        let connected = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        if connected != 0 {
            close(fd)
            XCTFail("could not connect to the bridge on port \(port)")
        }
        return fd
    }

    /// An ephemeral loopback port, closed again so the bridge can bind it.
    private func freePort() throws -> UInt16 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        try XCTSkipIf(fd < 0, "no socket available")
        defer { close(fd) }
        var addr = loopback(port: 0)
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

    private func loopback(port: UInt16) -> sockaddr_in {
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        return addr
    }
}
