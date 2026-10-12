import Foundation
import ObjCExceptionCatcher

/// The one throwaway touch the runner synthesizes before it serves, so no scenario's first tap is
/// the one XCUITest's event playback is slowest to deliver (#2157).
///
/// XCUITest plays every synthesized touch through testmanagerd, which attaches a fresh virtual
/// digitizer to backboardd for each playback. The first playback in a testmanagerd process pays a
/// one-time setup cost before that digitizer registers — measured 0.24–1.6 s on the iOS 26.4 CI
/// host, against about 1 ms for every playback after it. The playback does not wait for the
/// registration, so on a slow host its touch-down reaches backboardd first and is dropped ("didn't
/// see a previous touch down … skipping event"), while XCUITest still reports the tap as delivered.
/// The step then passes having done nothing.
///
/// Paying that cost with a touch nothing receives keeps every scenario's taps on the fast path. The
/// touch is aimed far past the bottom-left corner. backboardd clamps it to the screen edge and hands
/// it only to SpringBoard's edge-gesture recognizer, and a motionless touch there starts no system
/// gesture. Measured on iOS 26.4 in portrait: the app under test received no touch. The bottom edge
/// rather than the top: a top-corner tap lands on the status bar, which SpringBoard turns into a
/// scroll-to-top action for the foreground app, invisible as a touch but a change to a screen the
/// runner attached to (BE-0455).
///
/// It runs before the scenario's interruption policy can arrive, so it must not reach XCUITest's
/// interruption handling, whose default handler would answer a permission prompt with its default
/// button (BE-0406). Aiming it at SpringBoard is what keeps it out: a system prompt is
/// SpringBoard's own element, not an interruption of a SpringBoard-targeted event. Measured on
/// iOS 26.4 with the notification prompt up: the warm-up left the prompt standing and never invoked
/// the monitor, while an app-targeted tap invoked it and the default handler answered the prompt.
public enum EventSynthesisWarmUp {
    /// Where the warm-up touch is aimed, in points from the screen's top-left corner: far enough
    /// past the bottom-left corner that no screen size brings it on-screen.
    public static let offscreenOffset = CGVector(dx: -5000, dy: 5000)

    /// Whether the warm-up touch is being synthesized right now.
    ///
    /// XCUITest reports most interaction failures as a *recorded* issue rather than a raise, and the
    /// runner's `record(_:)` treats any issue recorded before the server binds as a fatal startup
    /// failure. Reading this flag lets it tell the warm-up's failure apart and let startup go on.
    /// Main thread only, like every XCUITest call.
    public private(set) static var inProgress = false

    /// Synthesize the warm-up touch, containing any exception it raises.
    ///
    /// A failed warm-up only leaves the first scenario tap on the slow path it was on before, so it
    /// is logged and startup continues rather than failing the runner. A raise is contained here; a
    /// recorded failure is the caller's to contain, while `inProgress` is set.
    ///
    /// - Parameters:
    ///   - touch: Synthesizes the throwaway touch.
    ///   - report: Where a contained exception is described; the runner's stderr by default, which
    ///     `BAJUTSU_XCUITEST_RUNNER_LOG` captures.
    /// - Returns: `true` if the touch was synthesized, `false` if it raised and was contained.
    @discardableResult
    public static func run(_ touch: () -> Void, report: ((String) -> Void)? = nil) -> Bool {
        inProgress = true
        defer { inProgress = false }
        do {
            try ObjCExceptionCatcher.catchException(touch)
            return true
        } catch {
            (report ?? writeToStandardError)(
                "bajutsu runner: event-synthesis warm-up raised, continuing: \(error.localizedDescription)\n"
            )
            return false
        }
    }

    private static func writeToStandardError(_ message: String) {
        FileHandle.standardError.write(Data(message.utf8))
    }
}
