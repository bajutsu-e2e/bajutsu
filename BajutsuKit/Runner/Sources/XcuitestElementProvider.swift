import XCTest
import BajutsuRunner

/// The concrete `ElementProviding` (BE-0019): the only XCTest-touching piece of the runner.
///
/// Walks `XCUIApplication` into the normalized `Element` shape the Python driver expects
/// (identifier / label / value / traits / frame, matching what the backend-agnostic `Element`
/// produces) and actuates the exact `XCUIElement` a snapshot handle maps back to.
///
/// BE-0105 makes the query cheap: instead of materializing an `XCUIElement` per node and reading
/// each attribute over its own XCUITest round-trip (elements × attributes ≈ 600 trips for one
/// screen), `queryElements()` takes **one** `app.snapshot()` and reads every attribute from that
/// tree. The trade-off is that snapshot nodes are values, not tappable elements, so each element's
/// backing records its identity and root-relative position path. `tap` / `gesture` re-derive the
/// live `XCUIElement` from that position path — one cheap resolution — and fall back to a narrow
/// identity query only when the path no longer resolves, which also reaches system-owned sheets
/// whose snapshot hierarchy cannot be replayed through live direct-child queries.
/// Backs a SpringBoard alert button by its ordinal within `springboard.alerts.buttons` (BE-0316).
///
/// The out-of-process alert is not part of the app snapshot the `PositionPathBacking` walk records,
/// so its buttons address by ordinal instead: `querySystemAlertButtons` reads them in order, and
/// `tapSystemAlertButton` re-derives the same live element by that ordinal. A permission prompt
/// carries a fixed, small set of buttons, so the ordinal is stable between the query and the tap.
private final class SystemAlertButtonBacking {
    let ordinal: Int
    init(ordinal: Int) { self.ordinal = ordinal }
}

final class XcuitestElementProvider: ElementProviding {
    private let app: XCUIApplication
    // A second, on-demand handle for SpringBoard — which owns the out-of-process permission prompt
    // (BE-0316) — built lazily so every other query and tap stays scoped to the app under test.
    private lazy var springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")

    init(app: XCUIApplication) {
        self.app = app
    }

    func queryElements() -> [ElementSnapshot] {
        // One accessibility round-trip for the whole attribute-bearing tree; the tabs a coordinate backend collapses
        // into an opaque "Tab Bar" group surface here as individual buttons, the point of the richer
        // actuator. A snapshot failure yields an empty screen rather than a crash — the run fails
        // loudly downstream when nothing resolves.
        guard let root = try? app.snapshot() else { return [] }
        return flattenSnapshot(root: SnapshotNodeAdapter(root))
    }

    func screenSize() -> (width: Double, height: Double) {
        // The application window's frame in points — the coordinate space the snapshot frames use — so
        // an on-screen element's frame center falls within it (BE-0326). The window fills the screen
        // and its frame is stable regardless of a scroll view's buffered off-screen children, unlike
        // the element tree's extent. (`XCUIScreen` has no frame/bounds; `app.frame` is the window.)
        let frame = app.frame
        return (Double(frame.width), Double(frame.height))
    }

    // `isHittable` reads `false` both for "covered" and for "offscreen" (Apple's own docs say so) —
    // but only the former is `tap` / `isHittable(backingElement:)`'s question; a not-yet-scrolled-to
    // target is a `scroll` question (docs/drivers.md), not this one. Tested on the same point a
    // following tap would actually land on (the frame's center), matching the web guard's own
    // point-based question (`_point_hits`, playwright.py) rather than a frame-overlap test:
    // `intersects` would still guard a target straddling the fold whose center has already scrolled
    // off, reaching `isHittable` for a question this check does not ask, and reading differently
    // from web for the identical screen. `app.frame` is the stable window/screen bounds
    // `screenSize()` above already uses for the same viewport-vs-content-extent distinction. A
    // single shared predicate, not one copy per caller, so `tap` and `isHittable(backingElement:)` —
    // which the recovery loop requires to agree — cannot drift apart on this question.
    private func centerIsOnScreen(_ el: XCUIElement) -> Bool {
        let f = el.frame
        return app.frame.contains(CGPoint(x: f.midX, y: f.midY))
    }

    func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult {
        guard let backing = backingElement as? PositionPathBacking else { return .notFound }
        guard let el = liveElement(for: backing) else { return .stale }
        if centerIsOnScreen(el) {
            guard el.isHittable else { return .notHittable }
        }
        if duration > 0 {
            el.press(forDuration: duration)
        } else if taps >= 2 {
            el.doubleTap()
        } else {
            el.tap()
        }
        return .ok
    }

