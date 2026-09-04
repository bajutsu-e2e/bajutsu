import Foundation

/// One rule of the interruption policy: the prompt's identifying labels, and the label to tap on it.
///
/// The Python mirror is `ResolvedAlertRule` (`bajutsu/common/orchestrator/types.py`). Matching
/// requires the prompt's *full* label set, since a single shared label (e.g. "Allow") cannot tell
/// two covered prompts apart.
public struct InterruptionRule: Sendable, Equatable {
    public let identify: [String]
    public let tap: String

    public init(identify: [String], tap: String) {
        self.identify = identify
        self.tap = tap
    }
}

/// Which button to press on an out-of-process alert that interrupts an XCUITest interaction.
///
/// This is a *mechanism*, not a policy: every label in it is resolved on the Python side from the
/// scenario's own `systemAlertHandling.rules`. Only the rules this surface can actually meet are
/// sent — an alert raised inside the application's own process never interrupts an XCUITest
/// interaction, so its rules are dropped rather than matched here (BE-0406). What lives in Swift is
/// only the matching discipline: a rule wins when every one of its identifying labels is on the
/// alert exactly once. "Exactly once", not merely present, so an alert with two identically labelled
/// buttons never resolves to whichever matched first (determinism first).
///
/// `governs` is true for any scenario whose guard is on, independent of whether any rule survived
/// the drop above (BE-0406 Unit 2b): a real declaration filtered down to nothing this surface can
/// act on is not the same as no declaration at all, and only the latter — an absent guard — should
/// still leave a declined alert unreported.
///
/// It exists because XCUITest resolves an interrupting alert *before* it synthesizes any element
/// interaction, and with no monitor registered it falls back to its own default handler, which taps
/// the alert's **default** button — granting a permission the scenario may have refused, invisibly.
/// A monitor that declines to act cannot replace that handler: XCUITest verifies the alert is gone
/// and, finding it still up, re-invokes the monitor on the very next interaction, which in a
/// resident runner is an unbounded loop. So the monitor has to actually answer, and this is what
/// tells it how — and when it cannot, `governs` is what tells the caller whether that decline is
/// worth reporting.
public struct InterruptionPolicy: Sendable, Equatable {
    public let rules: [InterruptionRule]
    public let governs: Bool

    public init(rules: [InterruptionRule] = [], governs: Bool = false) {
        self.rules = rules
        self.governs = governs
    }

    /// The label to tap on an alert offering `buttons`, or nil when no rule identifies it.
    ///
    /// Nil is the signal to decline the interruption rather than guess. Declining hands the alert
    /// back to XCUITest's default handler — no worse than before for a prompt no scenario
    /// described, and never a loop, because that handler does clear the alert. Since BE-0406 Unit 2b
    /// there is no built-in fallback here: an alert no rule identifies is always declined, and
    /// `governs` decides only whether that decline gets recorded.
    public func label(for buttons: [String]) -> String? {
        func presentExactlyOnce(_ label: String) -> Bool {
            buttons.filter { $0 == label }.count == 1
        }
        for rule in rules where rule.identify.allSatisfy(presentExactlyOnce) {
            return rule.tap
        }
        return nil
    }
}

/// The live interruption policy, plus what the monitor has tapped and declined since the last drain.
///
/// A shared store rather than a value threaded through `ElementProviding`, because the two sides
/// that need it never meet: the policy arrives on a server thread (`POST /interruptionPolicy`),
/// while the monitor that reads it runs on XCTest's own thread whenever an interaction is
/// interrupted. Every access is under one lock for that reason.
///
/// The drained tapped labels are what lets the Python side report an interruption-time dismissal as
/// an `AlertEvent`, so a prompt answered here is not silently missing from the run's report. The
/// drained declined button lists do the same for an alert nothing answered on the scenario's behalf
/// (BE-0406 Unit 2b): the failure mode this whole mechanism exists to end covers both.
public final class InterruptionPolicyStore: @unchecked Sendable {
    public static let shared = InterruptionPolicyStore()

    private let lock = NSLock()
    private var _policy = InterruptionPolicy()
    private var _tapped: [String] = []
    private var _declined: [[String]] = []

    public init() {}

    public var policy: InterruptionPolicy {
        lock.lock()
        defer { lock.unlock() }
        return _policy
    }

    /// Replaces the policy. Also clears the pending drain: what's queued belongs to whichever
    /// scenario set the policy it happened under, so carrying it into the next one would misreport
    /// it.
    public func setPolicy(_ policy: InterruptionPolicy) {
        lock.lock()
        defer { lock.unlock() }
        _policy = policy
        _tapped = []
        _declined = []
    }

    public func record(_ label: String) {
        lock.lock()
        defer { lock.unlock() }
        _tapped.append(label)
    }

    /// Records the buttons of an alert `governs` covered but no rule identified, before declining.
    public func recordDeclined(_ buttons: [String]) {
        lock.lock()
        defer { lock.unlock() }
        _declined.append(buttons)
    }

    /// Returns what was tapped and declined since the last drain, and clears both.
    public func drain() -> (tapped: [String], declined: [[String]]) {
        lock.lock()
        defer { lock.unlock() }
        let tapped = _tapped
        let declined = _declined
        _tapped = []
        _declined = []
        return (tapped, declined)
    }
}
