import Foundation
import ObjCExceptionCatcher

/// The resident runner's serve loop, kept alive across a test interruption XCTest defers to it.
///
/// `APIHandler.caught` contains an exception raised *inside* an operation, but XCTest has a second
/// way to end the test that never raises there. XCUIAutomation reports most of its own failures —
/// a failed element snapshot, a failed interaction — as issues flagged to interrupt the test,
/// whatever `continueAfterFailure` says. While a run loop is nested on the test's main thread, as
/// the serve loop's own pump always is, XCTest cannot unwind at that point, so it only marks the
/// test to halt. It then raises `_XCTestCaseInterruptionException` from its run-loop observer the
/// moment the outermost run loop exits — which is the serve loop's own turn returning, outside
/// every handler boundary. Uncaught, that exception ends the test method and tears the runner down
/// between two requests (measured: a notification banner XCUITest could not snapshot during a
/// `swipe` on iOS 26.5, BE-0461 Unit 1).
///
/// So each turn of the pump contains that one exception: the raise is logged and the loop serves on.
/// XCTest clears its halt mark before it raises, so the next turn starts clean. Any other exception
/// is raised again, as it was before this boundary existed: nothing shows the runner is still sound
/// after it, so XCTest tears the test down and the Python side respawns a fresh runner.
public enum ResidentLoop {
    /// The exception XCTest raises for a deferred test interruption (`-[XCTestCase _interruptTest]`).
    static let deferredInterruption = "_XCTestCaseInterruptionException"

    /// Run one turn of the serve loop, containing XCTest's deferred test interruption.
    ///
    /// Args:
    ///   turn: One pump of the main run loop.
    ///   report: Where a contained interruption is described; the runner's stderr by default, which
    ///     `BAJUTSU_XCUITEST_RUNNER_LOG` captures.
    ///
    /// Returns:
    ///   `true` if the turn returned normally, `false` if it raised the deferred interruption and
    ///   was contained. Any other exception is raised again, with its name and reason.
    @discardableResult
    public static func runTurn(
        _ turn: () -> Void, report: ((String) -> Void)? = nil
    ) -> Bool {
        do {
            try ObjCExceptionCatcher.catchException(turn)
            return true
        } catch {
            let name = (error as NSError).userInfo["NSExceptionName"] as? String ?? "NSException"
            guard name == deferredInterruption else {
                NSException(
                    name: NSExceptionName(name), reason: error.localizedDescription, userInfo: nil
                ).raise()
                return false
            }
            (report ?? writeToStandardError)("bajutsu runner: the serve loop raised \(name), still serving: \(error.localizedDescription)\n")
            return false
        }
    }

    private static func writeToStandardError(_ message: String) {
        FileHandle.standardError.write(Data(message.utf8))
    }
}
