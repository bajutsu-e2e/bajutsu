import Darwin
import Foundation

struct HTTPRequest {
    let method: String
    let path: String
    let body: Data?
}

struct HTTPResponse {
    let statusCode: Int
    let contentType: String
    let body: Data

    static func json(_ statusCode: Int = 200, _ object: [String: Any]) -> HTTPResponse {
        let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data()
        return HTTPResponse(statusCode: statusCode, contentType: "application/json", body: data)
    }

    static func png(_ data: Data) -> HTTPResponse {
        HTTPResponse(statusCode: 200, contentType: "image/png", body: data)
    }

    static func error(_ statusCode: Int, _ message: String) -> HTTPResponse {
        json(statusCode, ["status": "error", "message": message])
    }
}

typealias RequestHandler = (HTTPRequest) -> HTTPResponse

final class HTTPServer {
    // A request's bytes follow its connect over loopback within microseconds, so a read that stalls
    // this long is a peer that died or never sent one — never a merely slow one. Unbounded, such a
    // read holds one of the eight connection slots below for the life of the process, and enough of
    // them starve `/health`, which the driver then reads as a dead runner (BE-0287's whole point is
    // that `/health` stays answerable).
    static let defaultReceiveTimeout: TimeInterval = 10
    // A reply is written to a peer that is waiting for it, so only a peer that stopped reading — yet
    // left the connection open — stalls a send. The driver's own windows are 15s for a read and 30s
    // for an actuation (`_SOCKET_TIMEOUT_SECONDS` / `_ACTUATION_TIMEOUT_SECONDS` in
    // `bajutsu/drivers/xcuitest.py`), so this matches the wider of the two rather than exceeding it.
    // That is enough because the only reply large enough to approach the bound is a screenshot's
    // PNG, which travels the 15s read path; an actuation's reply is a few bytes of JSON.
    static let defaultSendTimeout: TimeInterval = 30

    private let handler: RequestHandler
    private let receiveTimeout: TimeInterval
    private let sendTimeout: TimeInterval
    private var listenFD: Int32 = -1
    private let lock = NSLock()
    // .userInitiated is declared, not inherited (BE-0362). Every request exists because the
    // driver is blocked on the reply — the /health polls that decide whether the runner is
    // alive among them — which is what the class denotes. Propagation from the XCUITest test
    // method's main thread already lands both queues here today, so declaring it changes no
    // priority; it stops the priority of the liveness-deciding path from moving silently when
    // someone later moves where start() is submitted from.
    private let queue = DispatchQueue(label: "bajutsu.runner.http", qos: .userInitiated)
    // Each accepted connection is handled here so the accept loop can return to
    // accept() at once. A long main-thread-bound gesture (BE-0287) must not wedge
    // the whole server: /health touches no shared state, so it has to stay
    // answerable while the gesture holds the main thread — that is what lets the
    // driver tell "runner busy" from "runner dead".
    private let connections = DispatchQueue(label: "bajutsu.runner.http.conn", qos: .userInitiated, attributes: .concurrent)
    // The declared classes, readable by the tests without handing out the queues themselves: the
    // accept loop owns `queue` for the server's lifetime, and `connections` may be entered only
    // through the accept loop, or the handler cap above stops bounding anything. A declaration
    // nothing reads back is the same silent drift the declaration itself guards against.
    var declaredQoS: (accept: DispatchQoS, connections: DispatchQoS) { (queue.qos, connections.qos) }
    // Driver calls are sequential and health polls are sporadic, so the realistic
    // peak is well under 8 simultaneous handlers; cap at 8 to bound concurrent
    // handler execution if polls pile up during a long gesture.
    private let connectionSemaphore = DispatchSemaphore(value: 8)
    private(set) var port: UInt16 = 0

    init(
        receiveTimeout: TimeInterval = HTTPServer.defaultReceiveTimeout,
        sendTimeout: TimeInterval = HTTPServer.defaultSendTimeout,
        handler: @escaping RequestHandler
    ) {
        self.receiveTimeout = receiveTimeout
        self.sendTimeout = sendTimeout
        self.handler = handler
    }

