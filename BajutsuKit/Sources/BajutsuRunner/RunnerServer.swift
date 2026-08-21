import Foundation

/// The XCUITest runner's HTTP server (BE-0019).
///
/// Start this from a UI test's `setUp` or a long-lived test method, passing
/// the real XCUITest-backed `ElementProviding` implementation. The server
/// binds to `127.0.0.1` and serves requests from the Python driver until
/// stopped.
///
/// Every route is answered by a handler generated from `openapi.yaml` (BE-0381): `APIHandler`
/// holds the logic and `LegacyBackedTransport` carries it over `HTTPServer`'s socket layer, which
/// Unit 5 replaces. The public interface here is what the migration deliberately holds still.
public final class RunnerServer {
    private let httpServer: HTTPServer
    private let handler: APIHandler
    private let transport: LegacyBackedTransport

    public init(provider: ElementProviding) {
        handler = APIHandler(provider: provider)
        transport = LegacyBackedTransport()
        httpServer = HTTPServer { [transport] request in transport.respond(to: request) }
    }

    /// Start the server. Returns the port it is listening on.
    /// Pass port 0 to let the OS pick an ephemeral port.
    @discardableResult
    public func start(port: UInt16 = 0) throws -> UInt16 {
        // Registration lands here rather than in `init` because the generated call throws and this
        // type's initializer is part of the public surface BE-0381 keeps unchanged. It runs before
        // the socket binds, so no connection can arrive at an empty route table.
        try handler.registerHandlers(on: transport)
        return try httpServer.start(port: port)
    }

    /// Start the server on the port from `BAJUTSU_RUNNER_PORT`, or an ephemeral port if unset.
    ///
    /// The Python `XcuitestEnvironment` allocates a port and passes it through this env var so
    /// the driver can connect immediately after readiness. A set-but-unparseable value throws
    /// rather than silently falling back to an ephemeral port.
    @discardableResult
    public func startFromEnvironment() throws -> UInt16 {
        guard let raw = ProcessInfo.processInfo.environment["BAJUTSU_RUNNER_PORT"] else {
            return try start(port: 0)
        }
        guard let port = UInt16(raw) else {
            throw EnvironmentError.invalidPort(raw)
        }
        return try start(port: port)
    }

    public enum EnvironmentError: Error, CustomStringConvertible {
        case invalidPort(String)

        public var description: String {
            switch self {
            case .invalidPort(let raw):
                return "BAJUTSU_RUNNER_PORT is not a valid port number: \(raw)"
            }
        }
    }

    /// The app launch environment forwarded by the Python environment via `BAJUTSU_LAUNCH_ENV_*`.
    ///
    /// Each env var whose key starts with `BAJUTSU_LAUNCH_ENV_` is collected with the prefix
    /// stripped; the caller sets these on `XCUIApplication.launchEnvironment` before `launch()`.
    public static var forwardedLaunchEnvironment: [String: String] {
        let prefix = "BAJUTSU_LAUNCH_ENV_"
        var result: [String: String] = [:]
        for (key, value) in ProcessInfo.processInfo.environment where key.hasPrefix(prefix) {
            result[String(key.dropFirst(prefix.count))] = value
        }
        return result
    }

    /// The app launch arguments forwarded by the Python environment via `BAJUTSU_LAUNCH_ARGS`.
    ///
    /// The value is a JSON array of strings; an absent or malformed value yields `[]`.
    public static var forwardedLaunchArguments: [String] {
        guard let raw = ProcessInfo.processInfo.environment["BAJUTSU_LAUNCH_ARGS"],
              let data = raw.data(using: .utf8),
              let array = try? JSONSerialization.jsonObject(with: data) as? [String] else {
            return []
        }
        return array
    }

    /// The deeplink URL forwarded by the Python environment via `BAJUTSU_DEEPLINK`, if any.
    public static var forwardedDeeplink: String? {
        ProcessInfo.processInfo.environment["BAJUTSU_DEEPLINK"]
    }

    /// The bundle id of the app under test, forwarded via `BAJUTSU_BUNDLE_ID`.
    ///
    /// One generic runner drives whatever app the run targets, so the host launches
    /// `XCUIApplication(bundleIdentifier:)` with this rather than its own target app.
    public static var forwardedBundleId: String? {
        ProcessInfo.processInfo.environment["BAJUTSU_BUNDLE_ID"]
    }

    /// Stop the server and close the listening socket.
    public func stop() {
        httpServer.stop()
    }
}
