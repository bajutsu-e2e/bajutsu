import Foundation
import HTTPTypes
import OpenAPIRuntime

/// The transitional `ServerTransport` that serves the generated handlers over the runner's existing
/// socket layer (Unit 4 of BE-0381).
///
/// Both stacks share one port: `HTTPServer` still accepts connections, parses requests, and frames
/// replies, while every route it dispatches is now a generated handler registered here. That is what
/// keeps the migration a behavior-preserving port — the socket layer BE-0287, BE-0323, and BE-0362
/// hardened is untouched, and the Python driver sees the wire contract it always did. Unit 5 deletes
/// this type along with `HTTPServer` and `Router`, once the listener moves to the chosen framework.
///
/// The module defines its own `HTTPRequest` and `HTTPResponse` (`HTTPServer.swift`), which shadow
/// HTTPTypes' inside this file, so every generated-side use below is written `HTTPTypes.`-qualified.
final class LegacyBackedTransport: ServerTransport {
    private struct Route: Hashable {
        let method: HTTPTypes.HTTPRequest.Method
        let path: String
    }

    private struct TemplatedPathUnsupported: Error {
        let path: String
    }

    private typealias Handler = @Sendable (
        HTTPTypes.HTTPRequest, HTTPBody?, ServerRequestMetadata
    ) async throws -> (HTTPTypes.HTTPResponse, HTTPBody?)

    private let lock = NSLock()
    private var routes: [Route: Handler] = [:]

    func register(
        _ handler: @Sendable @escaping (
            HTTPTypes.HTTPRequest, HTTPBody?, ServerRequestMetadata
        ) async throws -> (HTTPTypes.HTTPResponse, HTTPBody?),
        method: HTTPTypes.HTTPRequest.Method,
        path: String
    ) throws {
        // The route table matches paths literally, so a templated path would register cleanly and
        // then 404 every request; no operation in openapi.yaml uses one, and failing here puts the
        // discovery in the registration test rather than on a device.
        guard !path.contains("{") else { throw TemplatedPathUnsupported(path: path) }
        lock.withLock { routes[Route(method: method, path: path)] = handler }
    }

    /// The paths registered so far, readable by the tests.
    ///
    /// Registration is one generated call covering every operation at once, so a route that silently
    /// went missing would surface only as a 404 on a device. Reading the table back is what lets a
    /// test assert the sixteen the contract declares are all here.
    var registeredRoutes: Set<String> {
        lock.withLock { Set(routes.keys.map { "\($0.method.rawValue) \($0.path)" }) }
    }

    /// Answer *request* from the registered generated handlers, or 404 when none matches.
    ///
    /// The 404 is the legacy router's own: an unknown path — and an unknown *method* on a known
    /// path, which fails to resolve a route just the same — was its `default` case.
    func respond(to request: HTTPRequest) -> HTTPResponse {
        guard let method = HTTPTypes.HTTPRequest.Method(rawValue: request.method),
              let handler = lock.withLock({ routes[Route(method: method, path: request.path)] })
        else {
            return .error(404, "unknown endpoint")
        }
        let generated = HTTPTypes.HTTPRequest(
            method: method, scheme: nil, authority: nil, path: request.path
        )
        // The request's own Content-Type is deliberately dropped rather than forwarded. `Router`
        // never read it, so a driver that sent the wrong one was served anyway; forwarding it here
        // would newly answer 415 and make the port a behavior change. With the header absent the
        // runtime picks the operation's only declared content type, which is what the driver sends.
        return blocking { await Self.serve(handler, generated, request.body) }
    }

    /// Run *work* to completion on this thread.
    ///
    /// `HTTPServer` calls `respond(to:)` on a connection thread — one of at most eight, BE-0287's
    /// bound, which stays the transport's to enforce because `APIHandler` never sees a connection.
    /// The generated handlers are `async`, so the hop blocks that one thread until the task
    /// finishes. That costs the bound nothing: a connection slot was already held for the whole life
    /// of its request under the synchronous `Router`. Nor can it wedge the concurrency pool —
    /// `APIHandler` suspends on its own serial queue rather than blocking a cooperative thread, so
    /// the task this waits on always has somewhere to run.
    private func blocking(_ work: @escaping @Sendable () async -> HTTPResponse) -> HTTPResponse {
        let outcome = Outcome()
        let finished = DispatchSemaphore(value: 0)
        // Declared, not inherited, for the reason BE-0362 gives for the server's own queues: the
        // driver is blocked on every reply this task produces.
        Task(priority: .userInitiated) {
            outcome.response = await work()
            finished.signal()
        }
        finished.wait()
        return outcome.response
    }

    /// Carries one reply back to the blocked caller. The semaphore is the barrier: the task writes
    /// before signalling and the caller reads after waiting, so the two never touch it at once.
    private final class Outcome: @unchecked Sendable {
        var response: HTTPResponse!
    }

    private static func serve(
        _ handler: @escaping Handler, _ request: HTTPTypes.HTTPRequest, _ body: Data?
    ) async -> HTTPResponse {
        do {
            let (response, replyBody) = try await handler(
                request, body.map { HTTPBody($0) }, ServerRequestMetadata()
            )
            var bytes = Data()
            if let replyBody {
                // Uncapped because the only producer is `APIHandler`, whose bodies are already
                // materialized in memory: a cap here would bound this runner's own screenshot rather
                // than anything a peer sent. `HTTPServer` bounds what the peer sends.
                bytes = try await Data(collecting: replyBody, upTo: Int.max)
            }
            return HTTPResponse(
                statusCode: response.status.code,
                contentType: response.headerFields[.contentType] ?? "application/json",
                body: bytes
            )
        } catch let error as ServerError {
            // The runtime already classified the failure — a body that would not decode is a 400,
            // matching what `Router` answered for the same request. The reply carries only the
            // status's reason phrase, never `ServerError.description`: that one interpolates
            // `requestBody` and `operationInput`, which can hold a typed string or a picker row
            // value. The driver reads `status` and never `message` (`_decode` in
            // `bajutsu/drivers/xcuitest.py`), so the diagnostic belongs in the runner log instead.
            //
            // Both fields are logged, and neither echoes the body (measured, including the
            // `dataCorrupted` case, which reports "Unexpected end of file" with no content).
            // `causeDescription` already embeds the decoding detail whenever the runtime recognized
            // the failure, so the two overlap on that path; `underlyingError` is logged for the path
            // where they do not — an error the runtime cannot classify leaves `causeDescription`
            // as the literal string "Unknown", and then it is the only account of what went wrong.
            FileHandle.standardError.write(
                Data("bajutsu runner: \(request.method.rawValue) \(request.path ?? "") failed: \(error.causeDescription): \(error.underlyingError)\n".utf8)
            )
            return .error(error.httpStatus.code, error.httpStatus.reasonPhrase)
        } catch {
            // `UniversalServer` wraps every handler-side failure as a `ServerError`, caught above, so
            // only this function's own `Data(collecting:)` reaches here. Logged for the same reason
            // as above: without it a 500 the driver reports leaves nothing in the runner log.
            FileHandle.standardError.write(
                Data("bajutsu runner: \(request.method.rawValue) \(request.path ?? "") failed: \(error)\n".utf8)
            )
            return .error(500, "internal error")
        }
    }
}