    @discardableResult
    func start(port requestedPort: UInt16 = 0) throws -> UInt16 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw ServerError.socketCreationFailed }

        var reuse: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = requestedPort.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let bindResult = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                bind(fd, sockPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else {
            close(fd)
            throw ServerError.bindFailed(errno)
        }

        // Backlog of 1 was too small: a burst of driver + health-poll connections
        // arriving while the accept loop was busy could exhaust it and refuse a
        // poll outright (BE-0287). Give the kernel room to queue them.
        guard listen(fd, 16) == 0 else {
            close(fd)
            throw ServerError.listenFailed(errno)
        }

        var boundAddr = sockaddr_in()
        var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &boundAddr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                getsockname(fd, sockPtr, &addrLen)
            }
        }
        port = UInt16(bigEndian: boundAddr.sin_port)
        listenFD = fd

        queue.async { [weak self] in self?.acceptLoop() }
        return port
    }

    func stop() {
        let fd = lock.withLock { () -> Int32 in
            let fd = listenFD
            listenFD = -1
            return fd
        }
        guard fd >= 0 else { return }
        close(fd)
    }

    private func acceptLoop() {
        while true {
            let fd = lock.withLock { listenFD }
            guard fd >= 0 else { break }
            var clientAddr = sockaddr_in()
            var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)
            var failure: Int32 = 0
            let clientFD = withUnsafeMutablePointer(to: &clientAddr) { ptr in
                ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr -> Int32 in
                    let result = accept(fd, sockPtr, &addrLen)
                    failure = errno  // read before anything else can overwrite it
                    return result
                }
            }
            guard clientFD >= 0 else {
                guard let backoff = Self.acceptRetryDelay(failure) else { break }
                if backoff > 0 { Thread.sleep(forTimeInterval: backoff) }
                continue
            }
            configureConnection(clientFD)
            connections.async { [weak self] in
                guard let self else { close(clientFD); return }
                connectionSemaphore.wait()
                defer { connectionSemaphore.signal() }
                handleConnection(clientFD)
                close(clientFD)
            }
        }
    }

    /// How long to wait before retrying `accept()` after it failed with *code*, or `nil` when the
    /// failure is terminal and the loop must end.
    ///
    /// Ending the loop on *every* failure — the original behaviour — wedges the server rather than
    /// stopping it. The listening socket stays open, so the kernel keeps completing handshakes into
    /// the backlog: every connection the driver opens still succeeds, and none is ever accepted or
    /// answered. `/health` included, which leaves a live runner indistinguishable from a dead one and
    /// costs the driver a full timeout ladder per call. Only a failure of the listening socket itself
    /// justifies that, so a per-connection failure retries instead.
    ///
    /// Internal rather than private so a test can enumerate the classification directly: these are
    /// failures no test can reliably provoke against a real listening socket.
    static func acceptRetryDelay(_ code: Int32) -> TimeInterval? {
        switch code {
        // None of these say anything about the listening socket: a signal interrupted the call, or
        // the peer went away between completing its handshake and our accepting it. The latter is the
        // one to expect here — a driver-side health poll that hits its own timeout and disconnects
        // while still queued in the backlog produces exactly it.
        case EINTR, ECONNABORTED, EAGAIN, EPROTO:
            return 0
        // Descriptor or memory exhaustion: transient, but retrying at once would spin against a
        // condition that only time relieves, so pause briefly first.
        case EMFILE, ENFILE, ENOMEM, ENOBUFS:
            return 0.05
        // `EBADF` once `stop()` has closed the listening socket, plus anything unrecognised: the
        // socket will not serve again, so end the loop as before.
        default:
            return nil
        }
    }

    /// Apply the socket options every accepted connection needs, before any handler touches it.
    ///
    /// `SO_NOSIGPIPE` is the load-bearing one. Darwin raises `SIGPIPE` on a write to a socket whose
    /// peer has closed, and the signal's default disposition terminates the process — so a driver-side
    /// timeout that closes the connection while the handler is still blocked on the main thread would
    /// kill the whole XCTest host the moment that handler finally replied, taking the runner down with
    /// it. That race is routine here rather than exotic: the driver's read and actuation windows are
    /// tighter than a contended host's slowest operation, and `APIHandler` deliberately queues handlers
    /// behind one main-thread lock. The option turns such a write into a plain `EPIPE`, which
    /// `sendAll` already treats as "stop writing". The timeouts then bound the two blocking calls a
    /// handler makes, so a peer that vanishes without closing cannot hold a connection slot for ever.
    ///
    /// Internal rather than private so a test can read the options back off a socket it owns, which
    /// is the only way to assert the timeouts landed at all.
    func configureConnection(_ fd: Int32) {
        var enabled: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
        setTimeout(fd, SO_RCVTIMEO, receiveTimeout)
        setTimeout(fd, SO_SNDTIMEO, sendTimeout)
    }

    private func setTimeout(_ fd: Int32, _ option: Int32, _ seconds: TimeInterval) {
        let whole = seconds.rounded(.down)
        var window = timeval(
            tv_sec: Int(whole),
            tv_usec: Int32((seconds - whole) * 1_000_000)
        )
        setsockopt(fd, SOL_SOCKET, option, &window, socklen_t(MemoryLayout<timeval>.size))
    }

    private func handleConnection(_ fd: Int32) {
        guard let request = readRequest(fd) else {
            writeResponse(fd, .error(400, "bad request"))
            return
        }
        let response = handler(request)
        writeResponse(fd, response)
    }

    // MARK: - HTTP parsing

    private let maxHeaderSize = 8192
    private let maxBodySize = 65536

    private func readRequest(_ fd: Int32) -> HTTPRequest? {
        var headerBuf = Data()
        var singleByte = [UInt8](repeating: 0, count: 1)
        var terminated = false

        while headerBuf.count < maxHeaderSize {
            // `recv` now returns -1 with `EAGAIN` once the receive timeout elapses, which lands here
            // with the same verdict as a peer that closed: the request never fully arrived.
            let n = recv(fd, &singleByte, 1, 0)
            if n <= 0 { return nil }
            headerBuf.append(singleByte[0])
            if headerBuf.count >= 4,
               headerBuf.suffix(4) == Data([0x0D, 0x0A, 0x0D, 0x0A]) {
                terminated = true
                break
            }
        }
        // Reaching the cap without the blank-line terminator means the header was truncated or
        // oversized. Parsing it anyway would act on a request line the client never finished sending,
        // so report it as unparseable and let the caller answer 400.
        guard terminated else { return nil }

        guard let headerString = String(data: headerBuf, encoding: .utf8) else { return nil }
        let lines = headerString.components(separatedBy: "\r\n")
        guard let requestLine = lines.first else { return nil }
        let parts = requestLine.split(separator: " ", maxSplits: 2)
        guard parts.count >= 2 else { return nil }

        let method = String(parts[0])
        let path = String(parts[1])

        // A length that does not parse, is negative, or exceeds the cap describes a body we cannot
        // read faithfully. Clamping it to the cap — the earlier behaviour — would read the first
        // `maxBodySize` bytes, find the count satisfied, and dispatch a truncated body as a complete
        // one, so reject the request instead and let the caller answer 400.
        var contentLength = 0
        for line in lines.dropFirst() where line.lowercased().hasPrefix("content-length:") {
            let value = line.dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces)
            guard let declared = Int(value), declared >= 0, declared <= maxBodySize else { return nil }
            contentLength = declared
        }

        var body: Data?
        if contentLength > 0 {
            var bodyBuf = Data(count: contentLength)
            var totalRead = 0
            bodyBuf.withUnsafeMutableBytes { ptr in
                guard let base = ptr.baseAddress else { return }
                while totalRead < contentLength {
                    let n = recv(fd, base + totalRead, contentLength - totalRead, 0)
                    if n <= 0 { break }
                    totalRead += n
                }
            }
            // The same invariant as the unterminated header above, applied to the body: a client that
            // stopped short never finished sending this request, so it must not reach a handler.
            // Returning it with a nil body would leave that call to each route, and `/selectAll` and
            // `/copy` read no body at all — they would actuate the device on a truncated request.
            // The receive timeout above makes the short read reachable from a peer that merely
            // stalls, not only from one that closed, which is what makes the guard load-bearing.
            guard totalRead == contentLength else { return nil }
            body = bodyBuf
        }

        return HTTPRequest(method: method, path: path, body: body)
    }

    private func writeResponse(_ fd: Int32, _ response: HTTPResponse) {
        let statusText: String
        switch response.statusCode {
        case 200: statusText = "OK"
        case 400: statusText = "Bad Request"
        case 404: statusText = "Not Found"
        case 500: statusText = "Internal Server Error"
        default: statusText = "Unknown"
        }

        var header = "HTTP/1.1 \(response.statusCode) \(statusText)\r\n"
        header += "Content-Type: \(response.contentType)\r\n"
        header += "Content-Length: \(response.body.count)\r\n"
        header += "Connection: close\r\n"
        header += "\r\n"

        sendAll(fd, Data(header.utf8))
        sendAll(fd, response.body)
    }

    private func sendAll(_ fd: Int32, _ data: Data) {
        data.withUnsafeBytes { ptr in
            guard var base = ptr.baseAddress else { return }
            var remaining = data.count
            while remaining > 0 {
                let n = send(fd, base, remaining, 0)
                if n <= 0 { break }
                base += n
                remaining -= n
            }
        }
    }

    enum ServerError: Error {
        case socketCreationFailed
        case bindFailed(Int32)
        case listenFailed(Int32)
    }
}
