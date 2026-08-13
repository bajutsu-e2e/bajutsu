import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// In-app touch visualization for bajutsu.
///
/// A run records the Simulator screen, and the recording shows every consequence of a gesture
/// without ever showing the gesture. This draws a marker at each touch the app under test actually
/// receives, so a touch appears in the recorded video and in the step's screenshot. The marker is
/// drawn from the `UIEvent` the app dequeues rather than from the coordinate the driver sent, which
/// is what makes it evidence that the touch was *delivered* — a driver-side record cannot tell a
/// mis-aimed tap from one that never reached the app.
///
/// **Mechanism (and why this one).** The published technique installs a `UIWindow` subclass that
/// overrides `sendEvent(_:)` and replaces the app's window with it. Swapping an app's window is a
/// change to the app's own source, which prime directive 3 keeps out of the tool, so this exchanges
/// the implementation of `-[UIWindow sendEvent:]` on the class instead — the same
/// `method_exchangeImplementations` idiom `BajutsuScreen` uses on `viewDidAppear`, covering every
/// window in the process and touching no app code. An app that subclasses `UIWindow` and overrides
/// `sendEvent(_:)` itself is still covered, because that override calls `super`.
///
/// **Why a `CALayer` and not a `UIView`.** bajutsu resolves every selector against the
/// accessibility hierarchy `app.snapshot()` returns, and prime directive 2 requires an ambiguous
/// selector to fail rather than act on the first match. XCUITest surfaces a plain non-accessible
/// container view as an `.other` element, so a marker view would change element counts and could
/// turn a scenario's unique selector into an ambiguous one. `CALayer` is not a `UIResponder`,
/// conforms to no accessibility protocol, and takes no part in touch delivery, so it cannot appear
/// in the tree and cannot swallow the gesture it draws.
///
/// **Test/debug only**, like `BajutsuNet`: the hook is installed only when `BAJUTSU_TOUCH_MARKERS`
/// is `1`, which `bajutsu run --touch-markers` sets on the app's launch environment.
public enum BajutsuTouch {
    /// The launch environment variable that turns the visualization on. Off on any other value,
    /// and on its absence, so an app that never sees it behaves exactly as it does today.
    static let activationKey = "BAJUTSU_TOUCH_MARKERS"

    private static var installed = false

    /// Install the touch hook if `BAJUTSU_TOUCH_MARKERS` is `1`. Called from
    /// `BajutsuNet.startIfEnabled()` *before* its collector/mocks guard: touch visualization needs
    /// neither a collector nor a mock rule, and a plain recorded run with no network features at
    /// all is the case it is for.
    public static func startIfEnabled(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        #if canImport(UIKit)
        guard !installed else { return }  // idempotent — a relaunch in-process calls this once
        guard environment[activationKey] == "1" else { return }
        installed = true
        UIWindow.bajutsu_installTouchHook()
        #endif
    }

    #if canImport(UIKit)
    /// The marks that should be on screen. The model owns the rule that a new gesture clears the
    /// previous one's marks; this type owns only the drawing.
    private static var model = BajutsuTouchModel<ObjectIdentifier>()
    /// The layers drawn for each live touch, keyed the same way as the model.
    private static var layers: [ObjectIdentifier: MarkLayers] = [:]

    /// A touch that is still down. Green reads as "happening now" and, more practically, is a hue
    /// an app's own chrome rarely spends on a control, so the mark stays picked out against it.
    private static let activeTint = UIColor.systemGreen
    /// A touch that has lifted. Deliberately not a system-UI blue: a tint the app under test is
    /// likely to use for its own controls is exactly the one a viewer cannot pick out of a frame.
    private static let restingTint = UIColor.systemRed

    private struct MarkLayers {
        let contact: CAShapeLayer
        let trail: CAShapeLayer

        func removeFromSuperlayer() {
            contact.removeFromSuperlayer()
            trail.removeFromSuperlayer()
        }
    }

    static func handle(_ event: UIEvent, in window: UIWindow) {
        // `sendEvent(_:)` is delivered on the main thread, which is what lets the model and the
        // layer table below go unsynchronized. Bail rather than race if that ever stops holding.
        guard Thread.isMainThread, event.type == .touches, let touches = event.allTouches else {
            // A hover event can omit the contacts that are down, and sweeping against it would end
            // a touch that is still held, so only a touch event is allowed to drive the sweep.
            return
        }
        // Recover from a touch whose end never reached us — a window torn down mid-gesture stops
        // receiving `sendEvent` entirely. Without this the model would hold that touch active
        // forever and never clear a gesture again. The set is the event's whole touch complement,
        // NOT this window's slice of it: the model is shared by every window, so sweeping against
        // one window's touches would end a live touch belonging to another (an in-process keyboard
        // window alone makes two windows ordinary), reintroducing the same latch across windows.
        model.endTouchesMissing(from: Set(touches.map(ObjectIdentifier.init)))
        for touch in touches {
            // A touch belongs to one window for its whole life, so this both keeps a two-window
            // app from drawing each touch twice and keeps every phase of a touch on one window.
            guard touch.window === window, let phase = phase(of: touch) else { continue }
            let location = touch.location(in: window)
            let identity = ObjectIdentifier(touch)
            let cleared = model.apply(
                id: identity,
                phase: phase,
                at: BajutsuTouchPoint(x: Double(location.x), y: Double(location.y))
            )
            for staleIdentity in cleared {
                layers.removeValue(forKey: staleIdentity)?.removeFromSuperlayer()
            }
            draw(identity, in: window)
        }
    }

