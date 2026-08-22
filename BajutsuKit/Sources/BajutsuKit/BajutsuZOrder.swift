import Darwin
import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// The app-side half of BE-0355's `nativeZ`: a synchronous responder that reports each element's
/// real front-to-back position, measured from the app's own view tree.
///
/// `BajutsuScreen` and `BajutsuNet` push to a host collector when something happens. This one is
/// asked: the driver calls it alongside its own `/elements` query and gets an answer computed fresh
/// for that moment, because a stacking order replayed from an earlier push would describe a screen
/// that has since changed.
///
/// Being a listener rather than a sender, it binds loopback only and requires the per-run token the
/// host injected beside the port — iOS loopback is not isolated between apps, and the reply names
/// every element on screen.
public enum BajutsuZOrder {
    private static var server: _ZOrderServer?

    /// Start the responder when the host injected a port; a no-op otherwise.
    ///
    /// A failure to bind is swallowed on purpose: reporting `nativeZ` is diagnostic, and an app that
    /// cannot open the socket must still run its own scenario. The driver reads the refused
    /// connection as the honest absence it is.
    static func startIfEnabled(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        guard let raw = environment["BAJUTSU_ZORDER_PORT"],
              let port = UInt16(raw),
              let token = environment["BAJUTSU_ZORDER_TOKEN"], !token.isEmpty else { return }
        let srv = _ZOrderServer(token: token)
        do {
            try srv.start(port: port)
            server = srv
        } catch {
            // Non-fatal: the responder is test infrastructure, not app functionality.
        }
    }

    static func stop() {
        server?.stop()
        server = nil
    }

    #if canImport(UIKit)
    /// Every identified element on screen, paired with its own front-to-back position.
    ///
    /// The position is an ordinal over the real compositing order, counted back to front, so a larger
    /// value is closer to the viewer. It is not `CALayer.zPosition`: Apple documents that property as
    /// the wrong tool for sibling order, and it reads zero across a flat layout. Sibling order comes
    /// from the `subviews` array, with `zPosition` breaking the order only where an app actually set
    /// one — which is what UIKit itself composites.
    @MainActor
    static func positions() -> [[String: Any]] {
        var records: [[String: Any]] = []
        var ordinal = 0
        for window in orderedWindows() {
            visit(window, ordinal: &ordinal, into: &records)
        }
        return records
    }

    /// The app's windows, back to front: `windowLevel` first, then the order the scene reports.
    @MainActor
    private static func orderedWindows() -> [UIWindow] {
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
        return windows.enumerated()
            .sorted { lhs, rhs in
                if lhs.element.windowLevel != rhs.element.windowLevel {
                    return lhs.element.windowLevel < rhs.element.windowLevel
                }
                return lhs.offset < rhs.offset
            }
            .map(\.element)
    }

    @MainActor
    private static func visit(_ view: UIView, ordinal: inout Int, into records: inout [[String: Any]]) {
        ordinal += 1
        if let id = view.accessibilityIdentifier, !id.isEmpty {
            records.append(["identifier": id, "nativeZ": ordinal])
        }
        // A container that vends its own elements instead of subviews: they carry no layer of their
        // own and composite exactly where the container does, so they take its position.
        for case let element as UIAccessibilityIdentification in view.accessibilityElements ?? [] {
            guard let id = element.accessibilityIdentifier, !id.isEmpty else { continue }
            records.append(["identifier": id, "nativeZ": ordinal])
        }
        for child in paintOrdered(view.subviews) {
            visit(child, ordinal: &ordinal, into: &records)
        }
    }

    /// `subviews` in the order UIKit composites them: array order, with a set `zPosition` overriding.
    @MainActor
    private static func paintOrdered(_ subviews: [UIView]) -> [UIView] {
        subviews.enumerated()
            .sorted { lhs, rhs in
                if lhs.element.layer.zPosition != rhs.element.layer.zPosition {
                    return lhs.element.layer.zPosition < rhs.element.layer.zPosition
                }
                return lhs.offset < rhs.offset
            }
            .map(\.element)
    }
    #endif
}

// MARK: - Minimal HTTP server

private final class _ZOrderServer {
    private var listenFD: Int32 = -1
    private let lock = NSLock()
    private let queue = DispatchQueue(label: "bajutsu.zorder.responder")
    private let token: String

    init(token: String) {
        self.token = token
    }

