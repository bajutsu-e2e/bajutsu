import XCTest
import ObjCExceptionCatcher
@testable import BajutsuRunner

/// Collects what a ledger writes, in place of the unified log.
private final class MarkerSink: @unchecked Sendable {
    private let lock = NSLock()
    private var _lines: [String] = []

    var lines: [String] {
        lock.lock()
        defer { lock.unlock() }
        return _lines
    }

    func write(_ line: String) {
        lock.lock()
        _lines.append(line)
        lock.unlock()
    }
}

/// The touch-synthesis markers and the ids the runner reports against them (BE-0462 Unit 2): the
/// host can only attribute a dropped touch to the request that made it if every reply names the
/// window its touch was marked in, and no other.
final class SynthesisLedgerTests: XCTestCase {
    private var sink: MarkerSink!
    private var ledger: SynthesisLedger!

    private static let sample = ElementSnapshot(
        identifier: "ok", label: "OK", value: nil,
        traits: ["button"], frame: (0, 0, 10, 10), backingElement: NSObject()
    )
    private static let notifications = InterruptionRule(
        identify: ["Allow", "Don’t Allow"], tap: "Don’t Allow", surface: .native
    )

    override func setUp() {
        super.setUp()
        let sink = MarkerSink()
        self.sink = sink
        ledger = SynthesisLedger(nonce: "n0", sink: { sink.write($0) })
    }

    // MARK: - Markers

    func testEachSynthesisIsBracketedByBeginEndAndFenceUnderItsOwnId() {
        let first = ledger.mark { sink.write("touch") }
        let second = ledger.mark {}
        XCTAssertEqual(first.id, "n0/1")
        XCTAssertEqual(second.id, "n0/2")
        XCTAssertEqual(sink.lines, [
            "bajutsu.synthesis begin n0/1", "touch",
            "bajutsu.synthesis end n0/1", "bajutsu.synthesis fence n0/1",
            "bajutsu.synthesis begin n0/2",
            "bajutsu.synthesis end n0/2", "bajutsu.synthesis fence n0/2",
        ])
    }

    func testTheServingMarkerCarriesTheNonce() {
        ledger.announceServing()
        XCTAssertEqual(sink.lines, ["bajutsu.synthesis serving n0"])
    }

    func testTwoRunnersDrawDifferentNoncesSoTheirIdsNeverCollide() {
        // A respawned runner restarts its counter at 1 in the same device session's stream.
        let ids = (0..<8).map { _ in SynthesisLedger(sink: { _ in }).mark {}.id }
        XCTAssertEqual(Set(ids).count, ids.count)
        XCTAssertTrue(ids.allSatisfy { $0.hasSuffix("/1") })
    }

    func testASwiftThrowClosesItsOwnWindowWithAnAbort() {
        struct Refused: Error {}
        XCTAssertThrowsError(try ledger.mark { throw Refused() })
        XCTAssertNil(ledger.collect {}.synthesisId)
        ledger.abandonOpenWindows()
        XCTAssertEqual(sink.lines, ["bajutsu.synthesis begin n0/1", "bajutsu.synthesis abort n0/1"])
    }

    func testATouchCutShortByARaiseIsClosedWithAnAbortInnermostFirst() {
        try? ObjCExceptionCatcher.catchException {
            ledger.mark {
                ledger.mark(actuation: false) {
                    NSException(name: .genericException, reason: "No matches found").raise()
                }
            }
        }
        ledger.abandonOpenWindows()
        XCTAssertEqual(sink.lines, [
            "bajutsu.synthesis begin n0/1", "bajutsu.synthesis begin n0/2",
            "bajutsu.synthesis abort n0/2", "bajutsu.synthesis abort n0/1",
        ])
        // Nothing is left open to abort twice, and a completed window is never aborted.
        ledger.mark {}
        ledger.abandonOpenWindows()
        XCTAssertFalse(sink.lines.dropFirst(4).contains { $0.contains("abort") })
    }

    // MARK: - Collection

    func testCollectReportsTheLastActuationAndNilWhenNoneRan() {
        XCTAssertNil(ledger.collect {}.synthesisId)
        let retried = ledger.collect {
            ledger.mark {}
            ledger.mark {}
        }
        XCTAssertEqual(retried.synthesisId, "n0/2")
    }