    private static func phase(of touch: UITouch) -> BajutsuTouchPhase? {
        switch touch.phase {
        case .began: return .began
        case .moved: return .moved
        case .stationary: return .stationary
        case .ended: return .ended
        case .cancelled: return .cancelled
        // The hover and indirect-pointer region phases are not contacts, so they draw nothing.
        default: return nil
        }
    }

    private static func draw(_ identity: ObjectIdentifier, in window: UIWindow) {
        guard let mark = model.mark(for: identity) else { return }
        let marks = layers[identity] ?? makeLayers(in: window)
        layers[identity] = marks

        // Without this the layers animate toward each new position, so a marker would lag the
        // finger by Core Animation's default duration instead of tracking it.
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        marks.contact.position = CGPoint(x: mark.point.x, y: mark.point.y)
        // Green while the touch is down, red once it has lifted. Colour, not opacity alone, carries
        // the distinction: a still frame of the recording then says on its own whether the contact
        // it shows is the gesture happening at that moment or the one it left behind.
        let tint = mark.isActive ? Self.activeTint : Self.restingTint
        marks.contact.fillColor = tint.withAlphaComponent(0.6).cgColor
        marks.contact.strokeColor = tint.cgColor
        marks.trail.strokeColor = tint.withAlphaComponent(0.85).cgColor
        // The lifted value stays high because a step's screenshot only ever catches that state —
        // fading it far would leave every screenshot showing the faintest version of the mark.
        marks.contact.opacity = mark.isActive ? 1.0 : 0.7
        marks.trail.path = trailPath(mark.trail)
        CATransaction.commit()
    }

    private static func trailPath(_ points: [BajutsuTouchPoint]) -> CGPath? {
        guard let first = points.first, points.count > 1 else { return nil }
        let path = CGMutablePath()
        path.move(to: CGPoint(x: first.x, y: first.y))
        for point in points.dropFirst() {
            path.addLine(to: CGPoint(x: point.x, y: point.y))
        }
        return path
    }

    private static func makeLayers(in window: UIWindow) -> MarkLayers {
        let radius = CGFloat(BajutsuTouchMarker.radius)

        let contact = CAShapeLayer()
        contact.path = CGPath(
            ellipseIn: CGRect(x: -radius, y: -radius, width: radius * 2, height: radius * 2),
            transform: nil
        )
        // `draw` sets the fill and the trail's stroke on every event, so the values here only cover
        // the instant before the first one lands.
        contact.fillColor = restingTint.withAlphaComponent(0.6).cgColor
        contact.strokeColor = restingTint.cgColor
        contact.lineWidth = 1

        let trail = CAShapeLayer()
        trail.fillColor = nil
        trail.strokeColor = restingTint.withAlphaComponent(0.85).cgColor
        trail.lineWidth = 1
        trail.lineCap = .round
        trail.lineJoin = .round

        for layer in [trail, contact] {
            // Above whatever the app draws, without reordering the app's own layers.
            layer.zPosition = .greatestFiniteMagnitude
            window.layer.addSublayer(layer)
        }
        return MarkLayers(contact: contact, trail: trail)
    }
    #endif
}

#if canImport(UIKit)
extension UIWindow {
    /// Swizzle `sendEvent(_:)` so every touch the app receives is drawn, mirroring the
    /// `method_exchangeImplementations` idiom `BajutsuScreen` uses on `viewDidAppear`. Called once
    /// (guarded by `BajutsuTouch.installed`); a second call would swap the implementations back.
    static func bajutsu_installTouchHook() {
        guard
            let original = class_getInstanceMethod(self, #selector(UIWindow.sendEvent(_:))),
            let replacement = class_getInstanceMethod(self, #selector(UIWindow.bajutsu_sendEvent(_:)))
        else { return }
        method_exchangeImplementations(original, replacement)
    }

    @objc fileprivate func bajutsu_sendEvent(_ event: UIEvent) {
        // After the swizzle this calls the original `sendEvent`, so the app sees the event first
        // and the marker is drawn from what was actually delivered.
        self.bajutsu_sendEvent(event)
        BajutsuTouch.handle(event, in: self)
    }
}
#endif
