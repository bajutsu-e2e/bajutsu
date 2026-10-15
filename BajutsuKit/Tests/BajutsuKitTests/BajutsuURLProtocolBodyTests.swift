import Foundation
import XCTest

@testable import BajutsuKit

/// The forwarded request must carry the app's body unchanged: an intercepted POST whose body went
/// missing reached the server empty and failed.
final class BajutsuURLProtocolBodyTests: XCTestCase {
    private let payload = Data(#"{"user":"alice","token":"t"}"#.utf8)

    private func post() -> NSMutableURLRequest {
        let req = NSMutableURLRequest(url: URL(string: "https://example.com/login")!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return req
    }

    private func read(_ stream: InputStream?) -> Data? {
        guard let stream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buf = [UInt8](repeating: 0, count: 7)
        while true {
            let n = stream.read(&buf, maxLength: buf.count)
            if n <= 0 { break }
            data.append(buf, count: n)
        }
        return data
    }

    func testStreamBodyIsForwardedAndCaptured() throws {
        let req = post()
        req.httpBodyStream = InputStream(data: payload)
        let (forwarded, captured) = try XCTUnwrap(BajutsuURLProtocol.forwardedRequest(for: req as URLRequest))
        XCTAssertEqual(captured, payload)
        XCTAssertEqual(read(forwarded.httpBodyStream), payload)
        XCTAssertEqual(forwarded.httpMethod, "POST")
        XCTAssertEqual(forwarded.value(forHTTPHeaderField: "Content-Type"), "application/json")
    }

    func testDataBodyIsForwardedUntouched() throws {
        let req = post()
        req.httpBody = payload
        let (forwarded, captured) = try XCTUnwrap(BajutsuURLProtocol.forwardedRequest(for: req as URLRequest))
        XCTAssertEqual(captured, payload)
        XCTAssertEqual(forwarded.httpBody, payload)
    }

    func testBodylessRequestStaysBodyless() throws {
        let req = NSMutableURLRequest(url: URL(string: "https://example.com/users")!)
        let (forwarded, captured) = try XCTUnwrap(BajutsuURLProtocol.forwardedRequest(for: req as URLRequest))
        XCTAssertNil(captured)
        XCTAssertNil(forwarded.httpBody)
        XCTAssertNil(forwarded.httpBodyStream)
    }

    func testForwardedRequestIsMarkedHandled() throws {
        let req = post()
        req.httpBodyStream = InputStream(data: payload)
        let (forwarded, _) = try XCTUnwrap(BajutsuURLProtocol.forwardedRequest(for: req as URLRequest))
        XCTAssertFalse(BajutsuURLProtocol.canInit(with: forwarded))
    }
}
