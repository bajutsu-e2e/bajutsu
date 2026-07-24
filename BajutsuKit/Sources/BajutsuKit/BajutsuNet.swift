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
    /// Per-run shared token (`BAJUTSU_COLLECTOR_TOKEN`) attached to each report POST so the
    /// collector accepts only this run's app; nil unless bajutsu injected one.
    static private(set) var collectorToken: String?

    /// One JSON line per exchange is POSTed to the collector. The reporting session
    /// is kept separate so the report POST is never itself intercepted.
    static let reportSession = URLSession(configuration: .ephemeral)

    /// Activate capture if `BAJUTSU_COLLECTOR` is set. Call once, early (e.g. in the
    /// app's `init` / `application(_:didFinishLaunchingWithOptions:)`).
    public static func startIfEnabled(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        BajutsuMocks.shared.load(environment)
        if let raw = environment["BAJUTSU_COLLECTOR"], let url = URL(string: raw) {
            collectorURL = url
            collectorToken = environment["BAJUTSU_COLLECTOR_TOKEN"]
        }
        // Register the interceptor if there is anything to do: observe and/or stub.
        guard collectorURL != nil || !BajutsuMocks.shared.rules.isEmpty else { return }
        URLProtocol.registerClass(BajutsuURLProtocol.self)
        BajutsuURLProtocol.installIntoDefaultConfigurations()
        BajutsuWebView.startIfEnabled(environment: environment)
        BajutsuScreen.startIfEnabled()
    }

    static func report(
        request: URLRequest, requestBody: Data?, response: URLResponse?, body: Data,
        startedAt: Date, error: Error?, mocked: Bool = false
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
        if mocked { payload["mocked"] = true }
        if let error { payload["error"] = String(describing: error) }
        payload["requestHeaders"] = request.allHTTPHeaderFields ?? [:]
        if let http { payload["responseHeaders"] = stringHeaders(http.allHeaderFields) }
        if let reqBody = requestBody, let s = String(data: reqBody, encoding: .utf8), !s.isEmpty {
            payload["requestBody"] = s
        }
        if let s = String(data: body, encoding: .utf8), !s.isEmpty {
            payload["responseBody"] = s
        }
        postJSON(payload, to: collectorURL, token: collectorToken, session: reportSession)
    }

    private static func stringHeaders(_ headers: [AnyHashable: Any]) -> [String: String] {
        var out: [String: String] = [:]
        for (k, v) in headers { out[String(describing: k)] = String(describing: v) }
        return out
    }

    /// POST a JSON payload to the collector, fire-and-forget, bearer-authenticated with the
    /// per-run token. Shared by `report` above and `BajutsuScreen`'s transition report, so the
    /// request-construction boilerplate (headers, auth, serialization) is written once.
    ///
    /// Serialization and the `dataTask` handoff are dispatched off the caller's thread. `report`
    /// above already isn't guaranteed to run on the main thread, but `BajutsuScreen`'s caller,
    /// `viewDidAppear`, always is — and unlike an intercepted network exchange, an appearance
    /// report sits directly in a UIKit/SwiftUI lifecycle callback the accessibility bridge
    /// depends on to observe the UI settling. Keeping this off that thread avoids adding new
    /// main-thread work to a callback XCTest's automation session is already timing-sensitive
    /// around.
    static func postJSON(_ payload: [String: Any], to url: URL, token: String?, session: URLSession) {
        DispatchQueue.global(qos: .utility).async {
            guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
            var req = URLRequest(url: url)
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            if let token {
                req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            }
            req.httpBody = data
            session.dataTask(with: req).resume()  // fire-and-forget
        }
    }
}
