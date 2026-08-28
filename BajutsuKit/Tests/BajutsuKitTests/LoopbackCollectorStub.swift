import Darwin
import Foundation

// Only the control channel's tests need a collector to talk to, so the stub is selected by the same
// compilation condition the channel is.
#if BAJUTSU_ENABLE_CONTROL_CHANNEL

/// A stand-in for the two endpoints `NetworkCollector` serves the control channel (BE-0365), so the
/// app-side poll round can be tested over a real socket rather than only in its pure pieces.
///
/// It answers the collector's contract and nothing more: `GET /commands` hands over whatever is
/// queued and empties it, `POST /commands/ack` takes the app's report, and every other path is a
/// 404 — the same shape unit 1 landed. Each request is recorded whole, so a test can assert the
/// method, the path, the bearer token, and the body that actually went over the wire.
final class LoopbackCollectorStub {
    struct Request {
        let method: String
        let path: String
        let authorization: String
        let body: Data
    }

    /// Called on the server's own thread once an acknowledgement has been answered, so a test can
    /// wait on the round trip finishing instead of on a duration.
    var onAcknowledge: (() -> Void)?

    private let lock = NSLock()
    private var listenFD: Int32 = -1
    private var recorded: [Request] = []
    private var pending = "[]"
    private let queue = DispatchQueue(label: "bajutsu.tests.collector-stub")

    var requests: [Request] { lock.withLock { recorded } }

    /// Queue the JSON body the next `GET /commands` will hand over.
    func enqueue(_ commandsJSON: String) {
        lock.withLock { pending = commandsJSON }
    }

    /// Bind an ephemeral loopback port and start serving. Returns the port that was bound.
    func start() throws -> UInt16 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw Failure.socket }
        var reuse: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = 0  // let the OS choose, so parallel test runs cannot collide
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0, listen(fd, 8) == 0 else {
            close(fd)
            throw Failure.bind
        }

        var actual = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let named = withUnsafeMutablePointer(to: &actual) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &length) }
        }
        guard named == 0 else {
            close(fd)
            throw Failure.bind
        }

        lock.withLock { listenFD = fd }
        queue.async { [weak self] in self?.acceptLoop() }
        return UInt16(bigEndian: actual.sin_port)
    }

    func stop() {
        let fd = lock.withLock { () -> Int32 in
            let previous = listenFD
            listenFD = -1
            return previous
        }
        if fd >= 0 { close(fd) }
    }

    // --- serving ---

    private func acceptLoop() {
        while true {
            let fd = lock.withLock { listenFD }
            guard fd >= 0 else { return }
            let client = accept(fd, nil, nil)
            if client < 0 {
                if errno == EINTR { continue }
                return  // `stop()` closed the listener out from under us
            }
            var noSigPipe: Int32 = 1
            setsockopt(
                client, SOL_SOCKET, SO_NOSIGPIPE, &noSigPipe, socklen_t(MemoryLayout<Int32>.size)
            )
            handle(client)
            close(client)
        }
    }

    private func handle(_ fd: Int32) {
        guard let request = read(fd) else { return }
        lock.withLock { recorded.append(request) }
        switch (request.method, request.path) {
        case ("GET", "/commands"):
            let body = lock.withLock { () -> String in
                let queued = pending
                pending = "[]"  // the collector's drain empties the queue in the same step
                return queued
            }
            write(fd, status: "200 OK", body: Data(body.utf8))
        case ("POST", "/commands/ack"):
            write(fd, status: "204 No Content", body: Data())
            onAcknowledge?()
        default:
            write(fd, status: "404 Not Found", body: Data())
        }
    }

    private func read(_ fd: Int32) -> Request? {
        var raw = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        var headerEnd: Range<Data.Index>?
        while headerEnd == nil {
            let count = recv(fd, &buffer, buffer.count, 0)
            guard count > 0 else { return nil }
            raw.append(contentsOf: buffer[0..<count])
            headerEnd = raw.range(of: Data("\r\n\r\n".utf8))
        }
        guard let separator = headerEnd,
              let head = String(data: raw[..<separator.lowerBound], encoding: .utf8)
        else { return nil }

        let lines = head.components(separatedBy: "\r\n")
        let requestLine = lines.first?.components(separatedBy: " ") ?? []
        guard requestLine.count >= 2 else { return nil }

        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":") else { continue }
            let name = line[..<colon].lowercased()
            headers[name] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }

        var body = raw[separator.upperBound...]
        let expected = Int(headers["content-length"] ?? "") ?? 0
        while body.count < expected {
            let count = recv(fd, &buffer, buffer.count, 0)
            guard count > 0 else { break }
            body.append(contentsOf: buffer[0..<count])
        }
        return Request(
            method: requestLine[0],
            path: requestLine[1],
            authorization: headers["authorization"] ?? "",
            body: Data(body)
        )
    }

    private func write(_ fd: Int32, status: String, body: Data) {
        // `Connection: close` and an explicit length keep the client from waiting on a reuse this
        // one-connection-at-a-time stub never offers.
        let head = """
            HTTP/1.1 \(status)\r
            Content-Type: application/json\r
            Content-Length: \(body.count)\r
            Connection: close\r
            \r

            """
        var out = Data(head.utf8)
        out.append(body)
        out.withUnsafeBytes { bytes in
            _ = Darwin.send(fd, bytes.baseAddress, bytes.count, 0)
        }
    }

    enum Failure: Error {
        case socket
        case bind
    }
}

#endif
