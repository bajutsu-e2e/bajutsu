import Darwin
import Foundation
import XCTest
@testable import BajutsuRunner

/// The runner's HTTP server must survive what a driver-side timeout routinely does to it.
///
/// Every failure covered here reaches the Python driver the same way — the runner stops answering
/// `/health` — so the driver reports a timeout and cannot say why. The three causes are distinct:
/// an `accept()` failure that ended the accept loop while the listening socket stayed open (so
/// connections still succeed and are never answered), a reply to a peer that vanished first, which
/// raised `SIGPIPE` and killed the XCTest host outright, and a peer that connects without ever
/// sending a request, which held one of the eight connection slots for the life of the process.
final class HTTPServerResilienceTests: XCTestCase {
    // MARK: - The accept loop

    func testPerConnectionAcceptFailuresRetryWhileListenerFailuresEndTheLoop() {
        for code in [EINTR, ECONNABORTED, EAGAIN, EPROTO] {
            XCTAssertEqual(
                HTTPServer.acceptRetryDelay(code), 0,
                "errno \(code) concerns one connection, so the loop must retry at once"
            )
        }
        for code in [EMFILE, ENFILE, ENOMEM, ENOBUFS] {
            guard let delay = HTTPServer.acceptRetryDelay(code) else {
                return XCTFail("errno \(code) is transient exhaustion, so the loop must keep going")
            }
            XCTAssertGreaterThan(
                delay, 0, "errno \(code) needs a pause, or the retry spins against the exhaustion"
            )
        }
        XCTAssertNil(
            HTTPServer.acceptRetryDelay(EBADF),
            "a listening socket closed by stop() cannot serve again, so the loop must end"
        )
        XCTAssertNil(HTTPServer.acceptRetryDelay(EINVAL))
    }

    // MARK: - Socket options

    func testAcceptedConnectionsSuppressSigpipeAndBoundBothBlockingCalls() throws {
        let server = HTTPServer(receiveTimeout: 3, sendTimeout: 7) { _ in .json(200, [:]) }
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        try XCTSkipIf(fd < 0, "could not create a socket to configure")
        defer { close(fd) }

        server.configureConnection(fd)

        XCTAssertEqual(
            Self.intOption(fd, SO_NOSIGPIPE), 1,
            "without SO_NOSIGPIPE a reply to a departed peer kills the whole runner process"
        )
        XCTAssertEqual(Self.timeoutOption(fd, SO_RCVTIMEO), 3, accuracy: 0.01)
        XCTAssertEqual(Self.timeoutOption(fd, SO_SNDTIMEO), 7, accuracy: 0.01)
    }

    // MARK: - End to end

