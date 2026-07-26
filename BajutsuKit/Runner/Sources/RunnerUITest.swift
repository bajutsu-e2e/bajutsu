import Foundation
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

        // Bound `app.launch()` against an intermittent iOS 26 Simulator launch-attach hang. On some
        // cold launches the launch/accessibility handshake never completes, so `launch()` never
        // returns: the server below never binds, and the Python cold-spawn wait then burns its whole
        // startup ceiling (300s on CI) before failing — a stall that earns no retry, because that one
        // attempt spent the entire shared budget. The watchdog force-exits the runner if the launch
        // overruns, turning the indefinite hang into the fast process exit the Python side already
        // retries with a fresh cold spawn (a launch that *raises* instead already exits fast and
        // retries; this covers the launch that hangs). A genuinely unlaunchable app overruns every
        // attempt and still fails the gate, so no real breakage is absorbed. `launch()` is fast once
        // the XCTest host is up — host boot, the slow part of a cold start, is already done here — so
        // the ceiling clears a healthy launch by a wide margin and fires only on a true hang.
        let launchWatchdog = LaunchWatchdog(timeout: 90)
        app.launch()
        launchWatchdog.disarm()

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

/// Force-exits the runner if `app.launch()` overruns *timeout*, so an intermittent iOS 26 Simulator
/// launch hang becomes the fast process exit the Python cold-spawn retry heals — not a stall that
/// consumes the whole startup budget and earns no retry. `disarm()` is called the instant `launch()`
/// returns, so a healthy launch (well under the ceiling) never trips it.
private final class LaunchWatchdog {
    private let lock = NSLock()
    private var completed = false

    init(timeout: TimeInterval) {
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { [weak self] in
            guard let self else { return }
            self.lock.lock()
            let done = self.completed
            self.lock.unlock()
            guard !done else { return }
            FileHandle.standardError.write(
                Data("bajutsu runner: app.launch() exceeded \(Int(timeout))s — exiting for a fresh cold spawn\n".utf8)
            )
            // `_exit` (not `exit`) so no atexit handler can deadlock on a lock the stuck launch holds.
            _exit(EXIT_FAILURE)
        }
    }

    func disarm() {
        lock.lock()
        completed = true
        lock.unlock()
    }
}