    func testAMonitorTouchInsideARequestIsNeverTheRequestsOwnSynthesis() {
        let outcome = ledger.collect {
            ledger.mark {
                ledger.mark(actuation: false) {}
            }
        }
        XCTAssertEqual(outcome.synthesisId, "n0/1")
        let alone = ledger.collect { ledger.mark(actuation: false) {} }
        XCTAssertNil(alone.synthesisId)
    }

    func testANestedCollectionLeavesTheOuterOneAsItFoundIt() {
        let outer = ledger.collect { () -> String? in
            ledger.mark {}
            return ledger.collect { ledger.mark {} }.synthesisId
        }
        XCTAssertEqual(outer.value, "n0/2")
        XCTAssertEqual(outer.synthesisId, "n0/1")
    }

    // MARK: - Interruption records

    func testAnAnsweredShowingCarriesTheIdOfEveryPressBeforeTheDrain() {
        let store = InterruptionPolicyStore()
        store.recordMonitorPress(Self.notifications, alert: ["Allow"], synthesisId: "n0/1")
        store.recordMonitorPress(Self.notifications, alert: ["Allow"], synthesisId: "n0/2")
        let drained = store.drain()
        XCTAssertEqual(drained.tapped, ["Don’t Allow"])
        XCTAssertEqual(drained.tappedSynthesisIds, [["n0/1", "n0/2"]])

        // The entry left with the drain, so a later re-press has nothing to extend and adds nothing.
        store.recordMonitorPress(Self.notifications, alert: ["Allow"], synthesisId: "n0/3")
        XCTAssertEqual(store.drain(), InterruptionRecords())
    }

    func testAPolicyPushInsideAScenarioLeavesARePressNoStaleEntryToExtend() {
        let store = InterruptionPolicyStore()
        let policy = InterruptionPolicy(rules: [Self.notifications], governs: true)
        store.recordMonitorPress(Self.notifications, alert: ["Allow"], synthesisId: "n0/1")
        // Replaces the records but keeps the memo, whose entry index now points into nothing.
        store.setPolicy(policy, scenarioStart: false)
        store.record("Not Now", synthesisIds: ["n0/2"])
        store.recordMonitorPress(Self.notifications, alert: ["Allow"], synthesisId: "n0/3")
        XCTAssertEqual(store.drain().tappedSynthesisIds, [["n0/2"]])

        // A scenario-start push clears the memo, so the showing is a new answer with its own entry.
        store.recordMonitorPress(Self.notifications, alert: ["Allow"], synthesisId: "n0/4")
        store.setPolicy(policy, scenarioStart: true)
        store.recordMonitorPress(Self.notifications, alert: ["Allow"], synthesisId: "n0/5")
        let drained = store.drain()
        XCTAssertEqual(drained.tapped, ["Don’t Allow"])
        XCTAssertEqual(drained.tappedSynthesisIds, [["n0/5"]])
    }

    func testTheIdArraysStayParallelToTheEntriesTheyDescribe() {
        let store = InterruptionPolicyStore()
        store.record("Not Now")
        store.recordMonitorPress(Self.notifications, alert: ["Allow"], synthesisId: "n0/4")
        store.recordBanner("Reminder", synthesisIds: ["n0/5"])
        store.recordBanner("Went by itself")
        let drained = store.drain()
        XCTAssertEqual(drained.tappedSynthesisIds, [[], ["n0/4"]])
        XCTAssertEqual(drained.banners, ["Reminder", "Went by itself"])
        XCTAssertEqual(drained.bannerSynthesisIds, [["n0/5"], []])
    }

    // MARK: - Replies

