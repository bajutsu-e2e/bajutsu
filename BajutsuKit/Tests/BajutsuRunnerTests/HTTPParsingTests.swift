import Foundation
import XCTest
@testable import BajutsuRunner

final class HTTPParsingTests: XCTestCase {
    func testJsonResponseProducesValidJSON() throws {
        let response = HTTPResponse.json(200, ["status": "ready"])
        let parsed = try JSONSerialization.jsonObject(with: response.body) as? [String: Any]
        XCTAssertEqual(parsed?["status"] as? String, "ready")
        XCTAssertEqual(response.statusCode, 200)
        XCTAssertEqual(response.contentType, "application/json")
    }

    func testPngResponseCarriesRawBytes() {
        let png = Data([0x89, 0x50, 0x4E, 0x47])
        let response = HTTPResponse.png(png)
        XCTAssertEqual(response.body, png)
        XCTAssertEqual(response.contentType, "image/png")
    }

    func testErrorResponseIncludesMessage() throws {
        let response = HTTPResponse.error(404, "unknown endpoint")
        let parsed = try JSONSerialization.jsonObject(with: response.body) as? [String: Any]
        XCTAssertEqual(parsed?["status"] as? String, "error")
        XCTAssertEqual(parsed?["message"] as? String, "unknown endpoint")
        XCTAssertEqual(response.statusCode, 404)
    }

    func testServerStartsAndAcceptsConnection() throws {
        let server = HTTPServer { _ in .json(200, ["status": "ready"]) }
        let port = try server.start()
        defer { server.stop() }
        XCTAssertTrue(port > 0)

        let expectation = XCTestExpectation(description: "HTTP response received")
        let url = URL(string: "http://127.0.0.1:\(port)/health")!
        URLSession.shared.dataTask(with: url) { data, response, error in
            let http = response as? HTTPURLResponse
            XCTAssertNil(error)
            XCTAssertEqual(http?.statusCode, 200)
            if let data = data,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                XCTAssertEqual(json["status"] as? String, "ready")
            } else {
                XCTFail("expected JSON body")
            }
            expectation.fulfill()
        }.resume()
        wait(for: [expectation], timeout: 5)
    }

    func testServerHandlesPostWithBody() throws {
        let server = HTTPServer { request in
            guard let body = request.body,
                  let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                  let handle = json["handle"] as? String else {
                return .error(400, "bad body")
            }
            return .json(200, ["status": "ok", "handle": handle])
        }
        let port = try server.start()
        defer { server.stop() }

        let expectation = XCTestExpectation(description: "POST response received")
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/tap")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["handle": "h-1-0"])

        URLSession.shared.dataTask(with: request) { data, response, error in
            XCTAssertNil(error)
            if let data = data,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                XCTAssertEqual(json["status"] as? String, "ok")
                XCTAssertEqual(json["handle"] as? String, "h-1-0")
            } else {
                XCTFail("expected JSON body")
            }
            expectation.fulfill()
        }.resume()
        wait(for: [expectation], timeout: 5)
    }
}
