import XCTest
@testable import BajutsuRunner

final class GestureRetryTests: XCTestCase {
    func testActuatesOnceWhenTheFirstGestureLands() {
        var actuations = 0
        var state = "idle"
        actuateUntilStateChanges(
            maxAttempts: 4,
            signature: { state },
            actuate: {
                actuations += 1
                state = "pinched"
            }
        )
        XCTAssertEqual(actuations, 1, "a landed gesture must not be re-issued")
    }

    func testRetriesUntilADroppedGestureFinallyLands() {
        var actuations = 0
        var state = "idle"
        actuateUntilStateChanges(
            maxAttempts: 4,
            signature: { state },
            // The Simulator drops the first two synthesized gestures; the third lands.
            actuate: {
                actuations += 1
                if actuations >= 3 { state = "pinched" }
            }
        )
        XCTAssertEqual(actuations, 3)
    }

    func testStopsAtTheCapWhenTheStateNeverChanges() {
        var actuations = 0
        actuateUntilStateChanges(
            maxAttempts: 4,
            signature: { "idle" },
            actuate: { actuations += 1 }
        )
        XCTAssertEqual(actuations, 4, "a no-op gesture must return at the cap, not loop")
    }

    func testActuatesAtLeastOnceEvenWithANonPositiveCap() {
        var actuations = 0
        actuateUntilStateChanges(
            maxAttempts: 0,
            signature: { "idle" },
            actuate: { actuations += 1 }
        )
        XCTAssertEqual(actuations, 1)
    }
}
