import Darwin
import Foundation
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif
#if canImport(WebKit)
import WebKit
#endif

/// In-app WebView bridge for bajutsu (BE-0037).
///
/// When `BAJUTSU_WEBVIEW_PORT` is set in the launch environment, this starts a
/// minimal HTTP server inside the app. The Python driver sends requests to query
/// the DOM of an embedded `WKWebView` and dispatch taps.
///
/// Endpoints:
/// - `GET /webview/dom?id=<accessibilityIdentifier>` — walk the DOM of the
///   identified WKWebView and return normalized elements as JSON.
/// - `POST /webview/tap` — tap a point inside a WKWebView (body: `{"id": ..., "point": [x, y]}`).
public enum BajutsuWebView {
    private static var server: _BridgeServer?

    /// Start the bridge if `BAJUTSU_WEBVIEW_PORT` is present. Call from
    /// `BajutsuNet.startIfEnabled()`.
    static func startIfEnabled(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        guard let raw = environment["BAJUTSU_WEBVIEW_PORT"],
              let port = UInt16(raw) else { return }
        let srv = _BridgeServer()
        do {
            try srv.start(port: port)
            server = srv
        } catch {
            // Non-fatal: the bridge is test infrastructure, not app functionality.
        }
    }

    static func stop() {
        server?.stop()
        server = nil
    }

    // MARK: - JS

    /// The same page-walk JavaScript the Playwright backend uses (bajutsu/dom.py QUERY_JS).
    static let queryJS = """
    (() => {
      const out = [];
      const sel = '[data-testid], button, a, input, select, textarea, [role]';
      for (const el of document.querySelectorAll(sel)) {
        const r = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        if (r.width === 0 && r.height === 0) continue;
        const text = (el.innerText || el.textContent || '').trim();
        out.push({
          identifier: el.getAttribute('data-testid'),
          role: el.getAttribute('role') || el.tagName.toLowerCase(),
          label: el.getAttribute('aria-label') || (text ? text.slice(0, 200) : null),
          value: ('value' in el) ? el.value : null,
          disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
          selected: el.getAttribute('aria-selected') === 'true'
                    || el.getAttribute('aria-checked') === 'true',
          frame: [r.x, r.y, r.width, r.height],
        });
      }
      return out;
    })()
    """

    /// JavaScript template to click a point inside the WebView.
    static func tapJS(x: Double, y: Double) -> String {
        """
        (() => {
          const el = document.elementFromPoint(\(x), \(y));
          if (el) { el.click(); return 'ok'; }
          return 'not-found';
        })()
        """
    }

    /// JavaScript template to type text into the currently focused element.
    static func typeJS(text: String) -> String {
        let json = _jsonEncode(text)
        return """
        (() => {
          const el = document.activeElement;
          if (!el || !('value' in el)) return 'no-focus';
          el.value += \(json);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return 'ok';
        })()
        """
    }

    /// JavaScript template to scroll an element with the given data-testid into view.
    static func scrollJS(elementId: String) -> String {
        let json = _jsonEncode(elementId)
        return """
        (() => {
          const el = document.querySelector('[data-testid=' + \(json) + ']');
          if (!el) return 'not-found';
          el.scrollIntoView({ behavior: 'instant', block: 'center' });
          return 'ok';
        })()
        """
    }

