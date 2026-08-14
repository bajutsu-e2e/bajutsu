import Foundation
import XCTest

@testable import BajutsuKit

/// Unit tests for the forwarded collector URL and the loopback guard that depends on it.
///
/// `xcodebuild` path-normalizes `.xctestrun` environment values, so the `http://` bajutsu injects
/// reaches the app as `http:/` — measured, 22 characters in Python and 21 in the runner's own
/// environment. That one character parsed to a nil host, disarmed the loopback guard, and turned
/// every collector report into an intercepted request that was reported again: ~1,200 exchanges a
/// second, ~1.6 GB/s of growth in the app under test.
final class BajutsuCollectorURLTests: XCTestCase {
    func testAForwardedURLStrippedOfItsAuthorityIsRepaired() {
        XCTAssertEqual(
            BajutsuNet.repairedURL("http:/127.0.0.1:51168"), "http://127.0.0.1:51168")
        XCTAssertEqual(
            BajutsuNet.repairedURL("https:/127.0.0.1:8443"), "https://127.0.0.1:8443")
    }

    func testAWellFormedURLIsLeftAlone() {
        for raw in ["http://127.0.0.1:51168", "https://example.com/x", "http://localhost:1/a//b"] {
            XCTAssertEqual(BajutsuNet.repairedURL(raw), raw)
        }
    }

    func testARepairedURLParsesWithTheHostTheGuardNeeds() {
        let url = URL(string: BajutsuNet.repairedURL("http:/127.0.0.1:51168"))
        XCTAssertEqual(url?.host, "127.0.0.1")
        XCTAssertEqual(url?.port, 51168)
    }

    func testTheLoopbackGuardHoldsEvenForAnUnrepairedURL() {
        // Defence in depth: the repair above is the fix, this is the backstop. A hostless loopback
        // URL must still be refused, or a future forwarding quirk re-opens the same loop.
        for raw in ["http:/127.0.0.1:51168", "http://127.0.0.1:51168", "http:/localhost:9/report"] {
            let request = URLRequest(url: URL(string: raw)!)
            XCTAssertFalse(
                BajutsuURLProtocol.canInit(with: request), "must not intercept \(raw)")
        }
    }

    func testAnOrdinaryRequestIsStillIntercepted() {
        let request = URLRequest(url: URL(string: "https://example.com/api")!)
        XCTAssertTrue(BajutsuURLProtocol.canInit(with: request))
    }
}