    func isHittable(backingElement: AnyObject) -> TapResult {
        guard let backing = backingElement as? PositionPathBacking else { return .notFound }
        guard let el = liveElement(for: backing) else { return .stale }
        guard centerIsOnScreen(el) else { return .ok }
        return el.isHittable ? .ok : .notHittable
    }

    func tapPoint(x: Double, y: Double) -> TapResult {
        coordinate(x, y).tap()
        return .ok
    }

    func gesture(backingElement: AnyObject, kind: String, scale: Double, radians: Double) -> TapResult {
        guard let backing = backingElement as? PositionPathBacking else { return .notFound }
        guard let el = liveElement(for: backing) else { return .stale }
        let actuate: () -> Void
        switch kind {
        case "pinch":
            // velocity sign must match the scale direction (zoom in vs out) or XCUITest rejects it.
            actuate = { el.pinch(withScale: CGFloat(scale), velocity: scale >= 1 ? 1 : -1) }
        case "rotate":
            actuate = { el.rotate(CGFloat(radians), withVelocity: 1) }
        default:
            return .notFound
        }
        // XCUITest occasionally synthesizes a two-finger gesture the Simulator drops before the app's
        // recognizer fires, leaving no effect and a red `expect`. Re-issuing helps only when a landing
        // is *observable*, because pinch and rotate are accumulating: re-applying one that already
        // landed zooms/rotates again. The single app-agnostic signal a landing left is the actuated
        // element's own value — and only an app that mirrors its gesture result there (as the showcase
        // GestureView does) exposes it; a plain image / map / scroll view does not. So the retry is
        // opt-in: a scenario whose target self-mirrors sets BAJUTSU_GESTURE_RETRY=1 in its launchEnv,
        // and the retry re-issues until `el.value` moves (nil = unreadable, never a landing). Without
        // the opt-in the gesture is actuated exactly once — the deterministic default for an arbitrary
        // app, where re-applying an accumulating gesture a drop-dependent number of times would make
        // the magnitude non-deterministic (prime directive 2).
        if RunnerServer.forwardedLaunchEnvironment["BAJUTSU_GESTURE_RETRY"] == "1" {
            actuateUntilStateChanges(
                maxAttempts: Self.maxGestureAttempts,
                signature: { el.value as? String },
                actuate: actuate
            )
        } else {
            actuate()
        }
        return .ok
    }

    /// The most times `gesture` re-issues a dropped pinch/rotate before giving up (a genuinely
    /// no-op gesture then still returns, and the run's `expect` fails loudly rather than looping).
    private static let maxGestureAttempts = 4

    func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult {
        coordinate(fromX, fromY).press(forDuration: 0.1, thenDragTo: coordinate(toX, toY))
        return .ok
    }

    /// How long the scroll drag holds stationary at its end before lifting. A hold near zero
    /// velocity settles the scroll view where the drag left it, so no momentum carries the content
    /// past the target after lift — the non-inertial contract (BE-0326).
    private static let scrollSettleDuration: TimeInterval = 0.3

