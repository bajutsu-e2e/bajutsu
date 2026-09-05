import XCTest
@testable import BajutsuRunner

/// `/tap` folds the interruption monitor's drain into its own reply (BE-0407 Unit 6), so a step whose
/// last driver call was a tap can skip the separate `/interruptionPolicy/drain` round trip. This is
/// deliberately `APIHandler`-only — `Router` (legacy, kept only for `APIHandlerParityTests`) never
/// gains it, so those parity assertions stay meaningful for everything they still compare.
final class TapDrainFoldTests: XCTestCase {
    private static let sample = ElementSnapshot(
        identifier: "ok", label: "OK", value: nil,
        traits: ["button"], frame: (0, 0, 10, 10), backingElement: NSObject()
    )

    override func setUp() {
        super.setUp()
        // `InterruptionPolicyStore.shared` is process-wide and every `/tap` here drains it, so what
        // some earlier test (in this class or any other) left behind must not decide what this one
        // sees. Reset up front rather than per-test, so a new test cannot forget.
        _ = InterruptionPolicyStore.shared.drain()
    }

    private func firstHandle(_ handler: APIHandler) async throws -> String {
        let out = try await handler.queryElements(.init())
        guard case .ok(let ok) = out, case .json(let payload) = ok.body,
              let handle = payload.elements.first?.handle
        else {
            throw XCTSkip("no handle minted")
        }
        return handle
    }

    private func tapReplyJSON(_ handler: APIHandler, handle: String) async throws -> [String: Any] {
        let out = try await handler.tap(.init(body: .json(.init(handle: handle))))
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            throw XCTSkip("unexpected tap output")
        }
        return try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(payload)) as? [String: Any]
        )
    }

    func testATapThatFollowsATappedInterruptionCarriesItsLabelsInline() async throws {
        InterruptionPolicyStore.shared.setPolicy(InterruptionPolicy(governs: true))
        InterruptionPolicyStore.shared.record("Not Now")

        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        let handler = APIHandler(provider: provider)
        let handle = try await firstHandle(handler)

        let json = try await tapReplyJSON(handler, handle: handle)
        XCTAssertEqual(json["labels"] as? [String], ["Not Now"])
        XCTAssertEqual(json["unmatched"] as? [[String]], [])

        // Drained: a second tap sees nothing left, so the fields come back present but empty.
        let second = try await tapReplyJSON(handler, handle: handle)
        XCTAssertEqual(second["labels"] as? [String], [])
        XCTAssertEqual(second["unmatched"] as? [[String]], [])
    }

    func testATapThatFollowsADeclinedInterruptionCarriesItsUnmatchedButtonsInline() async throws {
        InterruptionPolicyStore.shared.setPolicy(InterruptionPolicy(governs: true))
        InterruptionPolicyStore.shared.recordDeclined(["Save", "Not Now"])

        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        let handler = APIHandler(provider: provider)
        let handle = try await firstHandle(handler)

        let json = try await tapReplyJSON(handler, handle: handle)
        XCTAssertEqual(json["labels"] as? [String], [])
        XCTAssertEqual(json["unmatched"] as? [[String]], [["Save", "Not Now"]])
    }

    func testAnUneventfulTapStillCarriesBothFieldsAsEmptyArrays() async throws {
        // No interruption at all is the overwhelmingly common case — but the fields must still be
        // *present*, empty arrays and all, so a driver on this contract can tell "drained and found
        // nothing" apart from "this runner never folds a drain into `/tap` at all" (BE-0407 Unit 6).
        // Collapsing the two would let a step's real interruption go unreported to a caller who
        // believed an absent pair meant the same as an empty one. `APIHandlerParityTests`'
        // `/tap` assertions account for this deliberate difference from the undrained `Router`.
        _ = InterruptionPolicyStore.shared.drain()  // clear anything a prior test left behind

        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        let handler = APIHandler(provider: provider)
        let handle = try await firstHandle(handler)

        let json = try await tapReplyJSON(handler, handle: handle)
        XCTAssertEqual(json["labels"] as? [String], [])
        XCTAssertEqual(json["unmatched"] as? [[String]], [])
    }

    func testACoordinateTapAlsoFoldsTheDrain() async throws {
        InterruptionPolicyStore.shared.setPolicy(InterruptionPolicy(governs: true))
        InterruptionPolicyStore.shared.record("Not Now")

        let handler = APIHandler(provider: FakeElementProvider())
        let out = try await handler.tap(.init(body: .json(.init(point: [1, 2]))))
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            return XCTFail("unexpected tap output")
        }
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(payload)) as? [String: Any]
        )
        XCTAssertEqual(json["labels"] as? [String], ["Not Now"])
    }

    func testIsHittableNeverFoldsTheDrain() async throws {
        // Only `/tap` does this — every other actuation stays undrained, and the driver still drains
        // explicitly for a step whose last call was one of those.
        InterruptionPolicyStore.shared.setPolicy(InterruptionPolicy(governs: true))
        InterruptionPolicyStore.shared.record("Not Now")

        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        let handler = APIHandler(provider: provider)
        let handle = try await firstHandle(handler)

        let out = try await handler.isHittable(.init(body: .json(.init(handle: handle))))
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            return XCTFail("unexpected isHittable output")
        }
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(payload)) as? [String: Any]
        )
        XCTAssertNil(json["labels"])
        // The drain is untouched by isHittable, so it is still there for a later explicit drain.
        XCTAssertEqual(InterruptionPolicyStore.shared.drain().tapped, ["Not Now"])
    }
}