    private static func _jsonEncode(_ s: String) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: s),
              let json = String(data: data, encoding: .utf8) else { return "\"\"" }
        return json
    }

    // MARK: - View hierarchy

    #if canImport(UIKit) && canImport(WebKit)
    /// Find a WKWebView in the view hierarchy matching the given accessibility identifier.
    @MainActor
    static func findWebView(id: String) -> WKWebView? {
        guard let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
              let window = scene.windows.first(where: { $0.isKeyWindow }) else { return nil }
        return _findWebView(in: window, id: id)
    }

    @MainActor
    private static func _findWebView(in view: UIView, id: String) -> WKWebView? {
        if let wv = view as? WKWebView, wv.accessibilityIdentifier == id {
            return wv
        }
        for child in view.subviews {
            if let found = _findWebView(in: child, id: id) {
                return found
            }
        }
        return nil
    }
    #elseif canImport(AppKit) && canImport(WebKit)
    @MainActor
    static func findWebView(id: String) -> WKWebView? {
        guard let window = NSApplication.shared.keyWindow else { return nil }
        return _findWebView(in: window.contentView, id: id)
    }

    @MainActor
    private static func _findWebView(in view: NSView?, id: String) -> WKWebView? {
        guard let view else { return nil }
        if let wv = view as? WKWebView, wv.accessibilityIdentifier() == id {
            return wv
        }
        for child in view.subviews {
            if let found = _findWebView(in: child, id: id) {
                return found
            }
        }
        return nil
    }
    #endif
}

// MARK: - Minimal HTTP server (self-contained, no dependency on BajutsuRunner)

