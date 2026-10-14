import Foundation
@testable import BajutsuRunner

final class FakeElementProvider: ElementProviding {
    var elementsToReturn: [ElementSnapshot] = []
    var tapResult: TapResult = .ok
    var screenshotData: Data? = Data([0x89, 0x50, 0x4E, 0x47])
    // When set, `tap` raises this NSException instead of returning — standing in for the XCUITest
    // "No matches found" interaction failure the runner must survive (the Router catches it as stale).
    var tapRaises: NSException?
    // When set, `queryElements` raises this — standing in for an `app.snapshot()` that raises while
    // the UI is in flux (the Router catches it as an empty screen, not a runner crash).
    var queryRaises: NSException?
    // When set, the SpringBoard query raises this, as an alert torn down mid-read can.
    var systemAlertQueryRaises: NSException?
    /// Runs at the top of `queryElements`, on the main thread the operation has already taken. A
    /// test holds it there to check what the runner still answers meanwhile (BE-0287).
    var beforeQueryElements: (() -> Void)?

    var isHittableResult: TapResult = .ok

    /// When set, every touch actuation that lands marks itself here, as the real provider does
    /// (BE-0462). Nil by default, so the parity tests' replies carry no `synthesisId` the legacy
    /// `Router` could never produce.
    var ledger: SynthesisLedger?
    /// How many times `gesture` synthesizes per call, standing in for the opt-in retry's attempts.
    var gestureAttempts = 1

    var tapCalls: [(backingElement: AnyObject, taps: Int, duration: TimeInterval)] = []
    var isHittableCalls: [AnyObject] = []
    var tapPointCalls: [(x: Double, y: Double)] = []
    var gestureCalls: [(backingElement: AnyObject, kind: String, scale: Double, radians: Double)] = []
    var swipeCalls: [(fromX: Double, fromY: Double, toX: Double, toY: Double)] = []
    var scrollCalls: [(fromX: Double, fromY: Double, toX: Double, toY: Double)] = []
    var typeCalls: [String] = []
    var deleteTextCalls: [Int] = []
    var selectAllCalls = 0
    var copyCalls = 0
    // The picker-wheel rows `setPickerValue` records, and the result it reports (BE-0356). Kept apart
    // from the shared `tapResult` so a test can script a `.valueNotFound` wheel without changing what
    // every other actuation returns.
    var setPickerValueResult: TapResult = .ok
    var setPickerValueCalls: [(backingElement: AnyObject, value: String)] = []
    // The SpringBoard alert buttons `/systemAlert/query` returns, and the taps it records (BE-0316).
    var systemAlertButtons: [ElementSnapshot] = []
    var systemAlertTapCalls: [AnyObject] = []
    // How many times `/systemAlert/query` or a resolving read checked SpringBoard and read the app
    // tree, and hooks that change the screen after a press — what the BE-0461 resolution tests
    // observe and drive.
    var systemAlertQueryCalls = 0
    var queryElementsCalls = 0
    var afterSystemAlertTap: (() -> Void)?
    var afterTap: (() -> Void)?
    // What `/app/state` reports (BE-0424). A live app by default, so every existing test keeps the
    // behavior it had before this route existed.
    var appRunState: AppRunState = .runningForeground
    // The notification banner `/notificationBanner/query` returns, empty when none is up (BE-0416).
    var notificationBanner: [ElementSnapshot] = []
    // What `enterApp`/`leaveApp` report, and the bundle ids `enterApp` was called with.
    // Kept apart from `tapResult` since these calls never resolve an element.
    var enterAppResult: AppActivationResult = .ok
    var leaveAppResult: AppActivationResult = .ok
    var enterAppCalls: [String] = []
    var leaveAppCalls = 0
    // The bundle ids `targetApp` was called with (BE-0447).
    var targetAppCalls: [String] = []

    func queryElements() -> [ElementSnapshot] {
        beforeQueryElements?()
        queryElementsCalls += 1
        if let exception = queryRaises { exception.raise() }
        return elementsToReturn
    }

    func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult {
        tapCalls.append((backingElement, taps, duration))
        if let exception = tapRaises {
            // Raised inside the marked touch when a ledger is set, as XCUITest raises mid-interaction.
            if let ledger { ledger.mark { exception.raise() } } else { exception.raise() }
        }
        touched(tapResult)
        afterTap?()
        return tapResult
    }

    func isHittable(backingElement: AnyObject) -> TapResult {
        isHittableCalls.append(backingElement)
        return isHittableResult
    }

    func tapPoint(x: Double, y: Double) -> TapResult {
        tapPointCalls.append((x, y))
        touched(tapResult)
        return tapResult
    }

    func gesture(backingElement: AnyObject, kind: String, scale: Double, radians: Double) -> TapResult {
        gestureCalls.append((backingElement, kind, scale, radians))
        for _ in 0..<gestureAttempts { touched(tapResult) }
        return tapResult
    }

    func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult {
        swipeCalls.append((fromX, fromY, toX, toY))
        touched(tapResult)
        return tapResult
    }

    func scroll(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult {
        scrollCalls.append((fromX, fromY, toX, toY))
        touched(tapResult)
        return tapResult
    }

    var screenSizeValue: (width: Double, height: Double) = (390, 844)

    func screenSize() -> (width: Double, height: Double) { screenSizeValue }

    func typeText(_ text: String) -> TapResult {
        typeCalls.append(text)
        return tapResult
    }

    func deleteText(count: Int) -> TapResult {
        deleteTextCalls.append(count)
        return tapResult
    }

    func selectAll() -> TapResult {
        selectAllCalls += 1
        return tapResult
    }

    func copySelection() -> TapResult {
        copyCalls += 1
        return tapResult
    }

    func setPickerValue(backingElement: AnyObject, value: String) -> TapResult {
        setPickerValueCalls.append((backingElement, value))
        touched(setPickerValueResult)
        return setPickerValueResult
    }

    func querySystemAlertButtons() -> [ElementSnapshot] {
        systemAlertQueryCalls += 1
        if let exception = systemAlertQueryRaises { exception.raise() }
        return systemAlertButtons
    }

    func tapSystemAlertButton(backingElement: AnyObject) -> TapResult {
        systemAlertTapCalls.append(backingElement)
        touched(tapResult)
        afterSystemAlertTap?()
        return tapResult
    }

    func queryNotificationBanner() -> [ElementSnapshot] {
        notificationBanner
    }

    func screenshot() -> Data? { screenshotData }

    func enterApp(bundleId: String) -> AppActivationResult {
        enterAppCalls.append(bundleId)
        return enterAppResult
    }

    func leaveApp() -> AppActivationResult {
        leaveAppCalls += 1
        return leaveAppResult
    }

    func targetApp(bundleId: String) {
        targetAppCalls.append(bundleId)
    }

    func appState() -> AppRunState { appRunState }

    /// Marks one synthesis unless *result* is one the real provider returns before touching anything.
    private func touched(_ result: TapResult) {
        switch result {
        case .ok, .valueNotFound: ledger?.mark {}
        case .stale, .notFound, .notHittable: break
        }
    }
}
