import Foundation
import XCTest

@testable import BajutsuKit

extension CollectorCredential {
    /// The credential an older host yields: it announces no signed scheme.
    static func bearer(_ token: String) -> CollectorCredential {
        CollectorCredential(token: token, signs: false)
    }
}

/// The app side of BE-0459's signed scheme: the canonical forms against the vectors the collector's
/// own tests read, and the credential's request and answer handling.
final class CollectorSigningTests: XCTestCase {
    private struct Vectors: Decodable {
        struct Request: Decodable {
            let token, method, path, nonce, body, signature: String
        }
        struct Answer: Decodable {
            let token, nonce, body, signature: String
            let status: Int
        }
        struct Path: Decodable {
            let target, path: String
        }
        let requests: [Request]
        let answers: [Answer]
        let paths: [Path]
    }

    /// The fixture file the Python suite reads too, found from this source file's own location.
    private func vectors() throws -> Vectors {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // BajutsuKitTests
            .deletingLastPathComponent()  // Tests
            .deletingLastPathComponent()  // BajutsuKit
            .deletingLastPathComponent()  // the repository
        let url = root.appendingPathComponent("tests/fixtures/be0459/collector_hmac_vectors.json")
        return try JSONDecoder().decode(Vectors.self, from: Data(contentsOf: url))
    }

    // --- the canonical forms ---

    func testRequestVectorsSignToTheSignatureTheCollectorPinned() throws {
        for v in try vectors().requests {
            XCTAssertEqual(
                CollectorSigning.requestSignature(
                    token: v.token, method: v.method, path: v.path, nonce: v.nonce, body: Data(v.body.utf8)
                ),
                v.signature,
                "\(v.method) \(v.path)"
            )
        }
    }

    func testAnswerVectorsSignToTheSignatureTheCollectorPinned() throws {
        for v in try vectors().answers {
            XCTAssertEqual(
                CollectorSigning.answerSignature(
                    token: v.token, nonce: v.nonce, status: v.status, body: Data(v.body.utf8)
                ),
                v.signature,
                "\(v.status)"
            )
        }
    }

    func testTheCanonicalPathMatchesTheCollectorsForEveryVectorTarget() throws {
        for v in try vectors().paths where !v.target.isEmpty {
            let url = try XCTUnwrap(URL(string: "http://192.0.2.7:6801\(v.target)"))
            XCTAssertEqual(CollectorSigning.canonicalPath(url), v.path, v.target)
        }
        // The bare report URL a collector is offered as has no path at all.
        XCTAssertEqual(CollectorSigning.canonicalPath(URL(string: "http://192.0.2.7:6801")!), "/")
    }

    func testNoncesAreFreshSixteenBytesUnpadded() {
        let nonces = Set((0..<64).map { _ in CollectorSigning.newNonce() })
        XCTAssertEqual(nonces.count, 64)
        XCTAssertTrue(nonces.allSatisfy { $0.count == 22 && !$0.contains("=") })
    }

    func testSignaturesMatchOnlyWhenEqual() {
        XCTAssertTrue(CollectorSigning.matches("abc", "abc"))
        XCTAssertFalse(CollectorSigning.matches("abc", "abd"))
        XCTAssertFalse(CollectorSigning.matches("abc", "abcd"))
    }

    // --- the announcement ---

    func testTheKitSignsOnlyWhenTheHostAnnouncesTheScheme() {
        XCTAssertEqual(CollectorCredential(token: "t", auth: "hmac")?.signs, true)
        XCTAssertEqual(CollectorCredential(token: "t", auth: nil)?.signs, false)
        XCTAssertEqual(CollectorCredential(token: "t", auth: "something-newer")?.signs, false)
        XCTAssertNil(CollectorCredential(token: nil, auth: "hmac"))
        XCTAssertNil(CollectorCredential(token: "", auth: "hmac"))
    }

    func testTheCredentialIsReadFromTheKeysTheHostInjects() {
        // The pool's own test pins the same two keys and the `hmac` value on the Python side.
        let signed = CollectorCredential(
            environment: ["BAJUTSU_COLLECTOR_TOKEN": "t", "BAJUTSU_COLLECTOR_AUTH": "hmac"]
        )
        XCTAssertEqual(signed, CollectorCredential(token: "t", signs: true))
        XCTAssertEqual(
            CollectorCredential(environment: ["BAJUTSU_COLLECTOR_TOKEN": "t"]), .bearer("t")
        )
        XCTAssertNil(CollectorCredential(environment: ["BAJUTSU_COLLECTOR_AUTH": "hmac"]))
    }

    func testTheKitSendsTheBearerHeaderWhenTheSchemeIsNotAnnounced() {
        let credential = CollectorCredential(token: "run-token", auth: nil)!
        var request = URLRequest(url: URL(string: "http://127.0.0.1:6801/ping")!)
        XCTAssertNil(credential.authorize(&request))
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer run-token")
    }

    func testASignedRequestCarriesNoTokenAndASignatureOverItsBody() throws {
        let credential = CollectorCredential(token: "run-token", signs: true)
        var request = URLRequest(url: URL(string: "http://192.0.2.7:6801")!)
        request.httpMethod = "POST"
        request.httpBody = Data(#"{"url":"https://example.test"}"#.utf8)
        let nonce = try XCTUnwrap(credential.authorize(&request))
        let header = try XCTUnwrap(request.value(forHTTPHeaderField: "Authorization"))
        XCTAssertFalse(header.contains("run-token"))
        XCTAssertEqual(
            header,
            CollectorSigning.authorization(
                token: "run-token", method: "POST", path: "/", nonce: nonce, body: request.httpBody!
            )
        )
    }

    // --- the answers ---

    private func answer(_ status: Int, signature: String?) -> HTTPURLResponse {
        var headers: [String: String] = [:]
        if let signature { headers[CollectorSigning.answerHeader] = signature }
        return HTTPURLResponse(
            url: URL(string: "http://192.0.2.7:6801/ping")!, statusCode: status, httpVersion: "HTTP/1.1",
            headerFields: headers
        )!
    }

    func testTheProbeTakesASignedAnswerBoundToItsNonce() {
        let credential = CollectorCredential(token: "run-token", signs: true)
        let signature = CollectorSigning.answerSignature(
            token: "run-token", nonce: "n1", status: 204, body: Data()
        )
        XCTAssertTrue(
            BajutsuNet.probeAnswered(answer(204, signature: signature), body: nil, credential: credential, nonce: "n1")
        )
        // The same answer replayed against a later probe's nonce.
        XCTAssertFalse(
            BajutsuNet.probeAnswered(answer(204, signature: signature), body: nil, credential: credential, nonce: "n2")
        )
    }

    func testTheProbeRejectsACandidateThatAnswersAnUnsigned204() {
        let credential = CollectorCredential(token: "run-token", signs: true)
        XCTAssertFalse(
            BajutsuNet.probeAnswered(answer(204, signature: nil), body: nil, credential: credential, nonce: "n1")
        )
    }

    func testTheProbeTreatsA409AsNotAnswered() {
        let credential = CollectorCredential(token: "run-token", signs: true)
        XCTAssertFalse(
            BajutsuNet.probeAnswered(answer(409, signature: nil), body: nil, credential: credential, nonce: "n1")
        )
    }

    func testABearerProbeTakesAPlain204AsItAlwaysDid() {
        XCTAssertTrue(
            BajutsuNet.probeAnswered(answer(204, signature: nil), body: nil, credential: .bearer("t"), nonce: nil)
        )
    }
}
