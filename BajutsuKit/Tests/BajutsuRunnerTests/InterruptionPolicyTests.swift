import XCTest
@testable import BajutsuRunner

/// `InterruptionPolicy.label(for:)` is the sole tap-vs-decline decider since BE-0406 Unit 2b
/// removed the built-in dismissive-candidate fallback: `nil` now escalates from "quietly tap a
/// dismissive word" to "fail the step", so a regression here either taps an unnamed button or
/// fails every governed step. It needs no Simulator — a pure function on a `public struct`.
final class InterruptionPolicyTests: XCTestCase {
    func testMatchesARuleWhoseIdentifyingLabelsAreAllPresentExactlyOnce() {
        let policy = InterruptionPolicy(
            rules: [InterruptionRule(identify: ["Allow", "Don't Allow"], tap: "Don't Allow")],
            governs: true
        )
        XCTAssertEqual(policy.label(for: ["Allow", "Don't Allow"]), "Don't Allow")
    }

    func testDoesNotMatchWhenAnIdentifyingLabelIsMissing() {
        let policy = InterruptionPolicy(
            rules: [InterruptionRule(identify: ["Allow", "Don't Allow"], tap: "Don't Allow")],
            governs: true
        )
        XCTAssertNil(policy.label(for: ["Allow"]))
    }

    func testDoesNotMatchWhenAnIdentifyingLabelAppearsTwice() {
        // "Exactly once", not merely present, so an alert with two identically labelled buttons
        // never resolves to whichever matched first (determinism first).
        let policy = InterruptionPolicy(
            rules: [InterruptionRule(identify: ["Allow", "Don't Allow"], tap: "Don't Allow")],
            governs: true
        )
        XCTAssertNil(policy.label(for: ["Allow", "Allow", "Don't Allow"]))
    }

    func testAnAlertNoRuleIdentifiesReturnsNilRatherThanAFallback() {
        // The behavior BE-0406 Unit 2b removed: no built-in candidate list stands in for an
        // unidentified alert any more.
        let policy = InterruptionPolicy(
            rules: [InterruptionRule(identify: ["Allow", "Don't Allow"], tap: "Don't Allow")],
            governs: true
        )
        XCTAssertNil(policy.label(for: ["Save", "Not Now"]))
    }

    func testAGoverningPolicyWithNoRulesNeverMatches() {
        // A scenario whose only rules were filtered out as in-tree-only still governs (BE-0406
        // Unit 2b), but an empty rule list here can still never identify anything.
        let policy = InterruptionPolicy(rules: [], governs: true)
        XCTAssertNil(policy.label(for: ["Allow", "Don't Allow"]))
    }

    func testTheFirstMatchingRuleWinsOverALaterOne() {
        let policy = InterruptionPolicy(
            rules: [
                InterruptionRule(identify: ["Allow", "Don't Allow"], tap: "Don't Allow"),
                InterruptionRule(identify: ["Allow", "Don't Allow"], tap: "Allow"),
            ],
            governs: true
        )
        XCTAssertEqual(policy.label(for: ["Allow", "Don't Allow"]), "Don't Allow")
    }

    // MARK: - Local Network and notifications: same buttons, told apart by the title marker

    private static let localNetworkMarker = "title: Allow “%@” to find devices on local networks?"

    func testTheTitleReducesToApplesTemplate() {
        XCTAssertEqual(
            alertTitleMarker("Allow “Showcase SwiftUI” to find devices on local networks?"),
            Self.localNetworkMarker)
        XCTAssertEqual(alertTitleMarker("No quotes"), "title: No quotes")
    }

    func testAnExcludedLabelRulesARuleOut() {
        let policy = InterruptionPolicy(
            rules: [
                InterruptionRule(
                    identify: ["Allow", "Don’t Allow"], tap: "Allow",
                    exclude: [Self.localNetworkMarker]),
                InterruptionRule(
                    identify: ["Allow", "Don’t Allow", Self.localNetworkMarker], tap: "Don’t Allow"),
            ],
            governs: true
        )
        // The Local Network prompt skips the notification rule and meets its own.
        XCTAssertEqual(
            policy.label(for: ["Don’t Allow", "Allow", Self.localNetworkMarker]), "Don’t Allow")
        // A notification prompt (another title) meets the notification rule.
        XCTAssertEqual(
            policy.label(
                for: ["Don’t Allow", "Allow", "title: “%@” Would Like to Send You Notifications"]),
            "Allow")
    }

    func testDrainReportsTheAlertEachTapAnswered() {
        // The label alone cannot tell Local Network from notifications; the matched alert can.
        let store = InterruptionPolicyStore()
        store.record("Allow", alert: ["Don’t Allow", "Allow", Self.localNetworkMarker])
        store.record("Not Now")
        let drained = store.drain()
        XCTAssertEqual(drained.tapped, ["Allow", "Not Now"])
        XCTAssertEqual(drained.tappedAlerts, [["Don’t Allow", "Allow", Self.localNetworkMarker], []])
        XCTAssertTrue(store.drain().tappedAlerts.isEmpty)
    }
}
