import Foundation
import XCTest
@testable import BajutsuRunner

final class IntegrationTests: XCTestCase {
    private var server: RunnerServer!
    private var provider: FakeElementProvider!
    private var port: UInt16!

    override func setUpWithError() throws {
        try super.setUpWithError()
        provider = FakeElementProvider()
        server = RunnerServer(provider: provider)
        port = try server.start()
    }

    override func tearDown() {
        server.stop()
        super.tearDown()
    }

    private func get(_ path: String) -> (Data?, HTTPURLResponse?) {
        let expectation = XCTestExpectation(description: "GET \(path)")
        var resultData: Data?
        var resultResponse: HTTPURLResponse?
        let url = URL(string: "http://127.0.0.1:\(port!)\(path)")!
        URLSession.shared.dataTask(with: url) { data, response, _ in
            resultData = data
            resultResponse = response as? HTTPURLResponse
            expectation.fulfill()
        }.resume()
        wait(for: [expectation], timeout: 5)
        return (resultData, resultResponse)
    }

    private func post(_ path: String, json: [String: Any]) -> (Data?, HTTPURLResponse?) {
        let expectation = XCTestExpectation(description: "POST \(path)")
        var resultData: Data?
        var resultResponse: HTTPURLResponse?
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port!)\(path)")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: json)
        URLSession.shared.dataTask(with: request) { data, response, _ in
            resultData = data
            resultResponse = response as? HTTPURLResponse
            expectation.fulfill()
        }.resume()
        wait(for: [expectation], timeout: 5)
        return (resultData, resultResponse)
    }

    private func parseJSON(_ data: Data?) -> [String: Any]? {
        guard let data = data else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    func testHealthEndpoint() {
        let (data, response) = get("/health")
        XCTAssertEqual(response?.statusCode, 200)
        XCTAssertEqual(parseJSON(data)?["status"] as? String, "ready")
    }

    func testElementsAndTapRoundTrip() {
        let backing = NSObject()
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "submit", label: "Submit", value: nil,
                traits: ["button"], frame: (10, 20, 80, 44), backingElement: backing
            ),
        ]

        let (elemData, _) = get("/elements")
        let elemJSON = parseJSON(elemData)
        let elements = elemJSON?["elements"] as? [[String: Any]]
        XCTAssertEqual(elements?.count, 1)

        let handle = elements?.first?["handle"] as? String
        XCTAssertNotNil(handle)
        XCTAssertEqual(elements?.first?["identifier"] as? String, "submit")
        XCTAssertEqual(elements?.first?["frame"] as? [Double], [10, 20, 80, 44])

        let (tapData, tapResponse) = post("/tap", json: ["handle": handle!])
        XCTAssertEqual(tapResponse?.statusCode, 200)
        XCTAssertEqual(parseJSON(tapData)?["status"] as? String, "ok")
        XCTAssertEqual(provider.tapCalls.count, 1)
        XCTAssertTrue(provider.tapCalls[0].backingElement === backing)
    }

    func testStaleHandleAfterRefresh() {
        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "a", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        let (firstData, _) = get("/elements")
        let firstHandle = ((parseJSON(firstData)?["elements"] as? [[String: Any]])?.first?["handle"] as? String)!

        provider.elementsToReturn = [
            ElementSnapshot(
                identifier: "b", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ]
        _ = get("/elements")

        let (tapData, _) = post("/tap", json: ["handle": firstHandle])
        XCTAssertEqual(parseJSON(tapData)?["status"] as? String, "stale")
    }

    func testScreenshotReturnsRawPNG() {
        let png = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        provider.screenshotData = png

        let (data, response) = get("/screenshot")
        XCTAssertEqual(response?.statusCode, 200)
        XCTAssertEqual(data, png)
    }
}