    private func json<T: Encodable>(_ value: T) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(value)) as? [String: Any])
    }

    private func reply(_ out: Operations.tap.Output) throws -> Components.Schemas.ActuationReply {
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            XCTFail("unexpected /tap output")
            throw CancellationError()
        }
        return payload
    }

    private func reply(_ out: Operations.swipe.Output) throws -> Components.Schemas.ActuationReply {
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            XCTFail("unexpected /swipe output")
            throw CancellationError()
        }
        return payload
    }

    private func reply(_ out: Operations.scroll.Output) throws -> Components.Schemas.ActuationReply {
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            XCTFail("unexpected /scroll output")
            throw CancellationError()
        }
        return payload
    }

    private func reply(_ out: Operations.setPickerValue.Output) throws -> Components.Schemas.ActuationReply {
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            XCTFail("unexpected /setPickerValue output")
            throw CancellationError()
        }
        return payload
    }

    private func reply(_ out: Operations.gesture.Output) throws -> Components.Schemas.ActuationReply {
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            XCTFail("unexpected /gesture output")
            throw CancellationError()
        }
        return payload
    }

    private func reply(_ out: Operations.tapSystemAlert.Output) throws -> Components.Schemas.ActuationReply {
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            XCTFail("unexpected /tapSystemAlert output")
            throw CancellationError()
        }
        return payload
    }

    private func reply(_ out: Operations.typeText.Output) throws -> Components.Schemas.ActuationReply {
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            XCTFail("unexpected /typeText output")
            throw CancellationError()
        }
        return payload
    }

    private func handler(_ provider: FakeElementProvider) -> APIHandler {
        provider.ledger = ledger
        return APIHandler(provider: provider, policyStore: InterruptionPolicyStore(), ledger: ledger)
    }

    private func handle(_ handler: APIHandler) async throws -> String {
        let out = try await handler.queryElements(.init())
        guard case .ok(let ok) = out, case .json(let payload) = ok.body,
              let handle = payload.elements.first?.handle
        else {
            throw XCTSkip("no handle minted")
        }
        return handle
    }

    func testEveryTouchActuationNamesTheWindowItsTouchWasMarkedIn() async throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        let handler = handler(provider)
        let handle = try await handle(handler)
        let drag = Components.Schemas.DragRequest(from: [1, 2], to: [3, 4])

        var ids: [String?] = []
        ids.append(try reply(await handler.tap(.init(body: .json(.init(handle: handle))))).synthesisId)
        ids.append(try reply(await handler.tap(.init(body: .json(.init(point: [5, 6]))))).synthesisId)
        ids.append(try reply(await handler.swipe(.init(body: .json(drag)))).synthesisId)
        ids.append(try reply(await handler.scroll(.init(body: .json(drag)))).synthesisId)
        ids.append(try reply(await handler.setPickerValue(
            .init(body: .json(.init(handle: handle, value: "May")))
        )).synthesisId)
        XCTAssertEqual(ids, ["n0/1", "n0/2", "n0/3", "n0/4", "n0/5"])
    }

    func testARetriedGestureReportsItsLastAttempt() async throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        provider.gestureAttempts = 3
        let handler = handler(provider)
        let handle = try await handle(handler)
        let out = try await handler.gesture(
            .init(body: .json(.init(handle: handle, kind: .pinch, scale: 2)))
        )
        XCTAssertEqual(try reply(out).synthesisId, "n0/3")
    }

    func testASystemAlertTapNamesItsWindow() async throws {
        let provider = FakeElementProvider()
        provider.systemAlertButtons = [Self.sample]
        let handler = handler(provider)
        let query = try await handler.querySystemAlert(.init())
        guard case .ok(let ok) = query, case .json(let payload) = ok.body,
              let handle = payload.elements.first?.handle
        else {
            throw XCTSkip("no alert handle minted")
        }
        let out = try await handler.tapSystemAlert(.init(body: .json(.init(handle: handle))))
        XCTAssertEqual(try reply(out).synthesisId, "n0/1")
    }

    func testAnActuationThatSynthesizedNoTouchCarriesNoId() async throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        let handler = handler(provider)
        let handle = try await handle(handler)

        // Refused before any touch: an unreachable element, and a stale handle.
        provider.tapResult = .notHittable
        let refused = try json(try reply(await handler.tap(.init(body: .json(.init(handle: handle))))))
        XCTAssertNil(refused["synthesisId"])
        let stale = try reply(await handler.tap(.init(body: .json(.init(handle: "gone")))))
        XCTAssertNil(stale.synthesisId)

        // Typing goes through key events, not the touch digitizer.
        provider.tapResult = .ok
        let typed = try reply(await handler.typeText(.init(body: .json(.init(text: "hi")))))
        XCTAssertNil(typed.synthesisId)

        // A raised interaction reports the stale fallback, never a completed touch, and closes the
        // window the raise cut short.
        provider.tapRaises = NSException(name: .genericException, reason: "No matches found")
        let raised = try reply(await handler.tap(.init(body: .json(.init(handle: handle)))))
        XCTAssertEqual(raised.status, .stale)
        XCTAssertNil(raised.synthesisId)
        XCTAssertEqual(sink.lines.suffix(2), ["bajutsu.synthesis begin n0/1", "bajutsu.synthesis abort n0/1"])
    }

    func testDoubleTapAndLongPressNameTheirWindow() async throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        let handler = handler(provider)
        let handle = try await handle(handler)
        let double = try reply(await handler.tap(.init(body: .json(.init(handle: handle, taps: 2)))))
        let press = try reply(await handler.tap(.init(body: .json(.init(handle: handle, duration: 1)))))
        XCTAssertEqual([double.synthesisId, press.synthesisId], ["n0/1", "n0/2"])
    }

    func testAMonitorTouchDuringATapNeverReplacesTheTapsOwnId() async throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        let ledger = ledger!
        provider.afterTap = { ledger.mark(actuation: false) {} }
        let handler = handler(provider)
        let handle = try await handle(handler)
        let tapped = try reply(await handler.tap(.init(body: .json(.init(handle: handle)))))
        XCTAssertEqual(tapped.synthesisId, "n0/1")
    }

    func testATapFoldsTheRecordIdsBesideItsOwn() async throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [Self.sample]
        provider.ledger = ledger
        let store = InterruptionPolicyStore()
        let handler = APIHandler(provider: provider, policyStore: store, ledger: ledger)
        let handle = try await handle(handler)
        store.recordMonitorPress(Self.notifications, alert: ["Allow"], synthesisId: "m/7")
        store.recordBanner("Reminder", synthesisIds: ["m/8"])
        let tapped = try reply(await handler.tap(.init(body: .json(.init(handle: handle)))))
        XCTAssertEqual(tapped.synthesisId, "n0/1")
        XCTAssertEqual(tapped.tappedSynthesisIds, [["m/7"]])
        XCTAssertEqual(tapped.bannerSynthesisIds, [["m/8"]])
    }

    func testAnInTreeResolvingPressIsReportedOnTheElementsReply() async throws {
        let rule = InterruptionRule(identify: ["Save Password", "Not Now"], tap: "Not Now", surface: .inTree)
        let provider = FakeElementProvider()
        provider.elementsToReturn = ["Save Password", "Not Now"].map { label in
            ElementSnapshot(
                identifier: nil, label: label, value: nil, traits: ["button"],
                frame: (0, 0, 10, 10), backingElement: NSObject()
            )
        }
        provider.afterTap = { [provider] in provider.elementsToReturn = [] }
        provider.ledger = ledger
        let store = InterruptionPolicyStore()
        store.setPolicy(InterruptionPolicy(rules: [rule], governs: true), scenarioStart: true)
        let handler = APIHandler(provider: provider, policyStore: store, ledger: ledger)

        let out = try await handler.queryElements(.init(query: .init(resolveAlerts: ._true)))
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            return XCTFail("unexpected /elements output")
        }
        XCTAssertEqual(payload.labels, ["Not Now"])
        XCTAssertEqual(payload.tappedSynthesisIds, [["n0/1"]])
        XCTAssertEqual(payload.bannerSynthesisIds, [])
    }

    func testAResolvingReadsPressIsReportedAgainstItsAnswerNotTheRequest() async throws {
        let provider = FakeElementProvider()
        provider.systemAlertButtons = ["Allow", "Don’t Allow"].map { label in
            ElementSnapshot(
                identifier: nil, label: label, value: nil, traits: ["button"],
                frame: (0, 0, 10, 10), backingElement: NSObject()
            )
        }
        provider.ledger = ledger
        let store = InterruptionPolicyStore()
        store.setPolicy(InterruptionPolicy(rules: [Self.notifications], governs: true), scenarioStart: true)
        let handler = APIHandler(provider: provider, policyStore: store, ledger: ledger)

        let out = try await handler.resolveSystemAlert(
            .init(body: .json(.init(labels: ["Allow", "Don’t Allow"])))
        )
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            return XCTFail("unexpected /systemAlert/resolve output")
        }
        XCTAssertEqual(payload.labels, ["Don’t Allow"])
        XCTAssertEqual(payload.tappedSynthesisIds, [["n0/1"]])
    }

    func testTheDrainEndpointCarriesTheIdArrays() async throws {
        let store = InterruptionPolicyStore()
        store.recordBanner("Reminder", synthesisIds: ["n0/9"])
        let handler = APIHandler(provider: FakeElementProvider(), policyStore: store, ledger: ledger)
        let out = try await handler.drainInterruptions(.init())
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            return XCTFail("unexpected drain output")
        }
        XCTAssertEqual(payload.bannerSynthesisIds, [["n0/9"]])
        XCTAssertEqual(payload.tappedSynthesisIds, [])
    }
}
