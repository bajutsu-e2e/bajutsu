import Foundation
import XCTest
@testable import BajutsuRunner

/// The XCUITest-operation serialization guard, on the stack Unit 4 makes the production path
/// (BE-0381). `APIHandler.operations` is what carries BE-0323 now — no two XCUITest operations may
/// run, or *re-enter*, at once — and this suite is what keeps guarding it after Unit 5 deletes
/// `Router` and `RouterConcurrencyTests` together.
///
/// The regression it has to catch is an operation that stops routing through the queue, not someone
/// typing `attributes: .concurrent`. So each case holds one operation on the main thread and then
/// dispatches a probe onto the queue: while the operation is in flight the probe must not run, which
/// is true only if the operation is *occupying* the queue. An operation that called the provider
/// inline, or took its own queue, leaves the probe free to run and fails the assertion — and so does
/// making the queue concurrent, since the probe would then run alongside the held operation.
///
/// A provider-side overlap counter cannot do this job, which is why the assertion lives here rather
/// than in a live-server test. Measured on this stack: with the queue made `attributes: .concurrent`,
/// two `/elements` over the socket really do run concurrently on it — queue peak 2 — while a probe
/// counting its own overlap inside the provider still reports 1, because libdispatch does not run a
/// second main-queue block while the first is executing (a marker dispatched during a
/// `RunLoop.run(until:)` spin inside a main block does not run; the same marker outside one does).
/// That is also why BE-0323's abort only ever appeared under XCUITest's own run loop, and why
/// `RouterConcurrencyTests`' counterpart assertion passes with `Router.actuationLock` removed
/// outright.
///
/// Unit 5 swaps the listener onto Hummingbird, which is the one change these assertions are blind to:
/// they must be re-pointed at whatever serializes there rather than deleted with the `Router`
/// comparisons.
final class APIHandlerConcurrencyTests: XCTestCase {
    /// A provider that parks the operation on the main thread until the test releases it, standing in
    /// for the run-loop spin a real `app.snapshot()` or interaction performs while it waits on the app.
    private final class HoldingProvider: ElementProviding {
        let entered = DispatchSemaphore(value: 0)
        let release = DispatchSemaphore(value: 0)

        private func hold() {
            entered.signal()
            release.wait()
        }

        func queryElements() -> [ElementSnapshot] {
            hold()
            return []
        }

        func tapPoint(x: Double, y: Double) -> TapResult {
            hold()
            return .ok
        }

        func typeText(_ text: String) -> TapResult {
            hold()
            return .ok
        }

        func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult { .ok }
        func isHittable(backingElement: AnyObject) -> TapResult { .ok }
        func gesture(backingElement: AnyObject, kind: String, scale: Double, radians: Double) -> TapResult { .ok }
        func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult { .ok }
        func scroll(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult { .ok }
        func screenSize() -> (width: Double, height: Double) { (390, 844) }
        func deleteText(count: Int) -> TapResult { .ok }
        func selectAll() -> TapResult { .ok }
        func copySelection() -> TapResult { .ok }
        func setPickerValue(backingElement: AnyObject, value: String) -> TapResult { .ok }
        func querySystemAlertButtons() -> [ElementSnapshot] { [] }
        func tapSystemAlertButton(backingElement: AnyObject) -> TapResult { .ok }
        func screenshot() -> Data? { nil }
    }

    func testAReadOccupiesTheQueue() {
        assertOccupiesTheQueue("/elements") { handler in
            _ = try await handler.queryElements(.init())
        }
    }

    func testAnActuationOccupiesTheQueue() {
        assertOccupiesTheQueue("/tap") { handler in
            _ = try await handler.tap(.init(body: .json(.init(point: [12, 34]))))
        }
    }

    func testATextEditOccupiesTheQueue() {
        assertOccupiesTheQueue("/type") { handler in
            _ = try await handler.typeText(.init(body: .json(.init(text: "hello"))))
        }
    }

    // MARK: - Helper

    /// Runs *operation*, and while it is parked on the main thread checks that a block dispatched onto
    /// `operations` cannot run — then that it runs once the operation returns, so a probe that never
    /// ran for an unrelated reason cannot pass this by accident.
    private func assertOccupiesTheQueue(
        _ name: String, _ operation: @escaping @Sendable (APIHandler) async throws -> Void
    ) {
        let provider = HoldingProvider()
        let handler = APIHandler(provider: provider)
        let probeRan = DispatchSemaphore(value: 0)
        let probed = XCTestExpectation(description: "probe dispatched during \(name)")
        let returned = XCTestExpectation(description: "\(name) returned")
        // `@unchecked Sendable` box: written on the probing thread before `probed` is fulfilled, read
        // on main after `wait(for:)` returns.
        final class Observed: @unchecked Sendable { var ranWhileHeld = true }
        let observed = Observed()

        // Off the main thread throughout: the main thread's job is to spin the run loop that services
        // the operation's `DispatchQueue.main.sync`, exactly as the live-server tests do.
        DispatchQueue.global().async {
            provider.entered.wait()
            handler.operations.async { probeRan.signal() }
            // Bounded: a queue the held operation occupies cannot run this, so the wait is expected to
            // time out. A bypassed or concurrent queue runs it immediately.
            observed.ranWhileHeld = probeRan.wait(timeout: .now() + 0.3) == .success
            probed.fulfill()
            provider.release.signal()
        }
        Task(priority: .userInitiated) {
            try? await operation(handler)
            returned.fulfill()
        }
        wait(for: [probed, returned], timeout: 10)

        XCTAssertFalse(
            observed.ranWhileHeld,
            "\(name) must hold `operations` for the whole operation (BE-0323)"
        )
        // Only meaningful when the probe was still pending: a probe that already ran above consumed
        // this signal, and re-checking it would report a second, misleading failure.
        if !observed.ranWhileHeld {
            XCTAssertEqual(
                probeRan.wait(timeout: .now() + 5), .success,
                "the probe must run once \(name) releases the queue"
            )
        }
    }
}
