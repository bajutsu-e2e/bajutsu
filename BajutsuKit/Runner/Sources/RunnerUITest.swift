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
        // side through its response status. XCUIAutomation's own failures interrupt the test
        // regardless of this flag; `serve` contains that interruption (`ResidentLoop`).
        continueAfterFailure = true

        // Take the answer to an out-of-process system prompt away from XCUITest. Before it
        // synthesizes any element interaction — and before some application-level queries — XCUITest
        // resolves whatever alert is interrupting; with no monitor registered it falls back to its
        // own default handler, which taps the alert's **default** button. Measured on iOS 18.6, that
        // grants the notification authorization request ("Allow") the moment any tap lands while the
        // request is up: the opposite of the scenario's own policy, invisible to the run (no step
        // fails, no `AlertEvent` is reported), and intermittent, since it depends on an interaction
        // coinciding with the prompt.
        //
        // The monitor answers instead, by the labels `InterruptionPolicy` carries — every one of
        // them resolved on the Python side from the scenario's `systemAlertHandling` and pushed over
        // `POST /interruptionPolicy`. It must actually press a button rather than merely claim the
        // alert: XCUITest verifies the interruption cleared, and a monitor that returns `true` while
        // the alert is still up is re-invoked on the very next interaction — in a resident runner
        // that serves queries continuously, an unbounded loop that takes the runner down with it
        // (measured). Declining (`false`) when no rule identifies this alert is therefore the only
        // safe fallback: it hands the alert back to XCUITest's default handler, which is what
        // happened before this monitor existed and does clear it. Since BE-0406 Unit 2b, a decline
        // the policy `governs` is recorded before it happens, so the step or `expect` that met it
        // can fail by name instead of continuing as if nothing had answered on the scenario's
        // behalf.
        _ = addUIInterruptionMonitor(withDescription: "bajutsu: the scenario's policy answers system alerts") { alert in
            // Answered before the policy is consulted at all — in particular before the `governs`
            // guard, since neither branch below is right for a banner (see `isNotificationBanner`,
            // BE-0416): a governed run would record it as an undeclared interruption and fail a
            // passing step, an ungoverned one would leave XCUITest to wait it out.
            if isNotificationBanner(identifier: alert.identifier) {
                return Self.swipeAwayNotificationBanner()
            }
            let policy = InterruptionPolicyStore.shared.policy
            guard policy.governs else { return false }
            let buttons = alert.buttons
            let labels = (0..<buttons.count).map { buttons.element(boundBy: $0).label }
            // The title joins the matched labels as a marker, so a prompt sharing another's buttons
            // (Local Network and notifications) is told apart the way the Python guard tells it.
            let title = alert.label
            let matched = title.isEmpty ? labels : labels + [alertTitleMarker(title)]
            guard let rule = policy.rule(for: matched, surface: .native) else {
                // A `labels` carrying no real button text is the same benign race `button.exists`
                // guards below: the alert lost the race with its own dismissal between XCUITest
                // flagging the interruption and this query — `buttons.count` can already reflect
                // the vanished alert while each individual `.label` resolves to `""`, so this
                // checks every element rather than the array's emptiness alone. Recording it would
                // fail an otherwise-passing step over nothing, naming no buttons at all (BE-0406
                // Unit 2b).
                if labels.contains(where: { !$0.isEmpty }) {
                    InterruptionPolicyStore.shared.recordDeclined(labels)
                }
                return false
            }
            let label = rule.tap
            guard let ordinal = labels.firstIndex(of: label) else { return false }
            let button = buttons.element(boundBy: ordinal)
            guard button.exists else { return false }
            button.tap()
            // Recorded so the driver can report this as an `AlertEvent`: a prompt answered here
            // being missing from the report is the failure this mechanism exists to end. The store
            // records only a showing's first press, so a showing a resolving read already answered
            // is not reported twice (BE-0461 Unit 2) — the press itself always happens.
            InterruptionPolicyStore.shared.recordMonitorPress(rule, alert: matched)
            return true
        }
    }

    /// Clear an interrupting notification banner with an upward swipe, and report whether it went.
    ///
    /// A swipe, not a tap: the banner's one button opens the notification's own app, which would
    /// navigate the run away from the scenario under test. The gesture is anchored to the banner's
    /// measured frame rather than a fixed screen coordinate, so it holds across device sizes, and it
    /// ends just above that frame rather than past the screen's edge, where the drag would become
    /// SpringBoard's own top-edge gesture instead. That frame is read only once the banner has
    /// settled: XCUITest can flag the banner while it is still sliding in from above the screen, and
    /// a frame read then puts the swipe's start off-screen and its end *below* it — a downward drag
    /// that cannot dismiss anything (issue #2156, measured on iOS 26.5 at a frame centred at y=-78.7).
    ///
    /// Returning `true` claims the interruption, and XCUITest re-invokes a monitor that claimed one
    /// it did not actually clear — the same unbounded reinvocation loop BE-0399 measured for an
    /// alert (above), reproduced for a banner while measuring BE-0416 by capping a throwaway test
    /// monitor at six invocations rather than letting it run to the failure BE-0399 hit. So the
    /// clearance is *confirmed* before claiming it.
    ///
    /// Declining is the last resort, not the fallback: XCUITest's own handler does not clear a
    /// banner. Measured on iOS 26.5, it ignores a non-alert element, and every lookup it makes of the
    /// banner by its `BannerNotification` type misses ("Automation type mismatch": the snapshot
    /// classifies the banner as `Other`) and records a UI automation failure; those failures then
    /// preempted the interrupted `swipe` and ended the test case, losing the runner (#2156). So an
    /// unconfirmed swipe keeps waiting for the banner's own auto-dismissal, which clears it as surely
    /// as the swipe would, and declines only if the banner outlasts that too — or if the identifier
    /// query cannot see the banner at all, where its absence would prove nothing (below).
    ///
    /// Either outcome still needs to be *legible*, or it reproduces in miniature the invisibility
    /// this whole mechanism exists to end: nothing calls `recordDeclined` for a banner (that path
    /// classifies as an `UndeclaredInterruption` and would fail the step, the outcome BE-0416 removes
    /// for a banner), so an unconfirmed swipe is logged instead — observable without failing the step.
    private static func swipeAwayNotificationBanner() -> Bool {
        let springboard = XCUIApplication(bundleIdentifier: springboardBundleID)
        let remaining = springboard.descendants(matching: .any)
            .matching(identifier: notificationBannerIdentifier)
        // Every later check reads the banner's absence from this query as its clearance, which holds
        // only once the query has seen the banner. A query that cannot resolve it here may simply be
        // missing a banner still on screen, and claiming that is the reinvocation loop above — so
        // decline rather than claim an interruption nothing here can confirm.
        let current = remaining.firstMatch
        guard current.exists else {
            logBanner("the identifier query could not see the interrupting banner — declining to XCUITest's own handler")
            return false
        }
        // Read before the swipe: once the banner is gone its label resolves to empty, and the label
        // is the only thing that identifies the dismissal in the run's report.
        let label = current.label
        guard let frame = settledFrame(of: remaining.firstMatch) else {
            return awaitBannerClearance(
                remaining, label: label, swiped: false, unconfirmed: "the banner never settled on screen"
            )
        }
        // The outcome itself, not only its proxy: a settled frame implies an upward swipe of at least
        // `bannerSwipeTravel` for the banner shapes measured, but the top-margin clamp can still
        // shorten or invert it for others. The same rule the step loop's proactive sweep applies
        // (`notification_banner_swipe_points`), so the two cannot drift.
        let endY = max(bannerSwipeTopMargin, frame.minY - bannerSwipeTravel)
        guard frame.midY - endY >= bannerSwipeTravel else {
            return awaitBannerClearance(
                remaining, label: label, swiped: false,
                unconfirmed: "the settled frame left the swipe shorter than \(bannerSwipeTravel) points"
            )
        }
        let origin = springboard.coordinate(withNormalizedOffset: .zero)
        let from = origin.withOffset(CGVector(dx: frame.midX, dy: frame.midY))
        let to = origin.withOffset(CGVector(dx: frame.midX, dy: endY))
        // Re-checked immediately before the gesture, mirroring the alert branch's own `button.exists`
        // guard above: the banner can lose the race with its own auto-dismissal between the frame
        // read and this press, and a flick delivered at its former screen position would then land on
        // whatever the application under test draws there instead — a gesture the scenario never
        // asked for. A banner already gone is cleared, so it is confirmed rather than declined.
        guard remaining.firstMatch.exists else {
            return awaitBannerClearance(
                remaining, label: label, swiped: false, unconfirmed: "the banner disappeared before the swipe"
            )
        }
        from.press(forDuration: bannerSwipePressDuration, thenDragTo: to)
        return awaitBannerClearance(
            remaining, label: label, swiped: true, unconfirmed: "the swipe did not clear the banner"
        )
    }

    /// The banner's frame once it is fully on screen and has stopped moving, or `nil` if it never
    /// does (or disappears) within `bannerSettleTimeout`. Two consecutive reads agreeing within a
    /// point mark the end of the slide-in animation; a top edge at or below the screen's own rules
    /// out a banner that is still partly above it.
    ///
    /// Read through the type-agnostic identifier query rather than the typed interrupting element.
    /// The lookups seen missing in #2156 were XCUITest's own, by the banner's `BannerNotification`
    /// type, after the monitor declined; the monitor's typed reads did resolve there. The identifier
    /// query is what the banner is matched on in the first place, so the monitor's reads stay off
    /// the type that iOS 26.5 misclassifies.
    private static func settledFrame(of banner: XCUIElement) -> CGRect? {
        var previous: CGRect?
        let deadline = Date().addingTimeInterval(bannerSettleTimeout)
        repeat {
            // `exists` first: reading `frame` of a vanished element records a UI automation failure.
            // Two separate resolutions, so this narrows that race rather than closing it; the paced
            // poll below bounds how many times the race is taken.
            guard banner.exists else { return nil }
            let frame = banner.frame
            // Within a point rather than exact `CGRect` equality: a frame that jitters by a fraction
            // of a point would otherwise never count as settled, and the gesture depends only on
            // `minY` and `height`.
            if let previous, frame.height > 0, frame.minY >= 0,
               abs(frame.minY - previous.minY) < 1, abs(frame.height - previous.height) < 1 {
                return frame
            }
            previous = frame
            Thread.sleep(forTimeInterval: bannerSettlePollInterval)
        } while Date() < deadline
        return nil
    }

    /// Confirm the banner is gone and claim it, or decline once it has outlasted
    /// `bannerClearanceTimeout`. A cleared banner is still claimed when the swipe was skipped, or when
    /// it went only after `bannerSwipeClearanceWindow`, since it is cleared either way; both cases are
    /// logged so the path that cleared it stays observable.
    private static func awaitBannerClearance(
        _ remaining: XCUIElementQuery, label: String, swiped: Bool, unconfirmed reason: String
    ) -> Bool {
        let start = Date()
        repeat {
            if !remaining.firstMatch.exists {
                let elapsed = Date().timeIntervalSince(start)
                if !swiped || elapsed > bannerSwipeClearanceWindow {
                    logBanner("\(reason); it cleared on its own after \(String(format: "%.1f", elapsed))s")
                }
                // An empty label would put a blank event in the run's report; the clearance is
                // still real, so it is claimed and only logged.
                if label.isEmpty {
                    logBanner("cleared, but its label read empty, so the report records nothing")
                } else {
                    InterruptionPolicyStore.shared.recordBanner(label)
                }
                return true
            }
        } while Date().timeIntervalSince(start) < bannerClearanceTimeout
        logBanner("\(reason); still on screen after \(bannerClearanceTimeout)s — declining to XCUITest's own handler")
        return false
    }

    private static func logBanner(_ message: String) {
        FileHandle.standardError.write(
            Data("bajutsu runner: notification banner: \(message)\n".utf8)
        )
    }

    /// How far above the banner's own top edge the swipe ends. Measured sufficient on iOS 26.5;
    /// the gesture only has to carry the banner into its dismissal, not off the screen.
    private static let bannerSwipeTravel: CGFloat = 20

    /// The highest point the swipe may end at. SpringBoard claims a drag that starts or ends within
    /// a few points of the top edge as its own notification-shade gesture, which would pull the
    /// shade down instead of dismissing the banner.
    private static let bannerSwipeTopMargin: CGFloat = 8

    /// How long the gesture presses before it starts travelling — half the `swipe` path's own 0.1s,
    /// so the drag reads as a dismissal rather than a long press. It is not what makes the gesture a
    /// flick: `press(forDuration:thenDragTo:)` takes no velocity, so the traversal runs at XCUITest's
    /// `.default` speed either way. The knob that would change that is `withVelocity:`, which `scroll`
    /// passes precisely because it is "the whole of what makes the gesture non-inertial"
    /// (`XcuitestElementProvider.scrollVelocity`, BE-0400).
    private static let bannerSwipePressDuration: TimeInterval = 0.05

    /// How long the banner may take to finish sliding in before the swipe is abandoned. The
    /// slide-in is a fraction of a second; this only bounds a banner that never comes to rest.
    private static let bannerSettleTimeout: TimeInterval = 2

    /// The pause between settle reads. Each read is an `exists` → `frame` pair that a banner
    /// vanishing in between turns into a recorded UI automation failure, so an unpaced poll would
    /// take that race once per ~64ms query pair, about 30 times in `bannerSettleTimeout`. Paced, it
    /// takes it at most ~20 times on any host, and the usual fraction-of-a-second slide-in settles in
    /// a handful. Two equal reads 0.1s apart are also firmer evidence the banner has stopped moving
    /// than two taken back to back.
    private static let bannerSettlePollInterval: TimeInterval = 0.1

    /// Not a deadline: the line past which a clearance after a real swipe is logged as the banner's
    /// own auto-dismissal rather than the swipe's work. It keeps the confirmation window the swipe had
    /// before #2156.
    private static let bannerSwipeClearanceWindow: TimeInterval = 2

    /// How long clearance is re-checked before the monitor declines. Deadlines, not sample counts: a
    /// fixed count of SpringBoard queries scales with host speed, so it can decide "unconfirmed" for a
    /// swipe that actually landed on a slow host, or block a fast one for far longer than the ~32ms
    /// per query measured while sizing this. A deadline instead gives the dismissal the same real
    /// time on every host, while each poll is still a real SpringBoard query rather than a sleep
    /// (determinism first). Sized to outlast a temporary banner's own display time, so declining
    /// after a clearance wait — fatal to the runner, see `swipeAwayNotificationBanner` — is reached
    /// only by a banner that will not leave by itself. The worst case per interruption, settle
    /// (`bannerSettleTimeout`, 2s) plus clearance (10s), is about 12s, well inside the 30s actuation window
    /// (`_ACTUATION_TIMEOUT_SECONDS` in `bajutsu/common/drivers/xcuitest/_functions.py`).
    private static let bannerClearanceTimeout: TimeInterval = 10

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
        if RunnerServer.forwardedAttach {
            // BE-0455: drive the process the operator already has open. No launch, so no launch env
            // or arguments to apply and no launch hang for the watchdog below to catch. An app that
            // exited after Python's probe, or sits in the background, would serve an empty tree under
            // a healthy runner; failing before the server binds exits through `record(_:)` instead,
            // so the one attach spawn fails loudly with this line in its log tail.
            guard app.state == .runningForeground else {
                XCTFail("bajutsu attach: \(RunnerServer.forwardedBundleId ?? "the app") is not running in the foreground (XCUIApplication.State \(app.state.rawValue))")
                return
            }
            try serve(app)
            return
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
        try serve(app)
    }

    /// Serve the loopback endpoints over `app` until Python terminates the process at teardown.
    private func serve(_ app: XCUIApplication) throws {
        let provider = XcuitestElementProvider(app: app)
        let server = RunnerServer(provider: provider)
        let port = try server.startFromEnvironment()
        XCTAssertGreaterThan(port, 0, "runner server did not bind a port")
        // Resident from here: the runner is healthy, so later recorded soft failures are operational
        // and `record(_:)` must stop force-exiting on them.
        serving = true
        defer { server.stop() }

        // Stay resident: pump the main run loop (servicing the server thread's
        // DispatchQueue.main work) until Python terminates the process at teardown. Each turn is
        // contained, because XCTest raises a deferred test interruption as the turn returns —
        // outside every handler boundary (`ResidentLoop`).
        while true {
            ResidentLoop.runTurn {
                RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow: 1))
            }
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
