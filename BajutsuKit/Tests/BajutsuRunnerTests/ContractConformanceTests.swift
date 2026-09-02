import Foundation
import XCTest
@testable import BajutsuRunner

/// The OpenAPI contract must describe the runner the Python driver already talks to, not a
/// redesign of it. Each test here drives the *legacy* `Router` and decodes its real response
/// bytes into the *generated* types, so a schema that drifts from shipped behaviour fails here
/// rather than on a device. The request-side tests decode the exact JSON
/// `bajutsu/common/drivers/xcuitest.py` sends today, which is the other half of the same contract.
final class ContractConformanceTests: XCTestCase {
    private let decoder = JSONDecoder()

    private func route(
        _ method: String, _ path: String, body: [String: Any]? = nil,
        provider: FakeElementProvider = FakeElementProvider()
    ) -> HTTPResponse {
        let data = body.map { try! JSONSerialization.data(withJSONObject: $0) }
        return Router(provider: provider).handle(
            HTTPRequest(method: method, path: path, body: data)
        )
    }

    private func decode<T: Decodable>(_ type: T.Type, from response: HTTPResponse) throws -> T {
        try decoder.decode(type, from: response.body)
    }

    // MARK: - Responses: the generated types must accept what the Router emits

    func testHealthReplyDecodesFromTheRouter() throws {
        let reply = try decode(
            Components.Schemas.HealthReply.self, from: route("GET", "/health")
        )
        // The driver's readiness wait polls until this exact string comes back.
        XCTAssertEqual(reply.status, .ready)
    }

    func testScreenReplyDecodesFromTheRouter() throws {
        let provider = FakeElementProvider()
        provider.screenSizeValue = (width: 393, height: 852)
        let reply = try decode(
            Components.Schemas.ScreenReply.self,
            from: route("GET", "/screen", provider: provider)
        )
        XCTAssertEqual(reply.width, 393)
        XCTAssertEqual(reply.height, 852)
    }

    /// `/screen` carries no `status`; the driver reads a 200 as success (`_decode` in
    /// `xcuitest.py` defaults the status). Pin that, so adding a `status` to the schema — which
    /// would look harmless — is caught here.
    func testScreenReplyCarriesNoStatusField() throws {
        let json = try JSONSerialization.jsonObject(
            with: route("GET", "/screen").body
        ) as? [String: Any]
        XCTAssertEqual(json?.keys.sorted(), ["height", "width"])
    }

