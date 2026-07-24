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
/// backing is its root-relative position path; `tap` / `gesture` re-derive the live `XCUIElement`
/// from that path and re-verify its attributes, returning `.stale` if the screen has shifted under it.
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

    func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult {
        guard let backing = backingElement as? PositionPathBacking else { return .notFound }
        guard let el = liveElement(for: backing) else { return .stale }
        if duration > 0 {
            el.press(forDuration: duration)
        } else if taps >= 2 {
            el.doubleTap()
        } else {
            el.tap()
        }
        return .ok
    }

    func tapPoint(x: Double, y: Double) -> TapResult {
        coordinate(x, y).tap()
        return .ok
    }

    func gesture(backingElement: AnyObject, kind: String, scale: Double, radians: Double) -> TapResult {
        guard let backing = backingElement as? PositionPathBacking else { return .notFound }
        guard let el = liveElement(for: backing) else { return .stale }
        switch kind {
        case "pinch":
            // velocity sign must match the scale direction (zoom in vs out) or XCUITest rejects it.
            el.pinch(withScale: CGFloat(scale), velocity: scale >= 1 ? 1 : -1)
        case "rotate":
            el.rotate(CGFloat(radians), withVelocity: 1)
        default:
            return .notFound
        }
        return .ok
    }

    func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult {
        coordinate(fromX, fromY).press(forDuration: 0.1, thenDragTo: coordinate(toX, toY))
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

    /// Re-derive the live `XCUIElement` for a snapshot backing, or nil if the screen no longer matches.
    ///
    /// Walking the recorded index path is one element resolution, not a re-walk of the whole tree; the
    /// attribute re-check (`identifier` / `label` / `traits`) guards against a sibling reorder whenever a
    /// distinguishing identifier or label is present. Two elements that share the same
    /// `identifier` / `label` / `traits` — icon-only controls with no accessibility text, or reused
    /// table/collection cells with a generic label and no per-row identifier — are indistinguishable by
    /// `attributesMatch`, so a reorder among such siblings is invisible and they rely on the position path
    /// alone for identity. Frame is deliberately excluded (BE-0287): a snapshot taken while the UI is
    /// still settling can record a frame that legitimately shifts before the tap, causing a false stale.
    private func liveElement(for backing: PositionPathBacking) -> XCUIElement? {
        let el = element(at: backing.path)
        guard el.exists else { return nil }
        let current = RecordedAttributes(
            identifier: nonEmpty(el.identifier),
            label: nonEmpty(el.label),
            traits: traitTokens(elementType: el.elementType, isEnabled: el.isEnabled, isSelected: el.isSelected),
            frame: frameTuple(el.frame)
        )
        return attributesMatch(recorded: backing.recorded, current: current) ? el : nil
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
