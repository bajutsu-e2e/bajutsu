import Foundation

/// In-app network observation for bajutsu.
///
/// A Simulator app shares the Mac's loopback, so when bajutsu runs a scenario it
/// starts a collector on `127.0.0.1:<port>` and injects its URL into the app via the
/// `BAJUTSU_COLLECTOR` launch env. A real device shares no loopback with the Mac, so there
/// the value lists one URL per host address the device might route to, and the app keeps
/// the first that answers (`reachableCollector`). `BajutsuNet.startIfEnabled()` activates a
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

    /// Activate capture if `BAJUTSU_COLLECTOR` is set. Call once, early (e.g. in the
    /// app's `init` / `application(_:didFinishLaunchingWithOptions:)`).
    public static func startIfEnabled(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        BajutsuMocks.shared.load(environment)
        // Ahead of the guard below on purpose: touch visualization needs neither a collector nor a
        // mock rule, and a plain recorded run with no network features at all is the case it is for.
        BajutsuTouch.startIfEnabled(environment: environment)
        // Ahead of the guard for the same reason: the driver asks for a stacking order on any run,
        // and the responder gates itself on the port and token the host injected (BE-0355).
        BajutsuZOrder.startIfEnabled(environment: environment)
        if let raw = environment["BAJUTSU_COLLECTOR"] {
            collectorToken = environment["BAJUTSU_COLLECTOR_TOKEN"]
            collectorURL = reachableCollector(candidateURLs(raw), token: collectorToken)
        }
        #if BAJUTSU_ENABLE_CONTROL_CHANNEL
        // The one inbound direction (BE-0365), and the only feature here that a compilation
        // condition has to select before its launch-env key can reach it. It polls the collector
        // resolved just above, so it is started after that and is inert without it.
        BajutsuControlChannel.startIfEnabled(
            environment: environment, collector: collectorURL, token: collectorToken
        )
        #endif
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
        // Reported whole, deliberately. A cap here looks like cheap insurance and is not: every
        // consumer of these bodies parses them — `responseSchema` and the `request.body` matcher both
        // run `json.loads`, so a body cut mid-object stops being JSON and the assertion fails with
        // "response body is not JSON" about a payload that was valid. That trades a memory bug for a
        // false verdict, which is the worse of the two. The memory this once cost came from the
        // report loop above, not from body size, and `repairedURL` closes that at the source.
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

    /// The candidate collector URLs in `BAJUTSU_COLLECTOR`, in the host's preference order.
    ///
    /// One URL on the Simulator; one per host address on a real device. Each is repaired on its own,
    /// since `xcodebuild` collapses the `//` of every URL in the value, not only the first.
    static func candidateURLs(_ raw: String) -> [URL] {
        raw.split(separator: ",").compactMap { piece in
            URL(string: repairedURL(piece.trimmingCharacters(in: .whitespaces)))
        }
    }

    /// Asks one candidate whether it is this run's collector: `completion(true)` on a 204.
    typealias CollectorProbe = (_ url: URL, _ token: String?, _ completion: @escaping (Bool) -> Void) -> Void

    /// The first candidate, in the host's order, that answers an authenticated `GET /ping`.
    ///
    /// A single candidate is taken as is, with no probe, so the Simulator's launch is unchanged. With
    /// several, every candidate is probed at once, and the launch waits only until the choice is
    /// settled: a candidate has answered and every one ahead of it has failed. An unreachable address
    /// later in the list therefore costs nothing; one ahead of the answer costs up to `timeout`.
    /// Reports start only once the collector is known, so none made early in the launch is lost.
    /// No answer leaves the app reporting nothing, as an app launched without a collector does.
    static func reachableCollector(
        _ candidates: [URL], token: String?, timeout: TimeInterval = 2,
        probe: CollectorProbe = pingCollector
    ) -> URL? {
        guard candidates.count > 1 else { return candidates.first }
        let answers = ProbeAnswers(count: candidates.count)
        for (index, url) in candidates.enumerated() {
            probe(url, token) { ok in answers.record(index, ok) }
        }
        answers.waitUntilSettled(timeout: timeout)
        return answers.firstAnswered().map { candidates[$0] }
    }

    /// The production probe: an authenticated `GET <url>/ping` on a session nothing intercepts.
    static func pingCollector(_ url: URL, _ token: String?, _ completion: @escaping (Bool) -> Void) {
        var req = URLRequest(url: url.appendingPathComponent("ping"), timeoutInterval: 2)
        if let token {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        reportSession.dataTask(with: req) { _, response, _ in
            completion((response as? HTTPURLResponse)?.statusCode == 204)
        }.resume()
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

/// Each candidate's probe result (nil until it returns), written from the probes' own queues.
private final class ProbeAnswers: @unchecked Sendable {
    private let lock = NSLock()
    private let settled = DispatchSemaphore(value: 0)
    private var results: [Bool?]
    private var signalled = false

    init(count: Int) { results = Array(repeating: nil, count: count) }

    func record(_ index: Int, _ ok: Bool) {
        lock.lock()
        defer { lock.unlock() }
        guard results[index] == nil else { return }
        results[index] = ok
        if !signalled, isSettled() {
            signalled = true
            settled.signal()
        }
    }

    func waitUntilSettled(timeout: TimeInterval) {
        _ = settled.wait(timeout: .now() + timeout)
    }

    /// The first candidate known to have answered, in the host's order.
    func firstAnswered() -> Int? {
        lock.lock()
        defer { lock.unlock() }
        return results.firstIndex(of: true)
    }

    /// Settled once the first unresolved-or-answered slot is an answer, or every probe has failed.
    private func isSettled() -> Bool {
        for result in results {
            switch result {
            case .some(true): return true
            case .some(false): continue
            case .none: return false
            }
        }
        return true
    }
}
