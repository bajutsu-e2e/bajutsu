import Foundation
import XCTest

@testable import BajutsuKit

// Selected by the same compilation condition as the channel itself, so the unflagged `swift test`
// in CI still compiles this file — to nothing, which is what proves the compile-out is clean.
#if BAJUTSU_ENABLE_CONTROL_CHANNEL

/// Unit tests for the app side of the in-app control channel (BE-0365 unit 2) — the decoding,
/// dispatch, and acknowledgement shaping, which are pure Foundation and need no Simulator (so they
/// run in `swift test` on a plain macOS runner, like the mock-matching tests beside them).
final class BajutsuControlChannelTests: XCTestCase {
    override func tearDown() {
        BajutsuControlChannel.stop()
        BajutsuTouch.setMarkersVisible(true)
        super.tearDown()
    }

    private func drained(_ json: String) -> [BajutsuAppCommand] {
        BajutsuControlChannel.commands(from: Data(json.utf8))
    }

    private func command(
        id: String = "c1", capability: String = "touch_visualization", enabled: Bool? = true
    ) -> BajutsuAppCommand {
        var payload: [String: Any] = ["id": id, "capability": capability]
        if let enabled { payload["enabled"] = enabled }
        return BajutsuAppCommand(id: id, capability: capability, payload: payload)
    }

    // --- decoding ---

    func testDecodesEveryCommandInTheOrderBajutsuQueuedThem() {
        let commands = drained(
            """
            [{"id": "c1", "capability": "touch_visualization", "enabled": false},
             {"id": "c2", "capability": "touch_visualization", "enabled": true}]
            """
        )
        XCTAssertEqual(commands.map(\.id), ["c1", "c2"])
        XCTAssertEqual(commands.map(\.capability), ["touch_visualization", "touch_visualization"])
        XCTAssertEqual(commands.first?.payload["enabled"] as? Bool, false)
    }

    func testAnEmptyDrainDecodesToNothing() {
        XCTAssertTrue(drained("[]").isEmpty)
    }

    func testACommandWithoutAnIdIsDropped() {
        // Nothing ties it to a wait, so there is no acknowledgement to send for it; the neighbour
        // that does carry an id still arrives.
        let commands = drained(
            """
            [{"capability": "touch_visualization", "enabled": true},
             {"id": "", "capability": "touch_visualization", "enabled": true},
             {"id": "c9", "capability": "touch_visualization", "enabled": true}]
            """
        )
        XCTAssertEqual(commands.map(\.id), ["c9"])
    }

