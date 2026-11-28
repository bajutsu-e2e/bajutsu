import XCTest
@testable import BajutsuRunner

/// `/location` (BE-0475) exercised against a fake `ElementProviding` conformer: each provider outcome
/// becomes its own reply status, and an out-of-range coordinate answers 400 without reaching it.
final class LocationTests: XCTestCase {
    private enum UnexpectedShape: Error { case output }

    private func status(_ reply: Components.Schemas.LocationReply) throws -> String? {
        let object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(reply))
        return (object as? [String: Any])?["status"] as? String
    }

    private func set(_ provider: FakeElementProvider, _ lat: Double, _ lon: Double) async throws
        -> Operations.setLocation.Output
    {
        try await APIHandler(provider: provider).setLocation(.init(body: .json(.init(lat: lat, lon: lon))))
    }

    func testSetLocationHandsTheCoordinateToTheProviderAndReportsOk() async throws {
        let provider = FakeElementProvider()
        guard case .ok(let ok) = try await set(provider, 35.6812, 139.7671), case .json(let body) = ok.body else {
            XCTFail("expected a 200 reply")
            throw UnexpectedShape.output
        }
        XCTAssertEqual(try status(body), "ok")
        XCTAssertEqual(provider.locationCalls, [[35.6812, 139.7671]])
    }

    func testUnsupportedKeepsItsOwnStatusOnBothRoutes() async throws {
        let provider = FakeElementProvider()
        provider.locationResult = .unsupported
        guard case .ok(let setOk) = try await set(provider, 0, 0), case .json(let setBody) = setOk.body else {
            XCTFail("expected a 200 reply")
            throw UnexpectedShape.output
        }
        XCTAssertEqual(try status(setBody), "unsupported")
        let cleared = try await APIHandler(provider: provider).clearLocation(.init())
        guard case .ok(let clearOk) = cleared, case .json(let clearBody) = clearOk.body else {
            XCTFail("expected a 200 reply")
            throw UnexpectedShape.output
        }
        XCTAssertEqual(try status(clearBody), "unsupported")
        XCTAssertEqual(provider.locationCalls, [[0, 0], nil])
    }

    func testARaisedAssignmentIsAnErrorRatherThanUnsupported() async throws {
        let provider = FakeElementProvider()
        provider.locationResult = .failed
        guard case .ok(let ok) = try await set(provider, 1, 2), case .json(let body) = ok.body else {
            XCTFail("expected a 200 reply")
            throw UnexpectedShape.output
        }
        XCTAssertEqual(try status(body), "error")
    }

    func testClearLocationAssignsNil() async throws {
        let provider = FakeElementProvider()
        let cleared = try await APIHandler(provider: provider).clearLocation(.init())
        guard case .ok(let ok) = cleared, case .json(let body) = ok.body else {
            XCTFail("expected a 200 reply")
            throw UnexpectedShape.output
        }
        XCTAssertEqual(try status(body), "ok")
        XCTAssertEqual(provider.locationCalls, [nil])
    }

    func testOutOfRangeCoordinatesAnswer400WithoutReachingTheProvider() async throws {
        let provider = FakeElementProvider()
        let cases: [(Double, Double)] = [
            (90.5, 0.0), (-91.0, 0.0), (0.0, 180.5), (0.0, -181.0),
            (Double.nan, 0.0), (0.0, Double.infinity), (-Double.infinity, 0.0),
        ]
        for (lat, lon) in cases {
            guard case .badRequest = try await set(provider, lat, lon) else {
                XCTFail("(\(lat), \(lon)) should answer 400")
                continue
            }
        }
        XCTAssertEqual(provider.locationCalls, [])
    }

    func testTheBoundaryCoordinatesAreAccepted() async throws {
        let provider = FakeElementProvider()
        for (lat, lon) in [(90.0, 180.0), (-90.0, -180.0)] {
            guard case .ok = try await set(provider, lat, lon) else {
                XCTFail("(\(lat), \(lon)) should be accepted")
                continue
            }
        }
        XCTAssertEqual(provider.locationCalls, [[90, 180], [-90, -180]])
    }
}