private final class _BridgeServer {
    private var listenFD: Int32 = -1
    private let lock = NSLock()
    private let queue = DispatchQueue(label: "bajutsu.webview.bridge")

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
            guard clientFD >= 0 else { break }
            _handleConnection(clientFD)
            close(clientFD)
        }
    }

    private func _handleConnection(_ fd: Int32) {
        guard let (method, path, body) = _readRequest(fd) else {
            _writeJSON(fd, status: 400, object: ["status": "error", "message": "bad request"])
            return
        }
        _route(fd, method: method, path: path, body: body)
    }

    // MARK: - Routing

    private func _route(_ fd: Int32, method: String, path: String, body: Data?) {
        if method == "GET", path.hasPrefix("/webview/dom") {
            _handleDOM(fd, path: path)
        } else if method == "POST", path == "/webview/tap" {
            _handleTap(fd, body: body)
        } else if method == "POST", path == "/webview/type" {
            _handleType(fd, body: body)
        } else if method == "POST", path == "/webview/scroll" {
            _handleScroll(fd, body: body)
        } else if method == "GET", path == "/health" {
            _writeJSON(fd, status: 200, object: ["status": "ready"])
        } else {
            _writeJSON(fd, status: 404, object: ["status": "error", "message": "not found"])
        }
    }

    private func _handleDOM(_ fd: Int32, path: String) {
        guard let id = _queryParam(path, key: "id") else {
            _writeJSON(fd, status: 400, object: ["status": "error", "message": "missing id param"])
            return
        }

        #if canImport(WebKit)
        let semaphore = DispatchSemaphore(value: 0)
        var elements: [[String: Any]] = []

        DispatchQueue.main.async {
            guard let wv = BajutsuWebView.findWebView(id: id) else {
                semaphore.signal()
                return
            }
            wv.evaluateJavaScript(BajutsuWebView.queryJS) { result, _ in
                if let records = result as? [[String: Any]] {
                    elements = records
                }
                semaphore.signal()
            }
        }
        semaphore.wait()
        _writeJSON(fd, status: 200, object: ["elements": elements])
        #else
        _writeJSON(fd, status: 200, object: ["elements": []])
        #endif
    }

    private func _handleTap(_ fd: Int32, body: Data?) {
        guard let body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              let id = json["id"] as? String,
              let rawPoint = json["point"] as? [Any],
              rawPoint.count == 2,
              let x = (rawPoint[0] as? NSNumber)?.doubleValue,
              let y = (rawPoint[1] as? NSNumber)?.doubleValue else {
            _writeJSON(fd, status: 400, object: ["status": "error", "message": "invalid body"])
            return
        }

        #if canImport(WebKit)
        let semaphore = DispatchSemaphore(value: 0)
        var tapResult = "error"

        DispatchQueue.main.async {
            guard let wv = BajutsuWebView.findWebView(id: id) else {
                semaphore.signal()
                return
            }
            wv.evaluateJavaScript(BajutsuWebView.tapJS(x: x, y: y)) { result, _ in
                if let r = result as? String { tapResult = r }
                semaphore.signal()
            }
        }
        semaphore.wait()
        _writeJSON(fd, status: 200, object: ["status": tapResult == "ok" ? "ok" : "not-found"])
        #else
        _writeJSON(fd, status: 200, object: ["status": "error", "message": "WebKit not available"])
        #endif
    }

    private func _handleType(_ fd: Int32, body: Data?) {
        guard let body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              let id = json["id"] as? String,
              let text = json["text"] as? String else {
            _writeJSON(fd, status: 400, object: ["status": "error", "message": "invalid body"])
            return
        }

        #if canImport(WebKit)
        let semaphore = DispatchSemaphore(value: 0)
        var typeResult = "error"

        DispatchQueue.main.async {
            guard let wv = BajutsuWebView.findWebView(id: id) else {
                semaphore.signal()
                return
            }
            wv.evaluateJavaScript(BajutsuWebView.typeJS(text: text)) { result, _ in
                if let r = result as? String { typeResult = r }
                semaphore.signal()
            }
        }
        semaphore.wait()
        _writeJSON(fd, status: 200, object: ["status": typeResult == "ok" ? "ok" : typeResult])
        #else
        _writeJSON(fd, status: 200, object: ["status": "error", "message": "WebKit not available"])
        #endif
    }

    private func _handleScroll(_ fd: Int32, body: Data?) {
        guard let body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              let id = json["id"] as? String,
              let elementId = json["elementId"] as? String else {
            _writeJSON(fd, status: 400, object: ["status": "error", "message": "invalid body"])
            return
        }

        #if canImport(WebKit)
        let semaphore = DispatchSemaphore(value: 0)
        var scrollResult = "error"

        DispatchQueue.main.async {
            guard let wv = BajutsuWebView.findWebView(id: id) else {
                semaphore.signal()
                return
            }
            wv.evaluateJavaScript(BajutsuWebView.scrollJS(elementId: elementId)) { result, _ in
                if let r = result as? String { scrollResult = r }
                semaphore.signal()
            }
        }
        semaphore.wait()
        _writeJSON(fd, status: 200, object: ["status": scrollResult == "ok" ? "ok" : scrollResult])
        #else
        _writeJSON(fd, status: 200, object: ["status": "error", "message": "WebKit not available"])
        #endif
    }

    // MARK: - HTTP helpers

    private func _queryParam(_ path: String, key: String) -> String? {
        guard let qIndex = path.firstIndex(of: "?") else { return nil }
        let query = path[path.index(after: qIndex)...]
        for pair in query.split(separator: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1)
            guard kv.count == 2, kv[0] == key else { continue }
            return String(kv[1]).removingPercentEncoding
        }
        return nil
    }

    private func _readRequest(_ fd: Int32) -> (method: String, path: String, body: Data?)? {
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

        var contentLength = 0
        for line in lines.dropFirst() {
            if line.lowercased().hasPrefix("content-length:") {
                let val = line.dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces)
                contentLength = min(Int(val) ?? 0, 65536)
            }
        }

        var body: Data?
        if contentLength > 0 {
            var bodyBuf = Data(count: contentLength)
            var total = 0
            bodyBuf.withUnsafeMutableBytes { ptr in
                guard let base = ptr.baseAddress else { return }
                while total < contentLength {
                    let n = recv(fd, base + total, contentLength - total, 0)
                    if n <= 0 { break }
                    total += n
                }
            }
            if total == contentLength { body = bodyBuf }
        }

        return (String(parts[0]), String(parts[1]), body)
    }

    private func _writeJSON(_ fd: Int32, status: Int, object: [String: Any]) {
        let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data()
        let statusText: String
        switch status {
        case 200: statusText = "OK"
        case 400: statusText = "Bad Request"
        case 404: statusText = "Not Found"
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