    func testAResponseThatIsNotAnArrayDecodesToNothing() {
        XCTAssertTrue(drained(#"{"id": "c1", "capability": "touch_visualization"}"#).isEmpty)
    }

    func testMalformedJSONDecodesToNothing() {
        XCTAssertTrue(drained("not json at all").isEmpty)
    }

    func testACommandInAShapeThisBuildPredatesSurvivesDecoding() {
        // The mid-scenario stub table (unit 4) carries no `enabled`. Decoding keeps it so `apply`
        // can refuse it by name; dropping it here would leave bajutsu's wait timing out blind.
        let commands = drained(#"[{"id": "c1", "capability": "request_stubs", "rules": []}]"#)
        XCTAssertEqual(commands.map(\.capability), ["request_stubs"])
    }

    // --- dispatch ---

    func testTouchVisualizationCommandTogglesTheMarkers() {
        XCTAssertTrue(BajutsuControlChannel.apply(command(enabled: false)).applied)
        XCTAssertFalse(BajutsuTouch.markersVisible)

        XCTAssertTrue(BajutsuControlChannel.apply(command(enabled: true)).applied)
        XCTAssertTrue(BajutsuTouch.markersVisible)
    }

    func testAnUnsupportedCapabilityIsRefusedByNameRatherThanIgnored() {
        let outcome = BajutsuControlChannel.apply(command(capability: "request_stubs"))
        XCTAssertFalse(outcome.applied)
        XCTAssertTrue(
            outcome.reason.contains("request_stubs"),
            "the reason has to name the capability bajutsu asked for, got: \(outcome.reason)"
        )
    }

    func testACommandNamingNoCapabilityIsRefusedWithItsOwnReason() {
        let outcome = BajutsuControlChannel.apply(command(capability: ""))
        XCTAssertFalse(outcome.applied)
        XCTAssertFalse(outcome.reason.isEmpty)
    }

    func testATouchVisualizationCommandWithoutAStateIsRefused() {
        BajutsuTouch.setMarkersVisible(false)
        let outcome = BajutsuControlChannel.apply(command(enabled: nil))
        XCTAssertFalse(outcome.applied)
        XCTAssertFalse(outcome.reason.isEmpty)
        XCTAssertFalse(BajutsuTouch.markersVisible, "a refused command must change nothing")
    }

    // --- the acknowledgement ---

    func testTheReportCarriesTheThreeFieldsTheCollectorReads() {
        let refused = BajutsuControlChannel.report(
            for: command(), outcome: .refused("no such thing")
        )
        XCTAssertEqual(refused["id"] as? String, "c1")
        XCTAssertEqual(refused["applied"] as? Bool, false)
        XCTAssertEqual(refused["reason"] as? String, "no such thing")

        let accepted = BajutsuControlChannel.report(for: command(), outcome: .accepted)
        XCTAssertEqual(accepted["applied"] as? Bool, true)
        XCTAssertEqual(accepted["reason"] as? String, "")
    }

    func testTheEndpointsSitUnderTheCollectorRoot() {
        let endpoint = BajutsuControlChannel.endpoints(
            collector: URL(string: "http://127.0.0.1:6801")!, token: "t"
        )
        XCTAssertEqual(endpoint.commands.absoluteString, "http://127.0.0.1:6801/commands")
        XCTAssertEqual(endpoint.acknowledge.absoluteString, "http://127.0.0.1:6801/commands/ack")
    }

    // --- activation ---

    private static let collector = URL(string: "http://127.0.0.1:1")!

    func testTheChannelIsInertWithoutItsLaunchEnvKey() {
        BajutsuControlChannel.startIfEnabled(
            environment: [:], collector: Self.collector, token: "t"
        )
        XCTAssertFalse(BajutsuControlChannel.isRunning)

        BajutsuControlChannel.startIfEnabled(
            environment: [BajutsuControlChannel.activationKey: "0"],
            collector: Self.collector, token: "t"
        )
        XCTAssertFalse(BajutsuControlChannel.isRunning)
    }

    func testTheChannelIsInertWithoutACollectorToPoll() {
        let enabled = [BajutsuControlChannel.activationKey: "1"]
        BajutsuControlChannel.startIfEnabled(environment: enabled, collector: nil, token: "t")
        XCTAssertFalse(BajutsuControlChannel.isRunning)

        BajutsuControlChannel.startIfEnabled(
            environment: enabled, collector: Self.collector, token: ""
        )
        XCTAssertFalse(BajutsuControlChannel.isRunning)
    }

    func testARejectedTokenOrAnUnservedPathReadsAsTerminal() {
        // Neither answer can be retried into a working channel, and a loop that kept asking would
        // leave a timer running inside the app under test for the rest of its life.
        XCTAssertTrue(BajutsuControlChannel.isTerminal(status: 401))
        XCTAssertTrue(BajutsuControlChannel.isTerminal(status: 404))
        // A busy or restarting collector is weather, not misconfiguration, so polling continues.
        XCTAssertFalse(BajutsuControlChannel.isTerminal(status: 200))
        XCTAssertFalse(BajutsuControlChannel.isTerminal(status: 503))
        XCTAssertFalse(BajutsuControlChannel.isTerminal(status: nil))
    }

    func testTheChannelStartsWhenBothGuardsAreSatisfiedAndStopsOnRequest() {
        BajutsuControlChannel.startIfEnabled(
            environment: [BajutsuControlChannel.activationKey: "1"],
            collector: Self.collector, token: "t"
        )
        XCTAssertTrue(BajutsuControlChannel.isRunning)

        BajutsuControlChannel.stop()
        XCTAssertFalse(BajutsuControlChannel.isRunning)
    }

    // --- the round trip ---

    func testAQueuedCommandIsDrainedAppliedAndAcknowledgedOverTheWire() throws {
        // The pure pieces above check the shapes; this checks that the poll round actually sends
        // them — the method, path, and bearer token of the drain, and the report that follows it.
        let collector = LoopbackCollectorStub()
        let acknowledged = expectation(description: "the app reported on the command")
        // Both set before the listener exists, so the serving thread never races the test's own.
        collector.onAcknowledge = { acknowledged.fulfill() }
        collector.enqueue(#"[{"id": "c7", "capability": "touch_visualization", "enabled": false}]"#)
        let port = try collector.start()
        defer { collector.stop() }

        BajutsuControlChannel.startIfEnabled(
            environment: [BajutsuControlChannel.activationKey: "1"],
            collector: URL(string: "http://127.0.0.1:\(port)")!,
            token: "run-token"
        )
        wait(for: [acknowledged], timeout: 10)

        let drain = try XCTUnwrap(collector.requests.first { $0.path == "/commands" })
        XCTAssertEqual(drain.method, "GET")
        XCTAssertEqual(drain.authorization, "Bearer run-token")

        let report = try XCTUnwrap(collector.requests.first { $0.path == "/commands/ack" })
        XCTAssertEqual(report.method, "POST")
        XCTAssertEqual(report.authorization, "Bearer run-token")
        let body = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: report.body) as? [String: Any]
        )
        XCTAssertEqual(body["id"] as? String, "c7")
        XCTAssertEqual(body["applied"] as? Bool, true)

        XCTAssertFalse(BajutsuTouch.markersVisible, "the command's state has to have taken effect")
    }

    func testAnUnservedPathActuallyEndsThePollLoop() throws {
        // The predicate above says a 404 is terminal; this says the poll round acts on it, so that
        // dropping `poll`'s terminal branch fails a test instead of silently leaving a 150 ms timer
        // inside the app for the rest of the process's life.
        let collector = LoopbackCollectorStub()
        let port = try collector.start()
        defer { collector.stop() }

        BajutsuControlChannel.startIfEnabled(
            environment: [BajutsuControlChannel.activationKey: "1"],
            // The stub 404s every path but `/commands`, the same way unit 1's `do_GET` does, so a
            // root carrying an extra segment stands in for a version-skewed poll.
            collector: URL(string: "http://127.0.0.1:\(port)/skewed")!,
            token: "run-token"
        )

        // A condition wait, not a duration: nothing else clears `endpoint`, so the loop going quiet
        // is the terminal branch having fired.
        wait(
            for: [
                XCTNSPredicateExpectation(
                    predicate: NSPredicate { _, _ in !BajutsuControlChannel.isRunning }, object: nil
                )
            ],
            timeout: 10
        )
        // Asserted after the wait rather than before it, where a 404 answered faster than the test
        // thread resumes would make it flaky. A recorded drain is what rules out the loop having
        // never started, which is the other way the wait above could be satisfied.
        XCTAssertEqual(collector.requests.map(\.path), ["/skewed/commands"])
    }
}

#endif
