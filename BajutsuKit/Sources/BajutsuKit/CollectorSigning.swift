import CryptoKit
import Foundation

/// How the app proves this run's token to the collector, and checks that an answer came from it.
///
/// A real device reaches the collector over cleartext HTTP on a network the host does not control,
/// so a token sent as a bearer header would be readable by anyone on the route (BE-0459). When the
/// host announces the signed scheme (`BAJUTSU_COLLECTOR_AUTH=hmac`), each request instead carries an
/// HMAC-SHA256 of its method, path, a fresh nonce, and its body, keyed by the token; the collector
/// signs its answer against that nonce. Without the announcement — an older Bajutsu — the app sends
/// the bearer header it always did, so a newer kit keeps working against it.
struct CollectorCredential: Equatable {
    let token: String
    /// Sign each request rather than send the token, because the host announced the scheme.
    let signs: Bool

    /// The value of `BAJUTSU_COLLECTOR_AUTH` that turns signing on.
    static let signedScheme = "hmac"

    init(token: String, signs: Bool) {
        self.token = token
        self.signs = signs
    }

    /// The credential the launch environment carries, or nil when bajutsu injected no token.
    init?(token: String?, auth: String?) {
        guard let token, !token.isEmpty else { return nil }
        self.init(token: token, signs: auth == Self.signedScheme)
    }

    /// The credential in the app's launch environment, read from the keys the host injects.
    init?(environment: [String: String]) {
        self.init(
            token: environment["BAJUTSU_COLLECTOR_TOKEN"], auth: environment["BAJUTSU_COLLECTOR_AUTH"]
        )
    }

    /// Authorize `request` for the collector. Returns the nonce its answer is signed against, or
    /// nil for a bearer request, whose answer carries nothing to check.
    func authorize(_ request: inout URLRequest) -> String? {
        guard signs else {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            return nil
        }
        let nonce = CollectorSigning.newNonce()
        let path = request.url.map(CollectorSigning.canonicalPath) ?? "/"
        let header = CollectorSigning.authorization(
            token: token, method: request.httpMethod ?? "GET", path: path, nonce: nonce,
            body: request.httpBody ?? Data()
        )
        request.setValue(header, forHTTPHeaderField: "Authorization")
        return nonce
    }

    /// Whether an answer is one this run's collector wrote for the request that carried `nonce`.
    ///
    /// A bearer request (nil nonce) has nothing to verify, so its answer is taken as it always was.
    func accepts(_ response: HTTPURLResponse?, body: Data?, nonce: String?) -> Bool {
        guard let nonce else { return true }
        guard let response,
              let presented = response.value(forHTTPHeaderField: CollectorSigning.answerHeader)
        else { return false }
        let expected = CollectorSigning.answerSignature(
            token: token, nonce: nonce, status: response.statusCode, body: body ?? Data()
        )
        return CollectorSigning.matches(expected, presented)
    }
}

/// The scheme's two canonical forms, byte for byte the same as the collector's `_hmac_auth.py`; the
/// fixed vectors in `tests/fixtures/be0459/` pin the two sides together.
enum CollectorSigning {
    static let scheme = "Bajutsu-HMAC-SHA256"
    static let answerHeader = "X-Bajutsu-Signature"

    /// Sixteen bytes from the system's cryptographically secure generator, encoded for the header.
    static func newNonce() -> String {
        var generator = SystemRandomNumberGenerator()
        return base64url(Data((0..<16).map { _ in UInt8.random(in: .min ... .max, using: &generator) }))
    }

    /// The path a signature covers: still percent-encoded, as the request line carries it, without
    /// the query, and `/` for a URL with no path — the collector's bare report URL.
    static func canonicalPath(_ url: URL) -> String {
        let path = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedPath ?? ""
        return path.isEmpty ? "/" : path
    }

    static func requestSignature(
        token: String, method: String, path: String, nonce: String, body: Data
    ) -> String {
        mac(token, ["bajutsu-request-v1", method, path, nonce, hexDigest(body)])
    }

    static func answerSignature(token: String, nonce: String, status: Int, body: Data) -> String {
        mac(token, ["bajutsu-answer-v1", nonce, String(status), hexDigest(body)])
    }

    static func authorization(
        token: String, method: String, path: String, nonce: String, body: Data
    ) -> String {
        let signature = requestSignature(
            token: token, method: method, path: path, nonce: nonce, body: body
        )
        return "\(scheme) nonce=\(nonce), signature=\(signature)"
    }

    /// Unpadded base64url, the encoding of every nonce and signature in the scheme.
    static func base64url(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    /// Compare two encoded signatures without an early exit, so timing reveals nothing of either.
    static func matches(_ expected: String, _ presented: String) -> Bool {
        let a = Array(expected.utf8)
        let b = Array(presented.utf8)
        guard a.count == b.count else { return false }
        return zip(a, b).reduce(UInt8(0)) { $0 | ($1.0 ^ $1.1) } == 0
    }

    private static func mac(_ token: String, _ lines: [String]) -> String {
        let canonical = Data(lines.joined(separator: "\n").utf8)
        let code = HMAC<SHA256>.authenticationCode(
            for: canonical, using: SymmetricKey(data: Data(token.utf8))
        )
        return base64url(Data(code))
    }

    private static func hexDigest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
