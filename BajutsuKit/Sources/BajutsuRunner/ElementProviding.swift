import Foundation

/// A single element snapshot returned by the provider.
public struct ElementSnapshot {
    public let identifier: String?
    public let label: String?
    public let value: String?
    public let traits: [String]
    public let frame: (x: Double, y: Double, width: Double, height: Double)
    /// Opaque reference the provider uses to act on this element later.
    public let backingElement: AnyObject

    public init(
        identifier: String?,
        label: String?,
        value: String?,
        traits: [String],
        frame: (x: Double, y: Double, width: Double, height: Double),
        backingElement: AnyObject
    ) {
        self.identifier = identifier
        self.label = label
        self.value = value
        self.traits = traits
        self.frame = frame
        self.backingElement = backingElement
    }
}

/// The result of a tap attempt, or of an `isHittable` query reusing the same resolution outcomes.
public enum TapResult {
    case ok
    case stale
    case notFound
    /// Resolved to a live element, but it is not reachable at its own point right now (covered by
    /// another on-screen element, or the platform's own hit-test refused it).
    case notHittable
    /// Resolved to a live element and acted on it, but the requested value never became the
    /// element's own value — a picker wheel with no such row (BE-0356). Its own case rather than a
    /// reuse of `.notFound`, whose "no actuatable element" meaning would misreport a wheel that
    /// resolved perfectly well and simply does not carry the value.
    case valueNotFound
}

/// Abstraction over XCUITest element access. The library never imports XCTest;
/// the real implementation is provided by the consuming UI test target.
public protocol ElementProviding: AnyObject {
    /// Return a snapshot of all on-screen elements. Called on the main thread.
    func queryElements() -> [ElementSnapshot]

    /// The device screen size in points, for the `scroll` action's on-screen stop condition
    /// (BE-0326). The flattened element tree excludes the app window, so the true viewport cannot be
    /// inferred from it. Called on the main thread.
    func screenSize() -> (width: Double, height: Double)

    /// Tap the element identified by its backing reference.
    func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult

    /// Whether the element identified by its backing reference is reachable at its own point right
    /// now, without acting on it. `.ok` means hittable, `.notHittable` means resolved but covered
    /// (or otherwise unreachable); `.stale` / `.notFound` mirror `tap`'s own resolution outcomes, so
    /// a caller distinguishes "covered" from "the handle no longer resolves" the same way it already
    /// does for a tap attempt.
    func isHittable(backingElement: AnyObject) -> TapResult

    /// Tap a raw screen coordinate.
    func tapPoint(x: Double, y: Double) -> TapResult

    /// Perform a two-finger gesture (pinch or rotate) on the element.
    func gesture(backingElement: AnyObject, kind: String, scale: Double, radians: Double) -> TapResult

    /// Swipe between two screen coordinates.
    func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult

    /// Scroll (a non-inertial drag) between two screen coordinates (BE-0326). Unlike `swipe`, the
    /// drag holds at its end before lifting, so the scroll view settles where the gesture left it
    /// rather than flinging past the target with momentum — the contract the `scroll` action's
    /// bounded re-query loop relies on.
    func scroll(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult

    /// Type text into the focused element.
    func typeText(_ text: String) -> TapResult

    /// Delete `count` characters from the end of the focused field (backspace; BE-0265).
    func deleteText(count: Int) -> TapResult

    /// Select the whole content of the focused field (BE-0265).
    func selectAll() -> TapResult

    /// Copy the active selection to the clipboard (BE-0265).
    func copySelection() -> TapResult

    /// Move the picker wheel identified by its backing reference to the row whose value is `value`
    /// (BE-0356), reporting `.valueNotFound` when the wheel never shows it.
    ///
    /// The detection cannot be left to the platform: `adjust(toPickerWheelValue:)` neither throws
    /// nor returns anything, and records a missed value as a soft `XCTIssue` that the resident
    /// runner's `continueAfterFailure` deliberately tolerates. An implementation must therefore read
    /// the wheel's resulting value back and compare it itself.
    func setPickerValue(backingElement: AnyObject, value: String) -> TapResult

    /// Snapshot the buttons of a presented iOS SpringBoard system alert (a permission prompt),
    /// empty when none is up (BE-0316). The alert is out of the app's process, so this queries a
    /// second, on-demand SpringBoard handle rather than the app under test.
    func querySystemAlertButtons() -> [ElementSnapshot]

    /// Tap the SpringBoard alert button identified by its backing reference (BE-0316).
    func tapSystemAlertButton(backingElement: AnyObject) -> TapResult

    /// Capture a screenshot as PNG data.
    func screenshot() -> Data?
}
