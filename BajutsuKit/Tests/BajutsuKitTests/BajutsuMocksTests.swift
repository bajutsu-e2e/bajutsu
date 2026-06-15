import Foundation
import XCTest

@testable import BajutsuKit

/// Unit tests for the deterministic request-matching / mock-parsing logic — the part of
/// BajutsuKit that is pure Foundation and needs no Simulator (so it runs in `swift test`
/// on a plain macOS runner, mirroring how the Python core's gate needs no device).
final class BajutsuMocksTests: XCTestCase {
    private func request(
        _ method: String, _ urlString: String
    ) -> URLRequest {
        var req = URLRequest(url: URL(string: urlString)!)
        req.httpMethod = method
        return req
    }

    private func rule(
        method: String? = nil,
        url: String? = nil,
        urlMatches: String? = nil,
        path: String? = nil,
        pathMatches: String? = nil,
        bodyMatches: String? = nil
    ) -> BajutsuMockRule {
        BajutsuMockRule(
            method: method,
            url: url,
            urlMatches: urlMatches,
            path: path,
            pathMatches: pathMatches,
            bodyMatches: bodyMatches,
            status: 200,
            headers: [:],
            body: Data(),
            delaySeconds: 0
        )
    }

    func testMethodAndExactPathMatch() {
        let r = rule(method: "get", path: "/api/users")
        XCTAssertTrue(r.matches(request("GET", "https://example.com/api/users"), body: nil))
        // Method is compared case-insensitively, path exactly.
        XCTAssertFalse(r.matches(request("POST", "https://example.com/api/users"), body: nil))
        XCTAssertFalse(r.matches(request("GET", "https://example.com/api/users/1"), body: nil))
    }

    func testUrlAndPathRegexMatch() {
        let r = rule(urlMatches: "users/[0-9]+", pathMatches: "^/api/")
        XCTAssertTrue(r.matches(request("GET", "https://example.com/api/users/42"), body: nil))
        XCTAssertFalse(r.matches(request("GET", "https://example.com/api/posts/42"), body: nil))
    }

    func testBodyMatchAgainstRequestBody() {
        let r = rule(bodyMatches: "\"token\"")
        let yes = Data("{\"token\":\"abc\"}".utf8)
        let no = Data("{\"other\":1}".utf8)
        XCTAssertTrue(r.matches(request("POST", "https://example.com/login"), body: yes))
        XCTAssertFalse(r.matches(request("POST", "https://example.com/login"), body: no))
    }

    func testLoadParsesEnvAndFirstMatchWins() {
        let json = """
        [
          {"match": {"path": "/a"}, "respond": {"status": 201, "body": "first"}},
          {"match": {"path": "/a"}, "respond": {"status": 202, "body": "second"}}
        ]
        """
        let mocks = BajutsuMocks()
        mocks.load(["BAJUTSU_MOCKS": json])
        XCTAssertEqual(mocks.rules.count, 2)

        let stub = mocks.stub(for: request("GET", "https://example.com/a"), body: nil)
        XCTAssertEqual(stub?.status, 201)
        XCTAssertEqual(stub.map { String(decoding: $0.body, as: UTF8.self) }, "first")

        XCTAssertNil(mocks.stub(for: request("GET", "https://example.com/b"), body: nil))
    }

    func testLoadIgnoresMissingOrMalformedEnv() {
        let mocks = BajutsuMocks()
        mocks.load([:])
        XCTAssertTrue(mocks.rules.isEmpty)
        mocks.load(["BAJUTSU_MOCKS": "not json"])
        XCTAssertTrue(mocks.rules.isEmpty)
    }
}
