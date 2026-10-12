import XCTest
@testable import BajutsuRunner

/// The runner's pre-serve warm-up touch (#2157). The touch itself needs a Simulator; this covers
/// the contract around it: it runs once, a raise never stops the runner from starting, and
/// `inProgress` brackets exactly the touch.
final class EventSynthesisWarmUpTests: XCTestCase {
    func testRunsTheTouchOnceAndReportsNothing() {
        var touches = 0
        var reports: [String] = []
        XCTAssertTrue(EventSynthesisWarmUp.run({ touches += 1 }, report: { reports.append($0) }))
        XCTAssertEqual(touches, 1)
        XCTAssertEqual(reports, [])
    }

    func testARaisedTouchIsContainedAndLogged() {
        // A failed warm-up only leaves the first tap on its old slow path, so startup must go on.
        var reports: [String] = []
        let exception = NSException(name: .genericException, reason: "No matches found", userInfo: nil)
        XCTAssertFalse(EventSynthesisWarmUp.run({ exception.raise() }, report: { reports.append($0) }))
        XCTAssertEqual(reports.count, 1)
        XCTAssertTrue(reports[0].contains("No matches found"), reports[0])
    }

    func testInProgressIsSetOnlyWhileTheTouchRuns() {
        // `record(_:)` reads this to keep a *recorded* warm-up failure from killing startup.
        var during = false
        XCTAssertFalse(EventSynthesisWarmUp.inProgress)
        EventSynthesisWarmUp.run({ during = EventSynthesisWarmUp.inProgress }, report: { _ in })
        XCTAssertTrue(during)
        XCTAssertFalse(EventSynthesisWarmUp.inProgress)
    }

    func testInProgressIsClearedAfterARaise() {
        let exception = NSException(name: .genericException, reason: "boom", userInfo: nil)
        EventSynthesisWarmUp.run({ exception.raise() }, report: { _ in })
        XCTAssertFalse(EventSynthesisWarmUp.inProgress)
    }
}
