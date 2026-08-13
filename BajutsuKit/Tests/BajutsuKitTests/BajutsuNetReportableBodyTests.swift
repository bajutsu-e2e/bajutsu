import Foundation
import XCTest

@testable import BajutsuKit

/// Unit tests for the bound on what a network report carries.
///
/// The bound is not a tidiness rule. Unbounded, stringifying a captured body allocated a second copy
/// of it on every exchange, and an app under automation grew ~1.6 GB/s — 18.6 GB inside one
/// 11-second scenario on a Simulator, measured with `vmmap` (a few dozen 128 MB `MALLOC_LARGE`
/// blocks) and attributed to this exact conversion by `malloc_history`. On a 7 GiB CI host that
/// exhausts the machine, and the Simulator's render service fails long before any test does. Pure
/// Foundation, so it runs in `swift test` with no Simulator, like the rest of these tests.
final class BajutsuNetReportableBodyTests: XCTestCase {
    private func body(_ count: Int, byte: UInt8 = UInt8(ascii: "a")) -> Data {
        Data(repeating: byte, count: count)
    }

    func testASmallTextBodyIsReportedWholeAndUnmarked() {
        let reported = BajutsuNet.reportableBody(Data("hello".utf8))
        XCTAssertEqual(reported?.text, "hello")
        // Zero means "nothing was cut" — the caller omits the byte-count key entirely, so an
        // untruncated report looks exactly as it did before the bound existed.
        XCTAssertEqual(reported?.fullBytes, 0)
    }

    func testAnAbsentOrEmptyBodyIsOmitted() {
        XCTAssertNil(BajutsuNet.reportableBody(nil))
        XCTAssertNil(BajutsuNet.reportableBody(Data()))
    }

    func testABodyAtTheCapIsStillReportedWhole() {
        let exact = body(BajutsuNet.maximumReportedBodyBytes)
        let reported = BajutsuNet.reportableBody(exact)
        XCTAssertEqual(reported?.text.count, BajutsuNet.maximumReportedBodyBytes)
        XCTAssertEqual(reported?.fullBytes, 0, "a body that fits must not be marked truncated")
    }

    func testAnOversizedBodyIsCutToTheCapAndReportsItsFullSize() {
        // The regression this file exists for: one byte over the cap must cost the cap, not the body.
        let huge = body(BajutsuNet.maximumReportedBodyBytes * 4 + 1)
        let reported = BajutsuNet.reportableBody(huge)
        XCTAssertEqual(reported?.text.utf8.count, BajutsuNet.maximumReportedBodyBytes)
        XCTAssertEqual(reported?.fullBytes, huge.count, "the reader must be able to see what was cut")
    }

    func testACutInsideAMultiByteSequenceStillReportsTheTextBeforeIt() {
        // A naive prefix can land mid-character, which strict UTF-8 refuses to decode — dropping a
        // body that is perfectly good text right up to the cut. Built so the cap falls inside the
        // final 3-byte character.
        var data = body(BajutsuNet.maximumReportedBodyBytes - 1)
        data.append(Data("あ".utf8))  // 3 bytes, straddling the cap
        data.append(body(1024))
        let reported = BajutsuNet.reportableBody(data)
        XCTAssertNotNil(reported, "a boundary-straddling cut must not discard the whole body")
        XCTAssertEqual(reported?.fullBytes, data.count)
        XCTAssertLessThanOrEqual(reported?.text.utf8.count ?? .max, BajutsuNet.maximumReportedBodyBytes)
    }

    func testANonTextBodyIsOmittedRatherThanMangled() {
        // Before the bound, a binary body failed `String(data:encoding:)` and the key was omitted.
        // That stays true: a truncated body is evidence, a garbled one is noise.
        var binary = Data([0xFF, 0xFE, 0xFD])
        binary.append(body(BajutsuNet.maximumReportedBodyBytes * 2, byte: 0xFF))
        XCTAssertNil(BajutsuNet.reportableBody(binary))
    }
}

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
