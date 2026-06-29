import Foundation

/// The XCUITest runner's HTTP server (BE-0019).
///
/// Start this from a UI test's `setUp` or a long-lived test method, passing
/// the real XCUITest-backed `ElementProviding` implementation. The server
/// binds to `127.0.0.1` and serves requests from the Python driver until
/// stopped.
public final class RunnerServer {
    private let httpServer: HTTPServer
    private let router: Router

    public init(provider: ElementProviding) {
        router = Router(provider: provider)
        httpServer = HTTPServer { [router] request in router.handle(request) }
    }

    /// Start the server. Returns the port it is listening on.
    /// Pass port 0 to let the OS pick an ephemeral port.
    @discardableResult
    public func start(port: UInt16 = 0) throws -> UInt16 {
        try httpServer.start(port: port)
    }

    /// Stop the server and close the listening socket.
    public func stop() {
        httpServer.stop()
    }
}
