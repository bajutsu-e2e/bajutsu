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

    func testElementsReportsEmptyWhenTheSnapshotRaisesAnException() {
        // The read-path guard: an `app.snapshot()` that raises while the UI is in flux must not abort
        // the resident runner. The Router catches it and answers an empty screen (the run then fails
        // loudly downstream when nothing resolves), never a crash → connection refused.
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "home.title", label: "Home", value: nil,
                traits: ["staticText"], frame: (0, 0, 10, 10), backingElement: NSObject()
            ),
        ]
        provider.queryRaises = NSException(
            name: .internalInconsistencyException, reason: "snapshot failed while UI in flux", userInfo: nil
        )
        let router = makeRouter(provider)

        let response = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
        let json = parseJSON(response)
        XCTAssertEqual(json?["status"] as? String, "ok")
        XCTAssertEqual((json?["elements"] as? [[String: Any]])?.count, 0)  // empty screen, not a crash
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

    func testTapReportsStaleWhenTheInteractionRaisesAnException() throws {
        // The runner-survival guard: XCUITest raises an NSException when an element vanishes mid-tap
        // ("No matches found"). Uncaught it would abort the resident runner; the Router must catch it
        // and answer "stale" (so the Python side re-resolves and retries) rather than let it escape.
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "ok", label: nil, value: nil,
                traits: ["button"], frame: (0, 0, 10, 10), backingElement: NSObject()
            ),
        ]
        provider.tapRaises = NSException(
            name: .internalInconsistencyException,
            reason: "No matches found for Element at index 2", userInfo: nil
        )
        let router = makeRouter(provider)

        let elemResponse = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
        let handle = (parseJSON(elemResponse)?["elements"] as? [[String: Any]])?.first?["handle"] as? String
        let tapBody = try JSONSerialization.data(withJSONObject: ["handle": handle!])

        let tapResponse = router.handle(HTTPRequest(method: "POST", path: "/tap", body: tapBody))
        XCTAssertEqual(parseJSON(tapResponse)?["status"] as? String, "stale")
        XCTAssertEqual(provider.tapCalls.count, 1)  // the interaction was attempted, then caught
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

    // MARK: - Helpers

    private func extractHandle(from router: Router) throws -> String {
        let response = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
        let elements = try XCTUnwrap(parseJSON(response)?["elements"] as? [[String: Any]])
        return try XCTUnwrap(elements.first?["handle"] as? String)
    }

    // MARK: - /gesture

    func testGesturePinchSendsScaleToProvider() throws {
        let provider = FakeElementProvider()
        let backing = NSObject()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "photo", label: "Photo", value: nil,
                traits: ["image"], frame: (0, 0, 200, 200), backingElement: backing
            ),
        ]
        let router = makeRouter(provider)
        let handle = try extractHandle(from: router)

        let body = try JSONSerialization.data(
            withJSONObject: ["handle": handle, "kind": "pinch", "scale": 2.0]
        )
        let response = router.handle(HTTPRequest(method: "POST", path: "/gesture", body: body))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "ok")
        XCTAssertEqual(provider.gestureCalls.count, 1)
        XCTAssertTrue(provider.gestureCalls[0].backingElement === backing)
        XCTAssertEqual(provider.gestureCalls[0].kind, "pinch")
        XCTAssertEqual(provider.gestureCalls[0].scale, 2.0)
    }

    func testGestureRotateSendsRadiansToProvider() throws {
        let provider = FakeElementProvider()
        let backing = NSObject()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "map", label: "Map", value: nil,
                traits: ["image"], frame: (0, 0, 300, 300), backingElement: backing
            ),
        ]
        let router = makeRouter(provider)
        let handle = try extractHandle(from: router)

        let body = try JSONSerialization.data(
            withJSONObject: ["handle": handle, "kind": "rotate", "radians": 1.57]
        )
        let response = router.handle(HTTPRequest(method: "POST", path: "/gesture", body: body))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "ok")
        XCTAssertEqual(provider.gestureCalls.count, 1)
        XCTAssertEqual(provider.gestureCalls[0].kind, "rotate")
        XCTAssertEqual(provider.gestureCalls[0].radians, 1.57)
    }

    func testGestureStaleHandleReturnsStale() throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "a", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        let router = makeRouter(provider)
        let handle = try extractHandle(from: router)

        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "b", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        _ = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))

        let body = try JSONSerialization.data(
            withJSONObject: ["handle": handle, "kind": "pinch", "scale": 1.0]
        )
        let response = router.handle(HTTPRequest(method: "POST", path: "/gesture", body: body))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "stale")
        XCTAssertEqual(provider.gestureCalls.count, 0)
    }

    func testGestureMissingHandleReturns400() throws {
        let body = try JSONSerialization.data(withJSONObject: ["kind": "pinch", "scale": 1.0])
        let response = makeRouter().handle(HTTPRequest(method: "POST", path: "/gesture", body: body))
        XCTAssertEqual(response.statusCode, 400)
    }

    func testGestureMissingKindReturns400() throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "a", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        let router = makeRouter(provider)
        let handle = try extractHandle(from: router)

        let body = try JSONSerialization.data(withJSONObject: ["handle": handle, "scale": 1.0])
        let response = router.handle(HTTPRequest(method: "POST", path: "/gesture", body: body))
        XCTAssertEqual(response.statusCode, 400)
    }

    func testGestureUnknownKindReturns400() throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "a", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        let router = makeRouter(provider)
        let handle = try extractHandle(from: router)

        let body = try JSONSerialization.data(
            withJSONObject: ["handle": handle, "kind": "flick", "scale": 1.0]
        )
        let response = router.handle(HTTPRequest(method: "POST", path: "/gesture", body: body))
        XCTAssertEqual(response.statusCode, 400)
    }

    // MARK: - /swipe

    func testSwipeSendsPointsToProvider() throws {
        let provider = FakeElementProvider()
        let router = makeRouter(provider)

        let body = try JSONSerialization.data(
            withJSONObject: ["from": [10.0, 20.0], "to": [100.0, 200.0]]
        )
        let response = router.handle(HTTPRequest(method: "POST", path: "/swipe", body: body))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "ok")
        XCTAssertEqual(provider.swipeCalls.count, 1)
        XCTAssertEqual(provider.swipeCalls[0].fromX, 10.0)
        XCTAssertEqual(provider.swipeCalls[0].fromY, 20.0)
        XCTAssertEqual(provider.swipeCalls[0].toX, 100.0)
        XCTAssertEqual(provider.swipeCalls[0].toY, 200.0)
    }

    func testSwipeMissingFieldsReturns400() throws {
        let body = try JSONSerialization.data(withJSONObject: ["from": [10.0, 20.0]])
        let response = makeRouter().handle(HTTPRequest(method: "POST", path: "/swipe", body: body))
        XCTAssertEqual(response.statusCode, 400)
    }

    // MARK: - /type

    func testTypeSendsTextToProvider() throws {
        let provider = FakeElementProvider()
        let router = makeRouter(provider)

        let body = try JSONSerialization.data(withJSONObject: ["text": "hello"])
        let response = router.handle(HTTPRequest(method: "POST", path: "/type", body: body))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "ok")
        XCTAssertEqual(provider.typeCalls, ["hello"])
    }

    func testTypeMissingTextReturns400() throws {
        let body = try JSONSerialization.data(withJSONObject: ["value": "oops"])
        let response = makeRouter().handle(HTTPRequest(method: "POST", path: "/type", body: body))
        XCTAssertEqual(response.statusCode, 400)
    }

    // MARK: - /deleteText, /selectAll, /copy (BE-0265)

    func testDeleteTextSendsCountToProvider() throws {
        let provider = FakeElementProvider()
        let router = makeRouter(provider)

        let body = try JSONSerialization.data(withJSONObject: ["count": 3])
        let response = router.handle(HTTPRequest(method: "POST", path: "/deleteText", body: body))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "ok")
        XCTAssertEqual(provider.deleteTextCalls, [3])
    }

    func testDeleteTextNonPositiveCountReturns400() throws {
        let body = try JSONSerialization.data(withJSONObject: ["count": 0])
        let response = makeRouter().handle(HTTPRequest(method: "POST", path: "/deleteText", body: body))
        XCTAssertEqual(response.statusCode, 400)
    }

    func testSelectAllInvokesProvider() {
        let provider = FakeElementProvider()
        let router = makeRouter(provider)

        let response = router.handle(HTTPRequest(method: "POST", path: "/selectAll", body: nil))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "ok")
        XCTAssertEqual(provider.selectAllCalls, 1)
    }

    func testCopyInvokesProvider() {
        let provider = FakeElementProvider()
        let router = makeRouter(provider)

        let response = router.handle(HTTPRequest(method: "POST", path: "/copy", body: nil))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "ok")
        XCTAssertEqual(provider.copyCalls, 1)
    }

    // MARK: - /systemAlert (BE-0316)

    func testSystemAlertQueryReturnsButtonsWithHandles() {
        let provider = FakeElementProvider()
        provider.systemAlertButtons = [
            ElementSnapshot(
                identifier: nil, label: "Allow", value: nil,
                traits: ["button"], frame: (0, 0, 100, 44), backingElement: NSObject()
            ),
            ElementSnapshot(
                identifier: nil, label: "Don't Allow", value: nil,
                traits: ["button"], frame: (0, 44, 100, 44), backingElement: NSObject()
            ),
        ]
        let router = makeRouter(provider)
        let response = router.handle(HTTPRequest(method: "POST", path: "/systemAlert/query", body: nil))
        let json = parseJSON(response)
        XCTAssertEqual(json?["status"] as? String, "ok")
        let elements = json?["elements"] as? [[String: Any]]
        XCTAssertEqual(elements?.count, 2)
        XCTAssertEqual(elements?.first?["label"] as? String, "Allow")
        XCTAssertNotNil(elements?.first?["handle"] as? String)
    }

    func testSystemAlertQueryEmptyWhenNoAlert() {
        let provider = FakeElementProvider()  // no buttons seeded → no alert up
        let router = makeRouter(provider)
        let response = router.handle(HTTPRequest(method: "POST", path: "/systemAlert/query", body: nil))
        XCTAssertEqual((parseJSON(response)?["elements"] as? [[String: Any]])?.count, 0)
    }

    func testSystemAlertTapWithValidHandleCallsProvider() throws {
        let provider = FakeElementProvider()
        let backing = NSObject()
        provider.systemAlertButtons = [
            ElementSnapshot(
                identifier: nil, label: "Allow", value: nil,
                traits: ["button"], frame: (0, 0, 100, 44), backingElement: backing
            ),
        ]
        let router = makeRouter(provider)

        let queryResponse = router.handle(
            HTTPRequest(method: "POST", path: "/systemAlert/query", body: nil)
        )
        let handle = (parseJSON(queryResponse)?["elements"] as? [[String: Any]])?.first?["handle"] as? String
        XCTAssertNotNil(handle)

        let tapBody = try JSONSerialization.data(withJSONObject: ["handle": handle!])
        let tapResponse = router.handle(
            HTTPRequest(method: "POST", path: "/systemAlert/tap", body: tapBody)
        )
        XCTAssertEqual(parseJSON(tapResponse)?["status"] as? String, "ok")
        XCTAssertEqual(provider.systemAlertTapCalls.count, 1)
        XCTAssertTrue(provider.systemAlertTapCalls[0] === backing)
    }

    func testSystemAlertTapWithUnknownHandleReturnsNotFound() throws {
        let router = makeRouter()
        let tapBody = try JSONSerialization.data(withJSONObject: ["handle": "h-never-issued"])
        let response = router.handle(HTTPRequest(method: "POST", path: "/systemAlert/tap", body: tapBody))
        XCTAssertEqual(parseJSON(response)?["status"] as? String, "not-found")
    }

    func testSystemAlertTapMissingHandleReturns400() {
        let router = makeRouter()
        let response = router.handle(HTTPRequest(method: "POST", path: "/systemAlert/tap", body: nil))
        XCTAssertEqual(response.statusCode, 400)
    }

    // MARK: - unknown route

    func testUnknownRouteReturns404() {
        let response = makeRouter().handle(HTTPRequest(method: "GET", path: "/nope", body: nil))
        XCTAssertEqual(response.statusCode, 404)
    }
}
