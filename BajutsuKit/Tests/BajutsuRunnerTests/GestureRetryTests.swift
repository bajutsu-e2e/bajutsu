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

    // MARK: - settlesTo (the setPickerValue read-back, BE-0356)

    func testSettlesToConfirmsAValueAlreadyAtRest() {
        var reads = 0
        let landed = settlesTo("大学", maxSamples: 5, sample: { reads += 1; return "大学" })
        XCTAssertTrue(landed)
        XCTAssertEqual(reads, 2, "a wheel already at rest costs one confirming read beyond the first")
    }

    func testSettlesToKeepsSamplingWhileTheWheelIsStillSettling() {
        // A decelerating wheel reports the rows it passes before it stops, so an early read would
        // call a value the wheel does have absent.
        var reads = 0
        let rows = ["中学", "高校", "大学", "大学"]
        let landed = settlesTo("大学", maxSamples: 5, sample: {
            defer { reads += 1 }
            return rows[min(reads, rows.count - 1)]
        })
        XCTAssertTrue(landed)
        XCTAssertEqual(reads, 4)
    }

    func testSettlesToRejectsAValueTheWheelOnlyPassedThrough() {
        // The failure the two-consecutive-sample rule exists for: the wheel sweeps through the
        // wanted row and rests one past it. A first-match rule would report a landing, leaving the
        // step green with the wheel on the wrong row — the silent approximate outcome the
        // determinism directive rules out.
        var reads = 0
        let rows = ["中学", "大学", "大学院", "大学院", "大学院"]
        let landed = settlesTo("大学", maxSamples: 5, sample: {
            defer { reads += 1 }
            return rows[min(reads, rows.count - 1)]
        })
        XCTAssertFalse(landed)
    }

    func testSettlesToReportsAValueTheWheelNeverShows() {
        var reads = 0
        let landed = settlesTo("存在しない", maxSamples: 4, sample: { reads += 1; return "大学" })
        XCTAssertFalse(landed)
        XCTAssertEqual(reads, 4, "an absent value costs the cap and then fails loudly")
    }

    func testSettlesToTreatsAnUnreadableSampleAsBreakingTheRun() {
        // nil means *couldn't observe*, never *matched* — the same rule actuateUntilStateChanges
        // follows. A value that could not be read has not been shown to be holding, so it restarts
        // the run rather than counting toward it.
        var reads = 0
        let rows: [String?] = ["大学", nil, "大学", "大学"]
        let landed = settlesTo("大学", maxSamples: 5, sample: {
            defer { reads += 1 }
            return rows[min(reads, rows.count - 1)]
        })
        XCTAssertTrue(landed)
        XCTAssertEqual(reads, 4, "the nil broke the first run, so the match had to be re-established")
    }

    func testSettlesToAlwaysReadsEnoughToConfirmARun() {
        // The cap clamps to two, not one: a lower cap could never satisfy the two-sample rule and
        // would report every landing as absent.
        var reads = 0
        let landed = settlesTo("大学", maxSamples: 0, sample: { reads += 1; return "大学" })
        XCTAssertTrue(landed)
        XCTAssertEqual(reads, 2)
    }
}
