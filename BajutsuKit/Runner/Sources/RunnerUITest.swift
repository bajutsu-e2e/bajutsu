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
        // failures; a *raised* NSException (an interaction or an `app.snapshot()` query that fails
        // when the screen is in flux — "No matches found", a failed snapshot) unwinds past this and
        // would abort the runner regardless. The Router catches that at every handler boundary
        // (`caughtOnMain`: actuation → stale, query → empty screen, screenshot → 500), so the two
        // together keep the runner serving; a genuinely failed operation still surfaces to the Python
        // side through its response status.
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
