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
}