    @discardableResult
    func start(port: UInt16) throws -> UInt16 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw _Error.socketFailed }

        var reuse: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let result = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                bind(fd, sockPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard result == 0 else {
            close(fd)
            throw _Error.bindFailed
        }
        guard listen(fd, 4) == 0 else {
            close(fd)
            throw _Error.listenFailed
        }

        listenFD = fd
        queue.async { [weak self] in self?._acceptLoop() }
        return port
    }

    func stop() {
        let fd = lock.withLock { () -> Int32 in
            let old = listenFD; listenFD = -1; return old
        }
        guard fd >= 0 else { return }
        close(fd)
    }

    private func _acceptLoop() {
        while true {
            let fd = lock.withLock { listenFD }
            guard fd >= 0 else { break }
            var clientAddr = sockaddr_in()
            var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)
            let clientFD = withUnsafeMutablePointer(to: &clientAddr) { ptr in
                ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                    accept(fd, sockPtr, &addrLen)
                }
            }
            if clientFD < 0 {
                // EINTR is routine on a device under test (signals, the debugger attaching) and
                // means nothing about the socket itself — retrying is what every other accept
                // loop does. Anything else here means the listening socket is gone (`stop()`'s
                // own `close` produces EBADF), so stop rather than spin on a dead fd.
                if errno == EINTR { continue }
                break
            }
            // A stalled client must not wedge this single-threaded loop the way an unbounded
            // `recv` in `_readRequest` would (BajutsuAndroidUIAutomatorServer's resident server
            // sets the equivalent `soTimeout` for the same reason): loopback is not isolated
            // between apps, so a connection here is not necessarily bajutsu's own driver.
            var timeout = timeval(tv_sec: 2, tv_usec: 0)
            setsockopt(clientFD, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
            // Without this, writing a reply to a peer that has already hung up raises SIGPIPE,
            // whose default action terminates the whole app under test — not this responder —
            // over a diagnostic write. A client racing this responder's own 2s main-thread
            // deadline (`_handleZOrder`) with its own timeout is the realistic way that happens.
            var noSigPipe: Int32 = 1
            setsockopt(clientFD, SOL_SOCKET, SO_NOSIGPIPE, &noSigPipe, socklen_t(MemoryLayout<Int32>.size))
            _handleConnection(clientFD)
            close(clientFD)
        }
    }

    private func _handleConnection(_ fd: Int32) {
        guard let request = _readRequest(fd) else {
            _writeJSON(fd, status: 400, object: ["status": "error", "message": "bad request"])
            return
        }
        guard request.token == token else {
            _writeJSON(fd, status: 401, object: ["status": "error", "message": "unauthorized"])
            return
        }
        guard request.method == "GET", request.path == "/zorder" else {
            _writeJSON(fd, status: 404, object: ["status": "error", "message": "not found"])
            return
        }
        _handleZOrder(fd)
    }

    private func _handleZOrder(_ fd: Int32) {
        #if canImport(UIKit)
        let semaphore = DispatchSemaphore(value: 0)
        var elements: [[String: Any]] = []
        DispatchQueue.main.async {
            elements = MainActor.assumeIsolated { BajutsuZOrder.positions() }
            semaphore.signal()
        }
        // A wedged main thread must not wedge the responder: the driver is waiting on this reply
        // inline with its own element query, and an absent position is a supported answer.
        guard semaphore.wait(timeout: .now() + 2) == .success else {
            _writeJSON(fd, status: 503, object: ["status": "error", "message": "main thread busy"])
            return
        }
        _writeJSON(fd, status: 200, object: ["status": "ok", "elements": elements])
        #else
        _writeJSON(fd, status: 200, object: ["status": "ok", "elements": []])
        #endif
    }

    // MARK: - HTTP helpers

    private func _readRequest(_ fd: Int32) -> (method: String, path: String, token: String)? {
        var buf = Data()
        var byte = [UInt8](repeating: 0, count: 1)
        while buf.count < 8192 {
            let n = recv(fd, &byte, 1, 0)
            if n <= 0 { return nil }
            buf.append(byte[0])
            if buf.count >= 4, buf.suffix(4) == Data([0x0D, 0x0A, 0x0D, 0x0A]) { break }
        }
        guard let header = String(data: buf, encoding: .utf8) else { return nil }
        let lines = header.components(separatedBy: "\r\n")
        guard let first = lines.first else { return nil }
        let parts = first.split(separator: " ", maxSplits: 2)
        guard parts.count >= 2 else { return nil }

        var bearer = ""
        for line in lines.dropFirst() where line.lowercased().hasPrefix("authorization:") {
            let value = line.dropFirst("authorization:".count).trimmingCharacters(in: .whitespaces)
            guard value.lowercased().hasPrefix("bearer ") else { continue }
            bearer = String(value.dropFirst("bearer ".count))
        }
        return (String(parts[0]), String(parts[1]), bearer)
    }

    private func _writeJSON(_ fd: Int32, status: Int, object: [String: Any]) {
        let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data()
        let statusText: String
        switch status {
        case 200: statusText = "OK"
        case 400: statusText = "Bad Request"
        case 401: statusText = "Unauthorized"
        case 404: statusText = "Not Found"
        case 503: statusText = "Service Unavailable"
        default: statusText = "Error"
        }
        var header = "HTTP/1.1 \(status) \(statusText)\r\n"
        header += "Content-Type: application/json\r\n"
        header += "Content-Length: \(data.count)\r\n"
        header += "Connection: close\r\n\r\n"
        _sendAll(fd, Data(header.utf8))
        _sendAll(fd, data)
    }

    private func _sendAll(_ fd: Int32, _ data: Data) {
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

    enum _Error: Error {
        case socketFailed
        case bindFailed
        case listenFailed
    }
}
