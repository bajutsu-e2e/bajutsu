import XCTest
import ObjCExceptionCatcher
@testable import BajutsuRunner

/// The serve loop's containment of a test interruption XCTest raises as a run-loop turn returns
/// (`ResidentLoop`). The real raise needs XCTest's UI-testing host; this stands in for it with the
/// same exception name, raised the same way — out of the turn, after the operation that triggered
/// it has already replied.
final class ResidentLoopTests: XCTestCase {
    private func interruption() -> NSException {
        NSException(
            name: NSExceptionName("_XCTestCaseInterruptionException"),
            reason: "Interrupting test", userInfo: nil
        )
    }

    func testATurnThatReturnsNormallyReportsNothing() {
        var reports: [String] = []
        var ran = false
        XCTAssertTrue(ResidentLoop.runTurn({ ran = true }, report: { reports.append($0) }))
        XCTAssertTrue(ran)
        XCTAssertEqual(reports, [])
    }

    func testARaisedInterruptionIsContainedAndNamedInTheLog() {
        var reports: [String] = []
        let exception = interruption()
        XCTAssertFalse(ResidentLoop.runTurn({ exception.raise() }, report: { reports.append($0) }))
        XCTAssertEqual(reports.count, 1)
        XCTAssertTrue(reports[0].contains("_XCTestCaseInterruptionException"), reports[0])
        XCTAssertTrue(reports[0].contains("Interrupting test"), reports[0])
    }

    func testTheLoopKeepsServingAfterAContainedRaise() {
        // The incident's shape: one turn raises, and the turns after it must still run — the runner
        // stays resident instead of tearing down between two requests (BE-0461 Unit 1).
        var turns = 0
        let exception = interruption()
        for index in 0..<3 {
            ResidentLoop.runTurn({
                turns += 1
                if index == 0 { exception.raise() }
            }, report: { _ in })
        }
        XCTAssertEqual(turns, 3)
    }

    func testAnyOtherExceptionIsRaisedAgainWithItsNameAndReason() {
        // Only the deferred interruption is known to leave XCTest's state clean; anything else must
        // still end the test so the Python side respawns a fresh runner rather than reuse this one.
        var reports: [String] = []
        let other = NSException(name: .internalInconsistencyException, reason: "corrupted", userInfo: nil)
        XCTAssertThrowsError(try ObjCExceptionCatcher.catchException {
            ResidentLoop.runTurn({ other.raise() }, report: { reports.append($0) })
        }) { error in
            let info = (error as NSError).userInfo
            XCTAssertEqual(info["NSExceptionName"] as? String, NSExceptionName.internalInconsistencyException.rawValue)
            XCTAssertEqual(error.localizedDescription, "corrupted")
        }
        XCTAssertEqual(reports, [])
    }
}
