import XCTest
import BajutsuRunner

/// The resident XCUITest runner (BE-0019). Python's `XcuitestEnvironment` starts this via
/// `xcodebuild test-without-building`; it launches the app under test by the forwarded bundle id,
/// serves the loopback actuation endpoints, and stays alive until the Python side tears it down.
final class RunnerUITest: XCTestCase {
    override func setUpWithError() throws {
        // The runner is a resident server handling many operations over one long-lived test method,
        // so a single soft XCUITest failure (e.g. a pinch/rotate on a small element that XCUITest
        // flags but still performs) must not end the test and tear the server down — that would
        // leave every later request with "connection refused". This covers only *recorded* soft
        // failures; a *raised* NSException (an element interaction that fails to resolve, "No matches
        // found", when the screen shifts mid-tap) unwinds past this and aborts the runner regardless.
        // The Router catches that at the actuation boundary (`onMainCatching`) and reports it as a
        // stale miss, so the two together keep the runner serving; a genuinely failed operation still
        // surfaces to the Python side through its response status.
        continueAfterFailure = true
    }

    func testServeUntilTornDown() throws {
        let app: XCUIApplication
        if let bundleId = RunnerServer.forwardedBundleId {
            app = XCUIApplication(bundleIdentifier: bundleId)
        } else {
            app = XCUIApplication()
        }
        for (key, value) in RunnerServer.forwardedLaunchEnvironment {
            app.launchEnvironment[key] = value
        }
        app.launchArguments += RunnerServer.forwardedLaunchArguments
        app.launch()

        let provider = XcuitestElementProvider(app: app)
        let server = RunnerServer(provider: provider)
        let port = try server.startFromEnvironment()
        XCTAssertGreaterThan(port, 0, "runner server did not bind a port")
        defer { server.stop() }

        // Stay resident: pump the main run loop (servicing the server thread's
        // DispatchQueue.main work) until Python terminates the process at teardown.
        while true {
            RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow: 1))
        }
    }
}
