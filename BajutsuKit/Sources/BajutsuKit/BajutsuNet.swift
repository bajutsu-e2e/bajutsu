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

    /// Restore the `//` that `xcodebuild` strips out of a forwarded collector URL.
    ///
    /// Measured on the Simulator: Python injects `http://127.0.0.1:<port>` (22 characters) and the
    /// XCTest runner's own environment already holds `http:/127.0.0.1:<port>` (21). `xcodebuild`
    /// path-normalizes `.xctestrun` `TestingEnvironmentVariables` — the same machinery that expands
    /// `__TESTROOT__` — and collapses the empty authority on the way through. `URL(string:)` then
    /// parses the result with a **nil host**, which is not a cosmetic loss: it silently disarmed
    /// `BajutsuURLProtocol.canInit`'s loopback guard, so every report POST was itself intercepted and
    /// re-reported, ~1,200 times a second, each carrying the last payload. Repaired here, at the one
    /// place the value is read, rather than by loosening the guard alone — a collector that cannot be
    /// addressed collects nothing either way.
    static func repairedURL(_ raw: String) -> String {
        for scheme in ["http", "https"] where raw.hasPrefix("\(scheme):/") && !raw.hasPrefix("\(scheme)://") {
            return "\(scheme)://" + raw.dropFirst(scheme.count + 2)
        }
        return raw
    }

    /// Cap on the bytes of each body a report carries.
    ///
    /// A report is evidence a human reads, not a copy of the payload, and stringifying a body
    /// allocates a second copy of whatever it is handed. Unbounded, that is not a rounding error:
    /// measured on a Simulator, an app under automation grew ~1.6 GB/s here and reached 18.6 GB
    /// inside one 11-second scenario — on a 7 GiB CI host it exhausts the machine, and the Simulator's
    /// render service starts failing long before any test does. Bounding the *report* rather than the
    /// capture is deliberate: `capturedRequestBody` is also what mock rules match on and what the
    /// forwarded request carries, so truncating at capture would change what the app under test sees.
    static let maximumReportedBodyBytes = 64 * 1024

    /// The reportable text of a body, plus its full byte count when it did not fit.
    ///
    /// Returns nil for an absent, empty, or non-text body — the same "omit the key" outcome the
    /// unbounded conversion produced, so a binary payload still contributes nothing to the report.
    static func reportableBody(_ data: Data?) -> (text: String, fullBytes: Int)? {
        guard let data, !data.isEmpty else { return nil }
        if data.count <= maximumReportedBodyBytes {
            guard let text = String(data: data, encoding: .utf8), !text.isEmpty else { return nil }
            return (text, 0)
        }
        // The cut can land inside a multi-byte UTF-8 sequence, which no encoding can decode; back off
        // up to three bytes to reach a boundary rather than discarding a body that is good text right
        // up to that point. Failing all four, the body is not UTF-8 at all and is omitted as before.
        for drop in 0...3 {
            let head = data.prefix(maximumReportedBodyBytes - drop)
            if let text = String(data: head, encoding: .utf8), !text.isEmpty {
                return (text, data.count)
            }
        }
        return nil
    }

    /// Activate capture if `BAJUTSU_COLLECTOR` is set. Call once, early (e.g. in the
    /// app's `init` / `application(_:didFinishLaunchingWithOptions:)`).
    public static func startIfEnabled(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        BajutsuMocks.shared.load(environment)
        // Ahead of the guard below on purpose: touch visualization needs neither a collector nor a
        // mock rule, and a plain recorded run with no network features at all is the case it is for.
        BajutsuTouch.startIfEnabled(environment: environment)
        if let raw = environment["BAJUTSU_COLLECTOR"], let url = URL(string: repairedURL(raw)) {
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
        // Truncation is reported beside the body, never applied silently: a reader has to be able to
        // tell a short body from a long one that was cut. The collector ignores keys it does not know
        // (`NetworkExchange` is `extra="ignore"`), so an older bajutsu reads these reports unchanged.
        if let (text, fullBytes) = reportableBody(requestBody) {
            payload["requestBody"] = text
            if fullBytes > 0 { payload["requestBodyBytes"] = fullBytes }
        }
        if let (text, fullBytes) = reportableBody(body) {
            payload["responseBody"] = text
            if fullBytes > 0 { payload["responseBodyBytes"] = fullBytes }
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
