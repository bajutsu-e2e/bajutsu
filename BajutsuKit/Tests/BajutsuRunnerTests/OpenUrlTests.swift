import XCTest
@testable import BajutsuRunner

/// `/app/openUrl` (BE-0487) exercised against a fake `ElementProviding` conformer: each provider
/// outcome becomes its own reply status, and a `not-opened` carries the provider's reason.
final class OpenUrlTests: XCTestCase {
    private enum UnexpectedShape: Error { case output }

    private func reply(_ output: Operations.openUrl.Output) throws -> [String: Any] {
        guard case .ok(let ok) = output, case .json(let payload) = ok.body else {
            XCTFail("unexpected openUrl output: \(output)")
            throw UnexpectedShape.output
        }
        return try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(payload)) as? [String: Any]
        )
    }

    private func open(_ provider: FakeElementProvider, _ url: String) async throws -> [String: Any] {
        try reply(await APIHandler(provider: provider).openUrl(.init(body: .json(.init(url: url)))))
    }

    func testOpenUrlHandsTheUrlToTheProviderAndReportsOk() async throws {
        let provider = FakeElementProvider()
        let body = try await open(provider, "myapp://orders/42?a=1&b=2")
        XCTAssertEqual(body["status"] as? String, "ok")
        XCTAssertNil(body["reason"])
        XCTAssertEqual(provider.openUrlCalls, ["myapp://orders/42?a=1&b=2"])
    }

    func testNotOpenedCarriesTheReason() async throws {
        let provider = FakeElementProvider()
        provider.openUrlResult = .notOpened("Failed to open myapp://x")
        let body = try await open(provider, "myapp://x")
        XCTAssertEqual(body["status"] as? String, "not-opened")
        XCTAssertEqual(body["reason"] as? String, "Failed to open myapp://x")
    }

    func testNotForegroundAndUnsupportedKeepTheirOwnStatuses() async throws {
        let provider = FakeElementProvider()
        provider.openUrlResult = .notForeground
        let notForeground = try await open(provider, "myapp://x")
        XCTAssertEqual(notForeground["status"] as? String, "not-foreground")
        provider.openUrlResult = .unsupported
        let unsupported = try await open(provider, "myapp://x")
        XCTAssertEqual(unsupported["status"] as? String, "unsupported")
    }
}
