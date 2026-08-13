import Foundation
import XCTest
@testable import BajutsuRunner

/// `/screenshot`'s deadline: the handler stops waiting for a slow capture, but never stops serializing.
///
/// XCUITest gives its own screenshot request roughly 90s while the Python client's read window is 15s,
/// so a slow capture used to reach the client as silence — which the channel could only classify as a
/// dead runner, spending the run's whole crash-recovery budget on a live one. The handler answers
/// first instead. What it must *not* do is release `actuationLock` early: the capture still holds the
/// main thread, and letting a second operation enqueue there re-enters XCUITest and aborts the host.
final class RouterScreenshotDeadlineTests: XCTestCase {
    /// A provider whose screenshot occupies the main thread for `spin` seconds, pumping the run loop
    /// rather than blocking it — a blocking fake would deadlock the test, which needs the same main run
    /// loop to service the Router's dispatched capture. It stands in for a slow capture's *duration*,
    /// not for XCUITest's re-entrancy: `RunLoop.run(until:)` does not drain the main dispatch queue
    /// here, so no queued operation re-enters this spin. Every field is main-thread-confined, and the
    /// assertions read them only once the capture has returned.
    private final class SpinningScreenshotProvider: ElementProviding {
        let spin: TimeInterval
        private(set) var screenshotReturnedAt: TimeInterval?
        private(set) var queryRanAt: TimeInterval?
        private(set) var maxConcurrent = 0
        private var current = 0

        init(spin: TimeInterval) { self.spin = spin }

        func screenshot() -> Data? {
            enter()
            RunLoop.current.run(until: Date(timeIntervalSinceNow: spin))
            leave()
            screenshotReturnedAt = ProcessInfo.processInfo.systemUptime
            return Data([0x89, 0x50, 0x4E, 0x47])
        }

        func queryElements() -> [ElementSnapshot] {
            enter()
            leave()
            queryRanAt = ProcessInfo.processInfo.systemUptime
            return []
        }

        private func enter() {
            current += 1
            maxConcurrent = Swift.max(maxConcurrent, current)
        }

        private func leave() { current -= 1 }

        func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult { .ok }
        func isHittable(backingElement: AnyObject) -> TapResult { .ok }
        func tapPoint(x: Double, y: Double) -> TapResult { .ok }
        func gesture(backingElement: AnyObject, kind: String, scale: Double, radians: Double) -> TapResult { .ok }
        func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult { .ok }
        func scroll(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult { .ok }
        func screenSize() -> (width: Double, height: Double) { (390, 844) }
        func typeText(_ text: String) -> TapResult { .ok }
        func deleteText(count: Int) -> TapResult { .ok }
        func selectAll() -> TapResult { .ok }
        func copySelection() -> TapResult { .ok }
        func querySystemAlertButtons() -> [ElementSnapshot] { [] }
        func tapSystemAlertButton(backingElement: AnyObject) -> TapResult { .ok }
    }

    private func errorMessage(_ response: HTTPResponse?) -> String? {
        guard let response else { return nil }
        let json = try? JSONSerialization.jsonObject(with: response.body) as? [String: Any]
        return json?["message"] as? String
    }

    func testAStalledCaptureIsAnsweredBeforeItReturns() {
        // 0.2s deadline against a 1.0s capture, so the reply can only come from the deadline path.
        let provider = SpinningScreenshotProvider(spin: 1.0)
        let router = Router(provider: provider, screenshotDeadline: 0.2)
        let replied = XCTestExpectation(description: "screenshot replied")
        var response: HTTPResponse?
        var repliedAt: TimeInterval?

        DispatchQueue.global().async {
            response = router.handle(HTTPRequest(method: "GET", path: "/screenshot", body: nil))
            repliedAt = ProcessInfo.processInfo.systemUptime
            replied.fulfill()
        }
        // Pumps the main run loop that services the dispatched capture; the capture's own spin keeps
        // pumping it, so this returns once the capture finishes and the reply has long since happened.
        wait(for: [replied], timeout: 5)
        XCTAssertNotNil(provider.screenshotReturnedAt, "the capture should have run to completion")

        XCTAssertEqual(response?.statusCode, 500)
        // Asserts the reason reaches the client, not its exact rendering: the Python driver surfaces
        // this message verbatim, so what matters is that it names the deadline rather than how.
        XCTAssertTrue(
            errorMessage(response)?.contains("deadline") == true,
            "the reply must say why: \(errorMessage(response) ?? "<no message>")"
        )
        // The point of the deadline: the client was answered while the capture was still running.
        XCTAssertLessThan(
            repliedAt ?? .infinity, provider.screenshotReturnedAt ?? 0,
            "the reply must precede the capture's return, not wait for it"
        )
    }

