import XCTest
@testable import BajutsuRunner

/// `/app/enter` and `/app/leave` are new — there is no legacy `Router` twin to keep in
/// parity, the way `TapDrainFoldTests` explains for `/tap`'s interruption-drain fold — so these
/// assertions exercise `APIHandler` directly against a fake `ElementProviding` conformer, no
/// XCUITest or Simulator involved.
final class AppActivationTests: XCTestCase {
    private enum UnexpectedShape: Error { case output }

    private func statusJSON(_ output: Operations.enterApp.Output) throws -> String {
        guard case .ok(let ok) = output, case .json(let payload) = ok.body else {
            XCTFail("unexpected enterApp output: \(output)")
            throw UnexpectedShape.output
        }
        return try XCTUnwrap(
            JSONSerialization.jsonObject(
                with: JSONEncoder().encode(payload)
            ) as? [String: Any]
        )["status"] as? String ?? "missing"
    }

    private func statusJSON(_ output: Operations.leaveApp.Output) throws -> String {
        guard case .ok(let ok) = output, case .json(let payload) = ok.body else {
            XCTFail("unexpected leaveApp output: \(output)")
            throw UnexpectedShape.output
        }
        return try XCTUnwrap(
            JSONSerialization.jsonObject(
                with: JSONEncoder().encode(payload)
            ) as? [String: Any]
        )["status"] as? String ?? "missing"
    }

    func testEnterAppSendsTheBundleIdToTheProviderAndReportsOk() async throws {
        let provider = FakeElementProvider()
        let handler = APIHandler(provider: provider)

        let output = try await handler.enterApp(
            .init(body: .json(.init(bundleId: "com.apple.mobilesafari")))
        )

        XCTAssertEqual(try statusJSON(output), "ok")
        XCTAssertEqual(provider.enterAppCalls, ["com.apple.mobilesafari"])
    }

    func testEnterAppReportsNotForegroundWhenActivationNeverReachesTheForeground() async throws {
        let provider = FakeElementProvider()
        provider.enterAppResult = .notForeground
        let handler = APIHandler(provider: provider)

        let output = try await handler.enterApp(
            .init(body: .json(.init(bundleId: "com.example.uninstalled")))
        )

        XCTAssertEqual(try statusJSON(output), "not-foreground")
    }

    func testLeaveAppCallsTheProviderAndReportsOk() async throws {
        let provider = FakeElementProvider()
        let handler = APIHandler(provider: provider)

        let output = try await handler.leaveApp(.init(body: .json(.init())))

        XCTAssertEqual(try statusJSON(output), "ok")
        XCTAssertEqual(provider.leaveAppCalls, 1)
    }

    func testLeaveAppReportsNotForegroundWhenReactivationNeverReachesTheForeground() async throws {
        let provider = FakeElementProvider()
        provider.leaveAppResult = .notForeground
        let handler = APIHandler(provider: provider)

        let output = try await handler.leaveApp(.init(body: .json(.init())))

        XCTAssertEqual(try statusJSON(output), "not-foreground")
    }
}
