import Foundation
import XCTest
@testable import BajutsuRunner

final class RouterTests: XCTestCase {
    private func makeRouter(_ provider: FakeElementProvider = FakeElementProvider()) -> Router {
        Router(provider: provider)
    }

    private func parseJSON(_ response: HTTPResponse) -> [String: Any]? {
        try? JSONSerialization.jsonObject(with: response.body) as? [String: Any]
    }

    // MARK: - /health

    func testHealthReturnsReady() {
        let response = makeRouter().handle(HTTPRequest(method: "GET", path: "/health", body: nil))
        let json = parseJSON(response)
        XCTAssertEqual(json?["status"] as? String, "ready")
    }

    // MARK: - /elements

    func testElementsReturnsSnapshotWithHandles() {
        let provider = FakeElementProvider()
        let backing = NSObject()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "home.title", label: "Home", value: nil,
                traits: ["staticText"], frame: (10, 20, 100, 44), backingElement: backing
            ),
        ]
        let router = makeRouter(provider)
        let response = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
        let json = parseJSON(response)
        XCTAssertEqual(json?["status"] as? String, "ok")

        guard let elements = json?["elements"] as? [[String: Any]],
              let first = elements.first else {
            XCTFail("expected elements array")
            return
        }
        XCTAssertEqual(first["identifier"] as? String, "home.title")
        XCTAssertEqual(first["label"] as? String, "Home")
        XCTAssertNil(first["value"] as? String)
        XCTAssertEqual(first["traits"] as? [String], ["staticText"])
        XCTAssertEqual(first["frame"] as? [Double], [10, 20, 100, 44])
        XCTAssertNotNil(first["handle"] as? String)
    }

    // MARK: - /tap

    func testTapWithValidHandleCallsProvider() throws {
        let provider = FakeElementProvider()
        let backing = NSObject()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "ok", label: "OK", value: nil,
                traits: ["button"], frame: (0, 0, 10, 10), backingElement: backing
            ),
        ]
        let router = makeRouter(provider)

        let elemResponse = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
        let elemJSON = parseJSON(elemResponse)
        let elements = elemJSON?["elements"] as? [[String: Any]]
        let handle = elements?.first?["handle"] as? String
        XCTAssertNotNil(handle)

        let tapBody = try JSONSerialization.data(withJSONObject: ["handle": handle!])
        let tapResponse = router.handle(HTTPRequest(method: "POST", path: "/tap", body: tapBody))
        let tapJSON = parseJSON(tapResponse)
        XCTAssertEqual(tapJSON?["status"] as? String, "ok")
        XCTAssertEqual(provider.tapCalls.count, 1)
        XCTAssertTrue(provider.tapCalls[0].backingElement === backing)
        XCTAssertEqual(provider.tapCalls[0].taps, 1)
    }

    func testTapWithStaleHandleReturnsStale() throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "a", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        let router = makeRouter(provider)

        let first = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
        let handle = (parseJSON(first)?["elements"] as? [[String: Any]])?.first?["handle"] as? String
        XCTAssertNotNil(handle)

        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "b", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        _ = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))

        let tapBody = try JSONSerialization.data(withJSONObject: ["handle": handle!])
        let tapResponse = router.handle(HTTPRequest(method: "POST", path: "/tap", body: tapBody))
        XCTAssertEqual(parseJSON(tapResponse)?["status"] as? String, "stale")
        XCTAssertEqual(provider.tapCalls.count, 0)
    }

    func testTapWithDoubleTapPassesTapsCount() throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "ok", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        let router = makeRouter(provider)

        let elemResponse = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
        let handle = (parseJSON(elemResponse)?["elements"] as? [[String: Any]])?.first?["handle"] as? String

        let tapBody = try JSONSerialization.data(withJSONObject: ["handle": handle!, "taps": 2])
        _ = router.handle(HTTPRequest(method: "POST", path: "/tap", body: tapBody))
        XCTAssertEqual(provider.tapCalls.first?.taps, 2)
    }

    func testTapWithDurationPassesLongPress() throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "ok", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        let router = makeRouter(provider)

        let elemResponse = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
        let handle = (parseJSON(elemResponse)?["elements"] as? [[String: Any]])?.first?["handle"] as? String

        let tapBody = try JSONSerialization.data(withJSONObject: ["handle": handle!, "duration": 1.5])
        _ = router.handle(HTTPRequest(method: "POST", path: "/tap", body: tapBody))
        XCTAssertEqual(provider.tapCalls.first?.duration, 1.5)
    }

    func testTapWithPointCallsTapPoint() throws {
        let provider = FakeElementProvider()
        let router = makeRouter(provider)

        let tapBody = try JSONSerialization.data(withJSONObject: ["point": [100.0, 200.0]])
        let response = router.handle(HTTPRequest(method: "POST", path: "/tap", body: tapBody))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "ok")
        XCTAssertEqual(provider.tapPointCalls.count, 1)
        XCTAssertEqual(provider.tapPointCalls[0].x, 100.0)
        XCTAssertEqual(provider.tapPointCalls[0].y, 200.0)
    }

    // MARK: - /screenshot

    func testScreenshotReturnsRawPNGBytes() {
        let provider = FakeElementProvider()
        let png = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A])
        provider.screenshotData = png
        let response = makeRouter(provider).handle(
            HTTPRequest(method: "GET", path: "/screenshot", body: nil)
        )
        XCTAssertEqual(response.statusCode, 200)
        XCTAssertEqual(response.contentType, "image/png")
        XCTAssertEqual(response.body, png)
    }

    func testScreenshotFailureReturnsError() {
        let provider = FakeElementProvider()
        provider.screenshotData = nil
        let response = makeRouter(provider).handle(
            HTTPRequest(method: "GET", path: "/screenshot", body: nil)
        )
        XCTAssertEqual(response.statusCode, 500)
    }

    // MARK: - unknown route

    func testUnknownRouteReturns404() {
        let response = makeRouter().handle(HTTPRequest(method: "GET", path: "/nope", body: nil))
        XCTAssertEqual(response.statusCode, 404)
    }
}
