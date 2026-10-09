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

    // MARK: - a real device's candidate collectors

    func testEveryCandidateIsSplitAndRepairedOnItsOwn() {
        let urls = BajutsuNet.candidateURLs("http:/192.0.2.7:4100, http:/[fd00::1]:4100")
        XCTAssertEqual(urls.map(\.host), ["192.0.2.7", "fd00::1"])
        XCTAssertEqual(urls.map(\.port), [4100, 4100])
    }

    func testASingleCandidateIsTakenWithoutAProbe() {
        let url = URL(string: "http://127.0.0.1:4100")!
        let chosen = BajutsuNet.reachableCollector([url], token: "t") { _, _, _ in
            XCTFail("the Simulator's one collector must not be probed")
        }
        XCTAssertEqual(chosen, url)
    }

    func testTheFirstCandidateInTheHostsOrderThatAnswersIsKept() {
        // The later candidate answers first; the host's order still decides, so the choice does
        // not depend on which probe happened to return sooner.
        let first = URL(string: "http://192.0.2.7:4100")!
        let second = URL(string: "http://198.51.100.9:4100")!
        let third = URL(string: "http://203.0.113.4:4100")!
        let chosen = BajutsuNet.reachableCollector([first, second, third], token: "t") { url, token, done in
            XCTAssertEqual(token, "t")
            if url == third {
                done(true)
            } else if url == second {
                DispatchQueue.global().asyncAfter(deadline: .now() + 0.05) { done(true) }
            } else {
                done(false)
            }
        }
        XCTAssertEqual(chosen, second)
    }

    func testNoAnswerWithinTheBoundLeavesNoCollector() {
        let urls = [URL(string: "http://192.0.2.7:4100")!, URL(string: "http://192.0.2.8:4100")!]
        let started = Date()
        let chosen = BajutsuNet.reachableCollector(urls, token: nil, timeout: 0.1) { _, _, _ in }
        XCTAssertNil(chosen)
        XCTAssertLessThan(Date().timeIntervalSince(started), 1)
    }

    func testAnUnreachableLaterCandidateDoesNotDelayTheLaunch() {
        // The first candidate answers and the second never returns (a dropped packet): the choice is
        // settled the moment the first answers, so the launch does not wait out the timeout.
        let first = URL(string: "http://192.0.2.7:4100")!
        let silent = URL(string: "http://198.51.100.9:4100")!
        let started = Date()
        let chosen = BajutsuNet.reachableCollector([first, silent], token: nil, timeout: 5) { url, _, done in
            if url == first { done(true) }
        }
        XCTAssertEqual(chosen, first)
        XCTAssertLessThan(Date().timeIntervalSince(started), 1)
    }
}
