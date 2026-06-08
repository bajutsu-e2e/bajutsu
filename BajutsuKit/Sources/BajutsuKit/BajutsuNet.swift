import Foundation

/// In-app network observation for bajutsu.
///
/// A Simulator app shares the Mac's loopback, so when bajutsu runs a scenario it
/// starts a collector on `127.0.0.1:<port>` and injects its URL into the app via the
/// `BAJUTSU_COLLECTOR` launch env. `BajutsuNet.startIfEnabled()` activates a
/// `URLProtocol` that records each request/response the app makes and POSTs it to the
/// collector, where a step's `request` assertion can check it.
///
/// **Test/debug only.** It captures headers and bodies, so gate it on a launch env
/// that production never sets, and don't ship it in release builds. Activation is a
/// no-op unless `BAJUTSU_COLLECTOR` is present.
public enum BajutsuNet {
    static private(set) var collectorURL: URL?

    /// One JSON line per exchange is POSTed to the collector. The reporting session
    /// is kept separate so the report POST is never itself intercepted.
    static let reportSession = URLSession(configuration: .ephemeral)

    /// Activate capture if `BAJUTSU_COLLECTOR` is set. Call once, early (e.g. in the
    /// app's `init` / `application(_:didFinishLaunchingWithOptions:)`).
    public static func startIfEnabled(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        guard let raw = environment["BAJUTSU_COLLECTOR"], let url = URL(string: raw) else { return }
        collectorURL = url
        URLProtocol.registerClass(BajutsuURLProtocol.self)
        BajutsuURLProtocol.installIntoDefaultConfigurations()
    }

    static func report(
        request: URLRequest, response: URLResponse?, body: Data, startedAt: Date, error: Error?
    ) {
        guard let collectorURL else { return }
        let http = response as? HTTPURLResponse
        let durationMs = Date().timeIntervalSince(startedAt) * 1000
        // Surface the exchange to the host app's UI (same data POSTed below).
        BajutsuExchangeStore.shared.record(BajutsuExchange(
            method: request.httpMethod ?? "GET",
            url: request.url?.absoluteString ?? "",
            path: request.url?.path ?? "",
            status: http?.statusCode,
            durationMs: durationMs,
            error: error.map { String(describing: $0) }
        ))
        var payload: [String: Any] = [
            "method": request.httpMethod ?? "GET",
            "url": request.url?.absoluteString ?? "",
            "path": request.url?.path ?? "",
            "durationMs": durationMs,
        ]
        if let http { payload["status"] = http.statusCode }
        if let error { payload["error"] = String(describing: error) }
        payload["requestHeaders"] = request.allHTTPHeaderFields ?? [:]
        if let http { payload["responseHeaders"] = stringHeaders(http.allHeaderFields) }
        if let reqBody = request.httpBody, let s = String(data: reqBody, encoding: .utf8) {
            payload["requestBody"] = s
        }
        if let s = String(data: body, encoding: .utf8), !s.isEmpty {
            payload["responseBody"] = s
        }
        guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
        var req = URLRequest(url: collectorURL)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        reportSession.dataTask(with: req).resume()  // fire-and-forget
    }

    private static func stringHeaders(_ headers: [AnyHashable: Any]) -> [String: String] {
        var out: [String: String] = [:]
        for (k, v) in headers { out[String(describing: k)] = String(describing: v) }
        return out
    }
}
