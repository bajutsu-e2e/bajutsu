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

/// The result of a tap attempt.
public enum TapResult {
    case ok
    case stale
    case notFound
}

/// Abstraction over XCUITest element access. The library never imports XCTest;
/// the real implementation is provided by the consuming UI test target.
public protocol ElementProviding: AnyObject {
    /// Return a snapshot of all on-screen elements. Called on the main thread.
    func queryElements() -> [ElementSnapshot]

    /// Tap the element identified by its backing reference.
    func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult

    /// Tap a raw screen coordinate.
    func tapPoint(x: Double, y: Double) -> TapResult

    /// Perform a two-finger gesture (pinch or rotate) on the element.
    func gesture(backingElement: AnyObject, kind: String, scale: Double, radians: Double) -> TapResult

    /// Swipe between two screen coordinates.
    func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult

    /// Type text into the focused element.
    func typeText(_ text: String) -> TapResult

    /// Capture a screenshot as PNG data.
    func screenshot() -> Data?
}
