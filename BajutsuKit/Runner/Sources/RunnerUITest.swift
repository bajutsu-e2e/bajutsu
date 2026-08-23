import Foundation
import XCTest
import BajutsuRunner

/// The resident XCUITest runner (BE-0019). Python's `XcuitestEnvironment` starts this via
/// `xcodebuild test-without-building`; it launches the app under test by the forwarded bundle id,
/// serves the loopback actuation endpoints, and stays alive until the Python side tears it down.
final class RunnerUITest: XCTestCase {
    // Set once the server has bound its port. Before that, any recorded failure is a *startup*
    // failure (see `record(_:)`); after it, a recorded soft failure is an operational blip that
    // `continueAfterFailure` tolerates.
    private var serving = false

    override func setUpWithError() throws {
        // The runner is a resident server handling many operations over one long-lived test method,
        // so a single soft XCUITest failure (e.g. a pinch/rotate on a small element that XCUITest
        // flags but still performs) must not end the test and tear the server down — that would
        // leave every later request with "connection refused". This covers only *recorded* soft
        // failures; a *raised* NSException (an interaction or an `app.snapshot()` query that fails
        // when the screen is in flux — "No matches found", a failed snapshot) unwinds past this and
        // would abort the runner regardless. `APIHandler` catches that at every handler boundary
        // (`caught`: actuation → stale, query → empty screen, screenshot → 500), so the two
        // together keep the runner serving; a genuinely failed operation still surfaces to the Python
        // side through its response status.
        continueAfterFailure = true
    }

    override func record(_ issue: XCTIssue) {
        super.record(issue)
        // A failure recorded before the server binds is a startup failure — chiefly the intermittent
        // iOS 26 launch/attach timeout ("Failed to launch …: Timed out attempting to launch app"),
        // which XCUITest gives its own ~39s ceiling. It leaves the runner unusable: the health server
        // below never binds, and — worse — the `xcodebuild` host does not always exit promptly once
        // the test unwinds, so the Python cold-spawn wait burns its whole 300s ceiling on a runner
        // that will never come up, a stall that earns no retry. Exit at once so the Python side
        // observes a dead process and retries with a fresh cold spawn; the launch timeout is
        // intermittent, so a retry usually lands. Once resident (`serving`), a recorded soft failure
        // is the operational blip `continueAfterFailure` deliberately tolerates and must NOT end the
        // runner (`APIHandler` already contains it at each handler boundary).
        guard !serving else { return }
        FileHandle.standardError.write(
            Data("bajutsu runner: startup failure before the server bound — exiting for a fresh cold spawn: \(issue.compactDescription)\n".utf8)
        )
        _exit(EXIT_FAILURE)
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

        // Backstop for the rarer shape of the iOS 26 launch flake: a launch that hangs *silently* —
        // `launch()` never returns and never records a failure, so `record(_:)` above never fires and
        // the server below never binds. (The common shape — a launch that times out and *records* a
        // failure at XCUITest's own ~39s ceiling — is caught by `record(_:)`.) Without this, a silent
        // hang would leave the Python cold-spawn wait to burn its whole 300s ceiling with no retry.
        // The watchdog force-exits the runner if `launch()` overruns, turning the hang into the fast
        // process exit the Python side retries with a fresh cold spawn. `launch()` is fast once the
        // XCTest host is up — host boot, the slow part of a cold start, is already done here — so the
        // ceiling clears a healthy launch by a wide margin and fires only on a true hang.
        let launchWatchdog = LaunchWatchdog(timeout: 90)
        app.launch()
        launchWatchdog.disarm()

        let provider = XcuitestElementProvider(app: app)
        let server = RunnerServer(provider: provider)
        let port = try server.startFromEnvironment()
        XCTAssertGreaterThan(port, 0, "runner server did not bind a port")
        // Resident from here: the runner is healthy, so later recorded soft failures are operational
        // and `record(_:)` must stop force-exiting on them.
        serving = true
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
