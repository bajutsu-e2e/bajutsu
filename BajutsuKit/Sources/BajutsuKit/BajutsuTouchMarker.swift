import Foundation

// The state behind bajutsu's in-app touch visualization, with no UIKit in it. `BajutsuTouch` owns
// the `UIWindow.sendEvent(_:)` hook and the layers; this file owns the question of *what* should be
// on screen after each event. The Swift lane builds the package for macOS, where UIKit does not
// exist, so anything importing UIKit is invisible to `swift test` — keeping the rule here is what
// makes it testable at all.

/// A touch phase, mirroring the `UITouch.Phase` cases the visualization reacts to.
public enum BajutsuTouchPhase {
    case began
    case moved
    case stationary
    case ended
    case cancelled
}

/// A contact point in the window's coordinate space, in points.
public struct BajutsuTouchPoint: Equatable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }

    func distance(to other: BajutsuTouchPoint) -> Double {
        ((x - other.x) * (x - other.x) + (y - other.y) * (y - other.y)).squareRoot()
    }
}

/// One touch's visible marks: where its contact is now, and the route it has travelled.
public struct BajutsuTouchMark: Equatable {
    /// The contact's current location, which is also its final location once it has lifted.
    public var point: BajutsuTouchPoint
    /// Every recorded location in order, starting at the contact's first. A swipe's trail is what
    /// matches the movement path Android's `pointer_location` setting draws.
    public var trail: [BajutsuTouchPoint]
    /// False once the touch has lifted. The mark stays visible either way, until a new gesture
    /// clears it.
    public var isActive: Bool
}

/// Tuning shared by the model and the renderer.
public enum BajutsuTouchMarker {
    /// Radius of the contact circle, in points, giving the 40-point diameter the technique uses.
    public static let radius: Double = 20
    /// A `.moved` this close to the previous trail point records nothing. UIKit delivers moves far
    /// more finely than a drawn route needs, so the filter bounds a trail's density without
    /// shortening the route it shows.
    static let minimumTrailSpacing: Double = 2
    /// Hard ceiling on one trail, past which the oldest point is dropped. No real gesture comes
    /// near it; the ceiling exists because the hook also runs under the crawl and a long session,
    /// where an unbounded trail would grow without limit.
    static let maximumTrailPoints = 2048
}

/// Tracks each touch's marks and decides when a new gesture clears the previous one's.
///
/// A gesture's marks are cleared by the next gesture and by nothing else: no timer, no fade, no
/// duration constant, so the visualization has no timing behavior that could differ between a fast
/// workstation and a loaded CI runner. A `.began` arriving while no other touch is active starts a
/// new gesture and clears the previous one's marks; a `.began` arriving while a touch is still down
/// joins the gesture in progress and clears nothing, which is what keeps a pinch's second finger
/// from erasing the first. The marks of the gesture a step performed are therefore still on screen
/// when the step ends, which is what puts them in that step's screenshot.
///
/// Generic over the touch identity so the model stays free of UIKit: the hook instantiates it with
/// `ObjectIdentifier`, taken from the `UITouch`, and a test instantiates it with anything `Hashable`.
public struct BajutsuTouchModel<ID: Hashable> {
    private var marks: [ID: BajutsuTouchMark] = [:]
    /// Identities in the order their touches began, so the renderer and the tests both see a
    /// deterministic order rather than a dictionary's.
    private var order: [ID] = []

    public init() {}

    /// True while at least one touch has not yet lifted.
    public var hasActiveTouch: Bool {
        marks.values.contains { $0.isActive }
    }

    /// Every mark that should currently be on screen, oldest contact first.
    public var visibleMarks: [(id: ID, mark: BajutsuTouchMark)] {
        order.compactMap { id in marks[id].map { (id, $0) } }
    }

    public func mark(for id: ID) -> BajutsuTouchMark? {
        marks[id]
    }

    /// Fold one touch into the model, returning the identities whose marks the caller must now
    /// remove from the screen. The return is non-empty only when a new gesture displaced an old
    /// one, so a renderer can treat it as its complete removal list.
    @discardableResult
    public mutating func apply(
        id: ID, phase: BajutsuTouchPhase, at point: BajutsuTouchPoint
    ) -> [ID] {
        switch phase {
        case .began:
            let cleared = hasActiveTouch ? [] : clearAll()
            if marks[id] == nil {
                order.append(id)
            }
            marks[id] = BajutsuTouchMark(point: point, trail: [point], isActive: true)
            return cleared
        case .moved:
            guard var mark = marks[id] else { return [] }
            mark.point = point
            append(point, to: &mark)
            marks[id] = mark
            return []
        case .stationary:
            return []
        case .ended, .cancelled:
            guard var mark = marks[id] else { return [] }
            mark.point = point
            append(point, to: &mark)
            mark.isActive = false
            marks[id] = mark
            return []
        }
    }

    /// Drop every mark, as a teardown would. Returns what the renderer must remove.
    @discardableResult
    public mutating func clearAll() -> [ID] {
        let cleared = order
        marks.removeAll()
        order.removeAll()
        return cleared
    }

    private func append(_ point: BajutsuTouchPoint, to mark: inout BajutsuTouchMark) {
        if let last = mark.trail.last, last.distance(to: point) < BajutsuTouchMarker.minimumTrailSpacing {
            return
        }
        mark.trail.append(point)
        if mark.trail.count > BajutsuTouchMarker.maximumTrailPoints {
            mark.trail.removeFirst(mark.trail.count - BajutsuTouchMarker.maximumTrailPoints)
        }
    }
}
