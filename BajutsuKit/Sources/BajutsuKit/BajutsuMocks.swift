import Foundation

/// One deterministic stub parsed from the BAJUTSU_MOCKS launch env. When an outgoing
/// request matches, BajutsuURLProtocol returns the canned response instead of hitting the
/// network (so a test does not depend on a live server). The match fields mirror bajutsu's
/// request matcher (method / url / urlMatches / path / pathMatches / bodyMatches).
struct BajutsuMockRule {
    let method: String?
    let url: String?
    let urlMatches: String?
    let path: String?
    let pathMatches: String?
    let bodyMatches: String?
    let status: Int
    let headers: [String: String]
    let body: Data
    let delaySeconds: TimeInterval

    func matches(_ request: URLRequest, body: Data?) -> Bool {
        if let method, method.uppercased() != (request.httpMethod ?? "GET").uppercased() { return false }
        let urlString = request.url?.absoluteString ?? ""
        if let url, url != urlString { return false }
        if let urlMatches, !Self.hit(urlMatches, urlString) { return false }
        let requestPath = request.url?.path ?? ""
        if let path, path != requestPath { return false }
        if let pathMatches, !Self.hit(pathMatches, requestPath) { return false }
        if let bodyMatches {
            let text = body.flatMap { String(data: $0, encoding: .utf8) } ?? ""
            if !Self.hit(bodyMatches, text) { return false }
        }
        return true
    }

    private static func hit(_ pattern: String, _ text: String) -> Bool {
        text.range(of: pattern, options: .regularExpression) != nil
    }
}

/// Holds the stub rules for the process. Loaded from the launch env, and replaced whole when the
/// control channel delivers a mid-scenario stub table (BE-0365 unit 4); the first matching rule
/// wins (declaration order).
///
/// Locked because the two sides run on different threads: `stub(for:body:)` is called from
/// URLProtocol's loading threads while a replacement lands on the main thread, and a request must
/// see either the old table or the new one, never a torn read.
final class BajutsuMocks {
    static let shared = BajutsuMocks()
    private let lock = NSLock()
    private var storage: [BajutsuMockRule] = []

    var rules: [BajutsuMockRule] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    func load(_ environment: [String: String] = ProcessInfo.processInfo.environment) {
        guard let raw = environment["BAJUTSU_MOCKS"],
              let data = raw.data(using: .utf8),
              let array = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
        else { return }
        replace(with: array)
    }

    /// Replace the whole table — never merge — so the rules after the call are exactly `objects`,
    /// in the same wire shape `BAJUTSU_MOCKS` carries; an empty array removes every stub.
    func replace(with objects: [[String: Any]]) {
        let parsed = objects.compactMap(Self.parse)
        lock.lock()
        defer { lock.unlock() }
        storage = parsed
    }

    /// Why `objects` is not a table the app can install as written, or nil when it is.
    ///
    /// `parse` is lenient on purpose for the launch env, defaulting whatever it cannot read. A table
    /// arriving on the control channel is acknowledged back to bajutsu, though, and "applied" there
    /// has to mean the rules the scenario wrote: a missing `match` would otherwise install a stub
    /// answering every request, and a pattern ICU cannot compile would never match, both reported
    /// as success (BE-0365 unit 4).
    static func problem(in objects: [[String: Any]]) -> String? {
        for (index, object) in objects.enumerated() {
            if let reason = problem(inRule: object) { return "mocks[\(index)]: \(reason)" }
        }
        return nil
    }

    private static func problem(inRule object: [String: Any]) -> String? {
        guard let match = object["match"] as? [String: Any] else { return "no 'match' object" }
        for key in ["method", "url", "urlMatches", "path", "pathMatches", "bodyMatches"] {
            if let value = match[key], !(value is String) { return "match.\(key) is not a string" }
        }
        for key in ["urlMatches", "pathMatches", "bodyMatches"] {
            guard let pattern = match[key] as? String else { continue }
            do {
                _ = try NSRegularExpression(pattern: pattern)
            } catch {
                return "match.\(key) is not a valid pattern: \(pattern)"
            }
        }
        guard let respondValue = object["respond"] else { return nil }
        guard let respond = respondValue as? [String: Any] else { return "'respond' is not an object" }
        if let status = respond["status"], !(status is NSNumber) { return "respond.status is not a number" }
        if let headers = respond["headers"], !(headers is [String: String]) {
            return "respond.headers is not a map of strings"
        }
        if let body = respond["body"], !(body is String) { return "respond.body is not a string" }
        if let delay = respond["delayMs"], !(delay is NSNumber) { return "respond.delayMs is not a number" }
        return nil
    }

    func stub(for request: URLRequest, body: Data?) -> BajutsuMockRule? {
        rules.first { $0.matches(request, body: body) }
    }

    private static func parse(_ object: [String: Any]) -> BajutsuMockRule? {
        let match = object["match"] as? [String: Any] ?? [:]
        let respond = object["respond"] as? [String: Any] ?? [:]
        return BajutsuMockRule(
            method: match["method"] as? String,
            url: match["url"] as? String,
            urlMatches: match["urlMatches"] as? String,
            path: match["path"] as? String,
            pathMatches: match["pathMatches"] as? String,
            bodyMatches: match["bodyMatches"] as? String,
            status: (respond["status"] as? NSNumber)?.intValue ?? 200,
            headers: respond["headers"] as? [String: String] ?? [:],
            body: Data((respond["body"] as? String ?? "").utf8),
            delaySeconds: ((respond["delayMs"] as? NSNumber)?.doubleValue ?? 0) / 1000.0
        )
    }
}
