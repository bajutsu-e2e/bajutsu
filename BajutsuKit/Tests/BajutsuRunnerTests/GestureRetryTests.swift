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

    func testAnUnreadableSampleIsNotMistakenForALanding() {
        var actuations = 0
        // Every post-actuation read fails (nil). A nil sample must not read as a change, so a
        // genuinely dropped gesture keeps retrying to the cap rather than stopping after one attempt.
        actuateUntilStateChanges(
            maxAttempts: 4,
            signature: { actuations == 0 ? "idle" : nil },
            actuate: { actuations += 1 }
        )
        XCTAssertEqual(actuations, 4)
    }

    func testKeepsRetryingThroughATransientReadFailureUntilItLands() {
        var actuations = 0
        actuateUntilStateChanges(
            maxAttempts: 5,
            signature: {
                switch actuations {
                case 0: return "idle"   // before
                case 1: return nil      // first post-actuation read transiently fails
                case 2: return "idle"   // still dropped
                default: return "pinched"  // landed on the third attempt
                }
            },
            actuate: { actuations += 1 }
        )
        XCTAssertEqual(actuations, 3)
    }
}