    func scroll(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult {
        // Unlike `swipe` (a plain press-and-drag that lifts with residual velocity, so iOS flings the
        // scroll view onward), hold at the end before lifting so the release velocity is ~zero and
        // the content stops where the gesture ended.
        coordinate(fromX, fromY).press(
            forDuration: 0.1,
            thenDragTo: coordinate(toX, toY),
            withVelocity: .default,
            thenHoldForDuration: Self.scrollSettleDuration
        )
        return .ok
    }

    func typeText(_ text: String) -> TapResult {
        app.typeText(text)
        return .ok
    }

    func deleteText(count: Int) -> TapResult {
        // Type the delete key `count` times on the focused field; XCUITest maps `.delete` to a real
        // backspace, so this is agnostic to what the field held (BE-0265). The orchestrator focuses
        // the field first, so the deletes land in it.
        app.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: count))
        return .ok
    }

    func selectAll() -> TapResult {
        // Cmd+A selects the focused field's whole content — the hardware-keyboard shortcut the
        // Simulator honors (BE-0265).
        app.typeKey("a", modifierFlags: .command)
        return .ok
    }

    func copySelection() -> TapResult {
        // Cmd+C copies the active selection to the clipboard, read back by the `clipboard` assertion.
        app.typeKey("c", modifierFlags: .command)
        return .ok
    }

    func querySystemAlertButtons() -> [ElementSnapshot] {
        // Read the buttons of whatever SpringBoard alert is up, in order; empty when no alert is
        // present (`count` == 0), which the Python driver polls against the step's timeout. A
        // permission prompt has a couple of buttons, so reading each one's label/frame directly
        // (rather than one whole-tree snapshot) is cheap, and the ordinal is the tappable backing.
        let buttons = springboard.alerts.buttons
        let count = buttons.count
        return (0..<count).map { i in
            let button = buttons.element(boundBy: i)
            return ElementSnapshot(
                identifier: nil,
                label: nonEmpty(button.label),
                value: nil,
                traits: ["button"],  // base.Trait.BUTTON
                frame: frameTuple(button.frame),
                backingElement: SystemAlertButtonBacking(ordinal: i)
            )
        }
    }

    func tapSystemAlertButton(backingElement: AnyObject) -> TapResult {
        guard let backing = backingElement as? SystemAlertButtonBacking else { return .notFound }
        let button = springboard.alerts.buttons.element(boundBy: backing.ordinal)
        guard button.exists else { return .stale }  // the alert dismissed itself between query and tap
        button.tap()
        return .ok
    }

    func screenshot() -> Data? {
        app.screenshot().pngRepresentation
    }

    // MARK: - Helpers

    /// Recover the live `XCUIElement` for a snapshot backing, or nil if the screen no longer matches.
    ///
    /// The recorded position path is the primary path: it is a single element resolution, not a
    /// re-walk of the whole tree, so it keeps the per-interaction cost off the ~600-round-trip scale
    /// the class's one-`snapshot()` design (BE-0105) exists to avoid. When it resolves and the
    /// attributes still match, that element wins.
    ///
    /// A narrow flat query is the recovery step, reached only when the position path fails — the
    /// element moved, or its snapshot child indices cannot be replayed through a live hierarchy with
    /// different system-owned wrapper nodes, as happens for the iOS Save Password sheet and for a
    /// presented `UIAlertController`. The query must yield one identity match, or several that also
    /// agree on value and frame — XCUITest registers an alert's button twice at one place, and that
    /// pair is one control rather than an ambiguity. Matches that disagree on either, and anonymous
    /// elements, have no identity to recover by, so a position-path miss on them is a genuine stale.
    /// Value and frame are deliberately excluded from the recorded-against-live identity check
    /// (BE-0287); they decide only between candidates read from one live query.
    private func liveElement(for backing: PositionPathBacking) -> XCUIElement? {
        let el = element(at: backing.path)
        if el.exists, attributesMatch(
            recorded: backing.recorded, current: recordedAttributes(of: el, includingValue: false)
        ) {
            return el
        }
        return uniquelyIdentifiedElement(matching: backing.recorded)
    }

    /// Resolve one semantic identity without depending on snapshot hierarchy shape.
    private func uniquelyIdentifiedElement(matching recorded: RecordedAttributes) -> XCUIElement? {
        guard recorded.identifier != nil || recorded.label != nil else { return nil }
        var query = app.descendants(matching: .any)
        if let identifier = recorded.identifier {
            query = query.matching(identifier: identifier)
        }
        if let label = recorded.label {
            query = query.matching(NSPredicate(format: "label == %@", label))
        }
        let candidates = query.allElementsBoundByIndex.filter { $0.exists }
        let attributes = candidates.map { recordedAttributes(of: $0, includingValue: true) }
        guard let index = resolvableMatchingIndex(recorded: recorded, candidates: attributes) else {
            return nil
        }
        return candidates[index]
    }

    /// Read the identity fields shared by flat-query and position-path resolution.
    ///
    /// `value` is read for the flat-query group check alone (`resolvableMatchingIndex`), which needs the
    /// same fields the host's `_collapse_identical_duplicates` keys on; the recorded-against-live
    /// identity match ignores it. It is therefore read only where it is asked for: `liveElement(for:)`
    /// calls this on the position-path element on every actuation that resolves normally, and one more
    /// XCUITest round-trip on that path would buy nothing — the per-interaction cost this class's
    /// one-`snapshot()` design (BE-0105) exists to keep down. `includingValue` carries no default on
    /// purpose, so each caller states its choice and the compiler asks a new one: a candidate list
    /// built without it reports `value` as `nil` throughout, which reads to `resolvableMatchingIndex`
    /// as every candidate agreeing, collapsing the ambiguity the field was added to preserve. Unlike
    /// `identifier` and `label`, `value` is read straight off the optional rather than through
    /// `nonEmpty`: the host's key keeps an absent value and an empty one apart (`Router` drops the key
    /// entirely for `nil`, so Python sees `None` against `""`), and `flattenSnapshot` records it
    /// unnormalized as well. Normalizing here alone would collapse a pair those two keep apart.
    private func recordedAttributes(
        of el: XCUIElement, includingValue: Bool
    ) -> RecordedAttributes {
        RecordedAttributes(
            identifier: nonEmpty(el.identifier),
            label: nonEmpty(el.label),
            value: includingValue ? el.value as? String : nil,
            traits: traitTokens(
                elementType: el.elementType, isEnabled: el.isEnabled, isSelected: el.isSelected
            ),
            frame: frameTuple(el.frame)
        )
    }

    /// Resolve a root-relative index path back to an `XCUIElement` by descending direct children —
    /// the inverse of the position path `flattenSnapshot` records over `app.snapshot()`.
    private func element(at path: PositionPath) -> XCUIElement {
        path.reduce(app as XCUIElement) { parent, index in
            parent.children(matching: .any).element(boundBy: index)
        }
    }

    /// An absolute screen point as an `XCUICoordinate` (offset from the app's origin).
    private func coordinate(_ x: Double, _ y: Double) -> XCUICoordinate {
        app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
            .withOffset(CGVector(dx: x, dy: y))
    }
}