    /// A peer that vanishes while its handler is blocked used to take the process with it: the reply
    /// wrote to a closed socket, Darwin raised `SIGPIPE`, and its default disposition terminated the
    /// XCTest host. That is exactly the shape of a driver-side socket timeout landing on a handler
    /// still queued behind the main-thread lock, so this is the routine case rather than the exotic
    /// one. Without the fix this test does not fail — it kills the test process.
    func testServerKeepsServingAfterAPeerVanishesMidReply() throws {
        let release = DispatchSemaphore(value: 0)
        let entered = XCTestExpectation(description: "the slow handler was entered")
        // Large enough that the reply needs more than one `send`, so a write meets the vanished peer
        // even if the first one is still absorbed by the socket buffer.
        let bulk = Data(repeating: 0x41, count: 4 << 20)
        let server = HTTPServer { request in
            guard request.path == "/slow" else { return .json(200, ["status": "fast"]) }
            entered.fulfill()
            release.wait()
            return .png(bulk)
        }
        let port = try server.start()
        defer {
            release.signal()
            server.stop()
        }

        let abandoned = try Self.connect(port: port)
        Self.write(abandoned, "GET /slow HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        wait(for: [entered], timeout: 5)
        Self.resetAndClose(abandoned)  // an abortive close, as an abandoned connection looks

        release.signal()

        // Reaching this line at all is most of the assertion; the server must also still be serving.
        XCTAssertEqual(
            Self.get(port: port, path: "/fast"), 200,
            "the server must keep serving after a peer vanished mid-reply"
        )
    }

    /// Nine silent peers — one more than the eight concurrent handler slots — so an unbounded read
    /// would occupy every slot and leave `/health` unanswerable for as long as they stayed connected.
    func testSilentPeersCannotStarveTheServerOfConnectionSlots() throws {
        let server = HTTPServer(receiveTimeout: 0.3, sendTimeout: 1) { _ in
            .json(200, ["status": "ready"])
        }
        let port = try server.start()
        defer { server.stop() }

        var silent: [Int32] = []
        defer { silent.forEach { close($0) } }
        for _ in 0..<9 { silent.append(try Self.connect(port: port)) }
        // Let the accept loop take all nine before probing, so every slot is genuinely occupied and a
        // pass cannot come from the probe simply winning a race to an idle server.
        Thread.sleep(forTimeInterval: 0.2)

        XCTAssertEqual(
            Self.get(port: port, path: "/health", timeout: 10), 200,
            "a bounded read must free each slot so /health stays answerable"
        )
    }

    /// A header that never terminates must not be parsed as though it had: the request line the
    /// client was still sending is not one the server may act on.
    func testHeaderThatOverrunsTheCapIsRejectedWithoutReachingTheHandler() throws {
        let reached = DispatchSemaphore(value: 0)
        let server = HTTPServer(receiveTimeout: 3, sendTimeout: 3) { _ in
            reached.signal()
            return .json(200, ["status": "handled"])
        }
        let port = try server.start()
        defer { server.stop() }

        let fd = try Self.connect(port: port)
        defer { close(fd) }
        // No blank line anywhere, and longer than the 8 KiB header cap.
        Self.write(fd, "GET /tap HTTP/1.1\r\nX-Pad: " + String(repeating: "A", count: 9000))

        XCTAssertTrue(
            Self.readAll(fd).hasPrefix("HTTP/1.1 400 "),
            "an unterminated header must be answered 400, not parsed"
        )
        XCTAssertEqual(
            reached.wait(timeout: .now()), .timedOut, "the handler must never see a partial request"
        )
    }

    // MARK: - Raw socket helpers

    private enum SocketFailure: Error { case create, connect }

    private static func connect(port: UInt16) throws -> Int32 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw SocketFailure.create }
        var target = sockaddr_in()
        target.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        target.sin_family = sa_family_t(AF_INET)
        target.sin_port = port.bigEndian
        target.sin_addr.s_addr = inet_addr("127.0.0.1")
        let result = withUnsafePointer(to: &target) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                Darwin.connect(fd, sockPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard result == 0 else {
            close(fd)
            throw SocketFailure.connect
        }
        // The tests below write to a server that may close first, and read from one that may not
        // answer; without these the test process would hit the very defects it is checking for.
        var enabled: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
        var window = timeval(tv_sec: 10, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &window, socklen_t(MemoryLayout<timeval>.size))
        return fd
    }

    private static func write(_ fd: Int32, _ text: String) {
        let bytes = Array(text.utf8)
        _ = bytes.withUnsafeBytes { send(fd, $0.baseAddress, bytes.count, 0) }
    }

    private static func readAll(_ fd: Int32) -> String {
        var received = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let n = recv(fd, &buffer, buffer.count, 0)
            if n <= 0 { break }
            received.append(contentsOf: buffer[0..<n])
        }
        return String(data: received, encoding: .utf8) ?? ""
    }

    /// Close with an abortive reset rather than a graceful shutdown, so a later write to the socket
    /// meets `EPIPE` the way an abandoned connection makes it.
    private static func resetAndClose(_ fd: Int32) {
        var abortive = linger(l_onoff: 1, l_linger: 0)
        setsockopt(fd, SOL_SOCKET, SO_LINGER, &abortive, socklen_t(MemoryLayout<linger>.size))
        close(fd)
    }

    private static func intOption(_ fd: Int32, _ option: Int32) -> Int32 {
        var value: Int32 = 0
        var size = socklen_t(MemoryLayout<Int32>.size)
        getsockopt(fd, SOL_SOCKET, option, &value, &size)
        return value
    }

    private static func timeoutOption(_ fd: Int32, _ option: Int32) -> TimeInterval {
        var window = timeval()
        var size = socklen_t(MemoryLayout<timeval>.size)
        getsockopt(fd, SOL_SOCKET, option, &window, &size)
        return TimeInterval(window.tv_sec) + TimeInterval(window.tv_usec) / 1_000_000
    }

    private static func get(port: UInt16, path: String, timeout: TimeInterval = 4) -> Int? {
        let done = DispatchSemaphore(value: 0)
        var status: Int?
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = timeout
        let url = URL(string: "http://127.0.0.1:\(port)\(path)")!
        URLSession(configuration: config).dataTask(with: url) { _, response, _ in
            status = (response as? HTTPURLResponse)?.statusCode
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + timeout + 1)
        return status
    }
}
