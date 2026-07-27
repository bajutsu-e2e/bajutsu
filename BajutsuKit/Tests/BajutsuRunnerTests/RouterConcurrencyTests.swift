import Foundation
import XCTest
@testable import BajutsuRunner

/// The XCUITest-operation serialization guard: no two operations may run — or *re-enter* — at once.
///
/// `app.snapshot()` / `app.screenshot()` / an interaction pumps the main run loop while it waits on
/// the app, and that spin drains the main dispatch queue. The concurrent HTTP server can deliver a
/// second operation (a scenario `/elements` alongside an evidence `/screenshot`) whose
/// `DispatchQueue.main.sync` block the spin would then run *inside* the first — re-entrancy XCUITest
/// forbids, which aborts the XCTest host (the CI-only mid-run crash). `Router.actuationLock` serializes
/// on the connection side so the second operation never enqueues onto main while the first is in
/// flight. This exercises that off the main thread (the connection path the direct-call RouterTests
/// never reach), with a provider that spins the run loop on entry so a missing lock *would* re-enter.
final class RouterConcurrencyTests: XCTestCase {
    /// A provider whose read spins the main run loop on entry, recording the peak overlap. Run through
    /// the Router's off-main path, a second concurrent read re-enters here iff nothing serializes them.
    private final class ReentrancyProbe: ElementProviding {
        private(set) var maxConcurrent = 0
        private var current = 0  // main-thread-confined: every call runs on main via the Router
        let spin: TimeInterval

        init(spin: TimeInterval) { self.spin = spin }

        func queryElements() -> [ElementSnapshot] {
            current += 1
            maxConcurrent = Swift.max(maxConcurrent, current)
            // Pump the main run loop the way a real `app.snapshot()` does; without the lock, a second
            // handler's queued main block is drained here and re-enters this method (current == 2).
            RunLoop.current.run(until: Date(timeIntervalSinceNow: spin))
            current -= 1
            return []
        }

        func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult { .ok }
        func tapPoint(x: Double, y: Double) -> TapResult { .ok }
        func gesture(backingElement: AnyObject, kind: String, scale: Double, radians: Double) -> TapResult { .ok }
        func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult { .ok }
        func scroll(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult { .ok }
        func typeText(_ text: String) -> TapResult { .ok }
        func deleteText(count: Int) -> TapResult { .ok }
        func selectAll() -> TapResult { .ok }
        func copySelection() -> TapResult { .ok }
        func querySystemAlertButtons() -> [ElementSnapshot] { [] }
        func tapSystemAlertButton(backingElement: AnyObject) -> TapResult { .ok }
        func screenshot() -> Data? { nil }
    }

    func testConcurrentReadsAreSerializedNotReentrant() {
        let probe = ReentrancyProbe(spin: 0.2)
        let router = Router(provider: probe)
        let both = XCTestExpectation(description: "both reads returned")
        both.expectedFulfillmentCount = 2

        // Two concurrent `/elements` off the main thread — the connection-handler path. Both dispatch
        // their XCUITest work onto main; `wait(for:)` below pumps the main run loop that services them.
        for _ in 0..<2 {
            DispatchQueue.global().async {
                _ = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
                both.fulfill()
            }
        }
        wait(for: [both], timeout: 5)

        // Serialized: the second read waited on `actuationLock` and never re-entered the first's
        // run-loop spin. Without the lock this is 2 (the second block drained mid-first-snapshot).
        XCTAssertEqual(probe.maxConcurrent, 1, "XCUITest operations must never run or re-enter concurrently")
    }

    func testHealthIsAnswerableWhileAReadHoldsTheLock() {
        // The lock must not wedge the server: `/health` touches no XCUITest state, takes no lock, and
        // must answer while a read holds it — the "runner busy, not dead" signal the driver relies on.
        let probe = ReentrancyProbe(spin: 0.3)
        let router = Router(provider: probe)
        let readReturned = XCTestExpectation(description: "read returned")
        DispatchQueue.global().async {
            _ = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
            readReturned.fulfill()
        }
        // `/health` never routes through `onMain`, so it returns on the calling thread regardless of
        // the lock — a direct call answers immediately even as the read above holds it.
        let health = router.handle(HTTPRequest(method: "GET", path: "/health", body: nil))
        let json = try? JSONSerialization.jsonObject(with: health.body) as? [String: Any]
        XCTAssertEqual(json?["status"] as? String, "ready")
        wait(for: [readReturned], timeout: 5)
    }
}