    /// The abandoned capture must hand the lock on when it finally returns.
    ///
    /// This is the half of the ownership hand-off that *is* observable in-process: drop the dispatched
    /// block's `actuationLock.signal()` and the lock is leaked, so the next operation waits forever and
    /// this test times out. The other half — that no second operation re-enters XCUITest mid-capture —
    /// is not observable here, because `RunLoop.run(until:)` does not drain the main dispatch queue in
    /// this harness, so the capture's hold on the main thread already serializes everything with or
    /// without the lock. Only the real XCUITest run loop drains it, so that half rests on the on-device
    /// lanes and on the reasoning in `captureScreenshotWithinDeadline`, not on this assertion.
    func testAnAbandonedCaptureStillHandsOnTheLock() {
        let provider = SpinningScreenshotProvider(spin: 1.0)
        let router = Router(provider: provider, screenshotDeadline: 0.2)
        let replied = XCTestExpectation(description: "screenshot replied")
        let readReturned = XCTestExpectation(description: "read returned")

        DispatchQueue.global().async {
            _ = router.handle(HTTPRequest(method: "GET", path: "/screenshot", body: nil))
            replied.fulfill()
            // Issued after the deadline reply, while the capture still owes the lock.
            _ = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
            readReturned.fulfill()
        }
        wait(for: [replied, readReturned], timeout: 5)

        XCTAssertNotNil(provider.queryRanAt, "the read must reach the provider once the lock is handed on")
        XCTAssertEqual(provider.maxConcurrent, 1, "XCUITest operations must never run or re-enter concurrently")
    }

    /// A second stalled screenshot must be bounded by the *lock* wait, not just the capture wait.
    ///
    /// The reviewer's case, and the normal one rather than an exotic one: a green `visual` job logs
    /// ~10 screenshot stalls, so the second arrives while the first abandoned capture still owns the
    /// lock for as long as XCUITest gives its own request (~90s). Bounding only the capture would leave
    /// this request blocked past the client's window, reaching it as exactly the silence the deadline
    /// exists to remove.
    func testASecondScreenshotIsBoundedByTheLockWaitToo() {
        let provider = SpinningScreenshotProvider(spin: 1.0)
        let router = Router(provider: provider, screenshotDeadline: 0.2)
        let first = XCTestExpectation(description: "first screenshot replied")
        let second = XCTestExpectation(description: "second screenshot replied")
        var secondResponse: HTTPResponse?
        var secondTook: TimeInterval = .infinity

        DispatchQueue.global().async {
            _ = router.handle(HTTPRequest(method: "GET", path: "/screenshot", body: nil))
            first.fulfill()
            // The first capture still holds the lock here and will for ~0.8s more.
            let start = ProcessInfo.processInfo.systemUptime
            secondResponse = router.handle(HTTPRequest(method: "GET", path: "/screenshot", body: nil))
            secondTook = ProcessInfo.processInfo.systemUptime - start
            second.fulfill()
        }
        wait(for: [first, second], timeout: 5)

        XCTAssertEqual(secondResponse?.statusCode, 500)
        // Bounded by its own deadline, not by the capture that holds the lock.
        // Bounded: ~0.2s (its own deadline). Unbounded: ~0.8s (waiting out the first capture). 0.5s
        // sits well clear of both, rather than a hair under the unbounded figure.
        XCTAssertLessThan(secondTook, 0.5, "the lock wait must be bounded by the deadline")
        XCTAssertTrue(
            errorMessage(secondResponse)?.contains("another operation still held the runner") == true,
            "the reply must name the lock wait: \(errorMessage(secondResponse) ?? "<none>")"
        )
    }

    func testAStalledReplyCarriesTheStalledStatusNotAPlainError() {
        // The Python client keys on this status to re-issue the read rather than fail the step, so the
        // distinction from a plain `error` is load-bearing, not cosmetic.
        let provider = SpinningScreenshotProvider(spin: 1.0)
        let router = Router(provider: provider, screenshotDeadline: 0.2)
        let replied = XCTestExpectation(description: "screenshot replied")
        var response: HTTPResponse?
        DispatchQueue.global().async {
            response = router.handle(HTTPRequest(method: "GET", path: "/screenshot", body: nil))
            replied.fulfill()
        }
        wait(for: [replied], timeout: 5)

        let json = try? JSONSerialization.jsonObject(with: response!.body) as? [String: Any]
        XCTAssertEqual(json?["status"] as? String, "stalled")
    }

    func testACaptureInsideTheDeadlineStillReturnsThePng() {
        let provider = SpinningScreenshotProvider(spin: 0)
        let router = Router(provider: provider, screenshotDeadline: 5)
        let replied = XCTestExpectation(description: "screenshot replied")
        var response: HTTPResponse?

        DispatchQueue.global().async {
            response = router.handle(HTTPRequest(method: "GET", path: "/screenshot", body: nil))
            replied.fulfill()
        }
        wait(for: [replied], timeout: 5)

        XCTAssertEqual(response?.statusCode, 200)
        XCTAssertEqual(response?.contentType, "image/png")
        XCTAssertEqual(response?.body, Data([0x89, 0x50, 0x4E, 0x47]))
    }
}
