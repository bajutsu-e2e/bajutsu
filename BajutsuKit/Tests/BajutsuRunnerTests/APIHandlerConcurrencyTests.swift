import Foundation
import XCTest
@testable import BajutsuRunner

/// The XCUITest-operation serialization guard, on the stack Unit 4 makes the production path
/// (BE-0381). `APIHandler.operations` is what carries BE-0323 now — no two XCUITest operations may
/// run, or *re-enter*, at once — and this suite is what keeps guarding it after Unit 5 deletes
/// `Router` and `RouterConcurrencyTests` together.
///
/// It asserts the queue's serialness directly rather than through a provider probe, because the
/// probe shape cannot see it. Measured on this stack (Swift 6.3.3, `swift test`): with `operations`
/// given `attributes: .concurrent`, two concurrent `/elements` over the live socket really do run
/// concurrently *on the queue* — peak 2 — while a `ReentrancyProbe`-style provider counting its own
/// overlap still reports 1, so an assertion on that counter passes against the regression. The cause
/// is the main hop, not the run loop: libdispatch's main-queue drain is not re-entrant, so a
/// `RunLoop.run(until:)` spin *inside* an executing main-queue block does not run another block
/// queued on main (measured: a marker dispatched during such a spin does not run, while the same
/// marker does run when the spin happens outside a main-queue block). The second operation's
/// `DispatchQueue.main.sync` therefore waits for the first to return whether or not anything
/// serialized it — which is also why BE-0323's abort only ever appeared under XCUITest's own run
/// loop, on the device lanes. `RouterConcurrencyTests`' counterpart assertion is vacuous for the same
/// reason: with `Router.actuationLock` removed entirely it still passes.
///
/// What that leaves pinned here is the mechanism the invariant rests on — one queue, one operation at
/// a time — and what it leaves unpinned is the queue being replaced wholesale. Unit 5 swaps the
/// listener onto Hummingbird, so it has to re-establish this assertion against whatever serializes
/// there rather than delete it with the suites that compare against `Router`.
final class APIHandlerConcurrencyTests: XCTestCase {
    func testTheOperationQueueAdmitsOneOperationAtATime() {
        let handler = APIHandler(provider: FakeElementProvider())
        let lock = NSLock()
        var current = 0
        var peak = 0
        // Dispatched straight onto the queue, with no hop to main: that is what makes an overlap
        // visible here and invisible to any probe reached through `serialized`.
        let secondEntered = DispatchSemaphore(value: 0)
        let both = XCTestExpectation(description: "both blocks ran")
        both.expectedFulfillmentCount = 2

        for index in 0..<2 {
            handler.operations.async {
                lock.lock()
                current += 1
                peak = Swift.max(peak, current)
                lock.unlock()
                if index == 0 {
                    // Bounded: a serial queue cannot let the second block in while this one holds the
                    // queue, so the wait is expected to time out. A concurrent queue signals it, and
                    // `peak` is 2 by the time this returns.
                    _ = secondEntered.wait(timeout: .now() + 0.5)
                } else {
                    secondEntered.signal()
                }
                lock.lock()
                current -= 1
                lock.unlock()
                both.fulfill()
            }
        }
        wait(for: [both], timeout: 5)

        lock.lock()
        let observed = peak
        lock.unlock()
        XCTAssertEqual(observed, 1, "every XCUITest operation must pass through a serial queue (BE-0323)")
    }
}