// MARK: - Snapshot bridging

/// Bridge XCTest's snapshot into the pure `SnapshotNode` the flatten walk consumes, so the whole tree
/// comes from a single `app.snapshot()` (BE-0105) with attributes normalized the same way the
/// per-element walk did. `XCUIElementSnapshot` is a protocol, not a concrete type, so it is wrapped
/// rather than conformed by extension.
private struct SnapshotNodeAdapter: SnapshotNode {
    private let snapshot: any XCUIElementSnapshot

    init(_ snapshot: any XCUIElementSnapshot) {
        self.snapshot = snapshot
    }

    var nodeIdentifier: String? { nonEmpty(snapshot.identifier) }
    var nodeLabel: String? { nonEmpty(snapshot.label) }
    var nodeValue: String? { snapshot.value as? String }
    var nodeTraits: [String] {
        traitTokens(
            elementType: snapshot.elementType, isEnabled: snapshot.isEnabled, isSelected: snapshot.isSelected
        )
    }
    var nodeFrame: (x: Double, y: Double, width: Double, height: Double) { frameTuple(snapshot.frame) }
    var nodeChildren: [SnapshotNode] { snapshot.children.map(SnapshotNodeAdapter.init) }
}

// MARK: - Attribute normalization (shared by the snapshot walk and the tap-time re-check)

private func nonEmpty(_ s: String) -> String? {
    s.isEmpty ? nil : s
}

private func frameTuple(_ f: CGRect) -> (x: Double, y: Double, width: Double, height: Double) {
    (Double(f.origin.x), Double(f.origin.y), Double(f.size.width), Double(f.size.height))
}

private func traitTokens(
    elementType: XCUIElement.ElementType, isEnabled: Bool, isSelected: Bool
) -> [String] {
    var out = [typeName(elementType)]
    if !isEnabled { out.append("notEnabled") }  // base.Trait.NOT_ENABLED
    if isSelected { out.append("selected") }  // base.Trait.SELECTED
    return out
}

/// Map `XCUIElement.ElementType` to the same lower-camel token the backend-agnostic trait vocabulary uses
/// (`AXButton` -> `button`), so a `traits:` selector resolves identically across backends.
private func typeName(_ t: XCUIElement.ElementType) -> String {
    switch t {
    case .button: return "button"
    case .staticText: return "staticText"
    case .cell: return "cell"
    case .tabBar: return "tabBar"
    case .navigationBar: return "navigationBar"
    case .toolbar: return "toolbar"
    case .image: return "image"
    case .textField: return "textField"
    case .secureTextField: return "secureTextField"
    case .searchField: return "searchField"
    case .textView: return "textView"
    case .switch: return "switch"
    case .link: return "link"
    case .slider: return "slider"
    case .table: return "table"
    case .collectionView: return "collectionView"
    case .scrollView: return "scrollView"
    case .alert: return "alert"
    case .sheet: return "sheet"
    case .pageIndicator: return "pageIndicator"
    case .segmentedControl: return "segmentedControl"
    case .picker: return "picker"
    case .pickerWheel: return "pickerWheel"
    case .keyboard: return "keyboard"
    case .other: return "other"
    default: return "other"
    }
}