    func testElementsReplyDecodesFromTheRouter() throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "home.title", label: "Home", value: "v",
                traits: ["staticText"], frame: (10, 20, 100, 44), backingElement: NSObject()
            ),
        ]
        let reply = try decode(
            Components.Schemas.ElementsReply.self,
            from: route("GET", "/elements", provider: provider)
        )
        XCTAssertEqual(reply.status, .ok)
        let element = try XCTUnwrap(reply.elements.first)
        XCTAssertEqual(element.identifier, "home.title")
        XCTAssertEqual(element.label, "Home")
        XCTAssertEqual(element.value, "v")
        XCTAssertEqual(element.traits, ["staticText"])
        XCTAssertEqual(element.frame, [10, 20, 100, 44])
        XCTAssertFalse(element.handle.isEmpty)
    }

    /// The runner *omits* `identifier` / `label` / `value` when the platform reports none — a
    /// Swift dictionary drops a key assigned nil rather than emitting a null. So the schema must
    /// make them optional-and-absent, never nullable; a `required` there would fail to decode a
    /// perfectly ordinary unlabelled element.
    func testElementDecodesWhenOptionalFieldsAreAbsent() throws {
        let provider = FakeElementProvider()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: nil, label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        let response = route("GET", "/elements", provider: provider)
        let raw = try JSONSerialization.jsonObject(with: response.body) as? [String: Any]
        let rawElement = try XCTUnwrap((raw?["elements"] as? [[String: Any]])?.first)
        XCTAssertNil(rawElement["identifier"], "expected the key to be absent, not null")
        XCTAssertNil(rawElement["label"])
        XCTAssertNil(rawElement["value"])

        let reply = try decode(Components.Schemas.ElementsReply.self, from: response)
        let element = try XCTUnwrap(reply.elements.first)
        XCTAssertNil(element.identifier)
        XCTAssertNil(element.label)
        XCTAssertNil(element.value)
    }

    /// The five status strings the driver matches against its own `_OK` / `_STALE` /
    /// `_NOT_FOUND` / `_NOT_HITTABLE` / `_VALUE_NOT_FOUND` constants. Every status the driver
    /// compares as a literal is modelled as an enum — these five, plus `ready` and `/elements`' own
    /// `ok` — which is the whole point of the contract: renaming one on either side stops compiling
    /// instead of silently mismatching at run time.
    func testActuationReplyCoversEveryStatusTheRouterEmits() throws {
        let cases: [(TapResult, Components.Schemas.ActuationReply.statusPayload)] = [
            (.ok, .ok),
            (.stale, .stale),
            (.notFound, .not_hyphen_found),
            (.notHittable, .not_hyphen_hittable),
            (.valueNotFound, .value_hyphen_not_hyphen_found),
        ]
        for (result, expected) in cases {
            let provider = FakeElementProvider()
            provider.elementsToReturn = [
                ElementSnapshot(
                    identifier: "a", label: nil, value: nil,
                    traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
                ),
            ]
            provider.tapResult = result
            let router = Router(provider: provider)
            let handle = try handleFromFirstElement(router)
            let response = router.handle(
                HTTPRequest(
                    method: "POST", path: "/tap",
                    body: try JSONSerialization.data(withJSONObject: ["handle": handle])
                )
            )
            let reply = try decoder.decode(
                Components.Schemas.ActuationReply.self, from: response.body
            )
            XCTAssertEqual(reply.status, expected, "for \(result)")
        }
    }

    func testErrorReplyDecodesFromTheRouter() throws {
        // A missing body is the Router's own 400 path.
        let response = route("POST", "/type")
        XCTAssertEqual(response.statusCode, 400)
        let reply = try decode(Components.Schemas.ErrorReply.self, from: response)
        XCTAssertEqual(reply.status, .error)
        XCTAssertFalse(reply.message.isEmpty)
    }

    func testSystemAlertQueryReusesTheElementsReplyShape() throws {
        let provider = FakeElementProvider()
        provider.systemAlertButtons = [
            ElementSnapshot(
                identifier: nil, label: "Allow", value: nil,
                traits: ["button"], frame: (0, 0, 80, 40), backingElement: NSObject()
            ),
        ]
        let reply = try decode(
            Components.Schemas.ElementsReply.self,
            from: route("POST", "/systemAlert/query", body: [:], provider: provider)
        )
        XCTAssertEqual(reply.elements.first?.label, "Allow")
    }

    // MARK: - Requests: the generated types must accept what the driver sends

    /// Each literal below is the body `bajutsu/common/drivers/xcuitest.py` builds today. Decoding them
    /// into the generated request types is what proves the schema did not tighten the contract
    /// under the shipping driver.
    func testGeneratedRequestTypesDecodeTheDriversBodies() throws {
        let tap = try decoder.decode(
            Components.Schemas.TapRequest.self, from: Data(#"{"handle": "h1"}"#.utf8)
        )
        XCTAssertEqual(tap.handle, "h1")
        XCTAssertNil(tap.point)

        let doubleTap = try decoder.decode(
            Components.Schemas.TapRequest.self, from: Data(#"{"handle": "h1", "taps": 2}"#.utf8)
        )
        XCTAssertEqual(doubleTap.taps, 2)

        let longPress = try decoder.decode(
            Components.Schemas.TapRequest.self,
            from: Data(#"{"handle": "h1", "duration": 1.5}"#.utf8)
        )
        XCTAssertEqual(longPress.duration, 1.5)

        let pointTap = try decoder.decode(
            Components.Schemas.TapRequest.self, from: Data(#"{"point": [12.5, 34.0]}"#.utf8)
        )
        XCTAssertEqual(pointTap.point, [12.5, 34.0])
        XCTAssertNil(pointTap.handle)

        let hittable = try decoder.decode(
            Components.Schemas.HandleRequest.self, from: Data(#"{"handle": "h1"}"#.utf8)
        )
        XCTAssertEqual(hittable.handle, "h1")

        let pinch = try decoder.decode(
            Components.Schemas.GestureRequest.self,
            from: Data(#"{"handle": "h1", "kind": "pinch", "scale": 2.0}"#.utf8)
        )
        XCTAssertEqual(pinch.kind, .pinch)
        XCTAssertEqual(pinch.scale, 2.0)

        let rotate = try decoder.decode(
            Components.Schemas.GestureRequest.self,
            from: Data(#"{"handle": "h1", "kind": "rotate", "radians": 1.57}"#.utf8)
        )
        XCTAssertEqual(rotate.kind, .rotate)

        let drag = try decoder.decode(
            Components.Schemas.DragRequest.self,
            from: Data(#"{"from": [1.0, 2.0], "to": [3.0, 4.0]}"#.utf8)
        )
        XCTAssertEqual(drag.from, [1, 2])
        XCTAssertEqual(drag.to, [3, 4])

        let type = try decoder.decode(
            Components.Schemas.TypeRequest.self, from: Data(#"{"text": "hello"}"#.utf8)
        )
        XCTAssertEqual(type.text, "hello")

        let delete = try decoder.decode(
            Components.Schemas.DeleteTextRequest.self, from: Data(#"{"count": 3}"#.utf8)
        )
        XCTAssertEqual(delete.count, 3)

        // `/selectAll`, `/copy`, and `/systemAlert/query` are sent an empty object, not an
        // absent body, so the schema has to accept `{}`.
        XCTAssertNoThrow(
            try decoder.decode(Components.Schemas.EmptyRequest.self, from: Data("{}".utf8))
        )
    }

    // MARK: - Helpers

    private func handleFromFirstElement(_ router: Router) throws -> String {
        let response = router.handle(HTTPRequest(method: "GET", path: "/elements", body: nil))
        let reply = try decoder.decode(Components.Schemas.ElementsReply.self, from: response.body)
        return try XCTUnwrap(reply.elements.first?.handle)
    }
}
