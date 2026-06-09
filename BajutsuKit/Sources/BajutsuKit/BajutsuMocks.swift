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

/// Holds the stub rules for the process. Loaded once from the launch env; the first
/// matching rule wins (declaration order).
final class BajutsuMocks {
    static let shared = BajutsuMocks()
    private(set) var rules: [BajutsuMockRule] = []

    func load(_ environment: [String: String] = ProcessInfo.processInfo.environment) {
        guard let raw = environment["BAJUTSU_MOCKS"],
              let data = raw.data(using: .utf8),
              let array = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
        else { return }
        rules = array.compactMap(Self.parse)
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
