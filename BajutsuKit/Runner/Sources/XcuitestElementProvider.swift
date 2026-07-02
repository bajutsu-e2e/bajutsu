import XCTest
import BajutsuRunner

/// The concrete `ElementProviding` (BE-0019): the only XCTest-touching piece of the runner.
///
/// Walks `XCUIApplication` into the normalized `Element` shape the Python driver expects
/// (identifier / label / value / traits / frame, matching what `bajutsu/drivers/idb.py`
/// produces) and actuates the exact `XCUIElement` a snapshot handle maps back to.
final class XcuitestElementProvider: ElementProviding {
    private let app: XCUIApplication

    init(app: XCUIApplication) {
        self.app = app
    }

    func queryElements() -> [ElementSnapshot] {
        // One flat walk of the tree; the tabs idb collapses into an opaque "Tab Bar" group
        // surface here as individual buttons, which is the whole point of the richer actuator.
        let all = app.descendants(matching: .any).allElementsBoundByIndex
        var out: [ElementSnapshot] = []
        out.reserveCapacity(all.count)
        for el in all where el.exists {
            let f = el.frame
            out.append(
                ElementSnapshot(
                    identifier: nonEmpty(el.identifier),
                    label: nonEmpty(el.label),
                    value: el.value as? String,
                    traits: traits(of: el),
                    frame: (
                        x: Double(f.origin.x),
                        y: Double(f.origin.y),
                        width: Double(f.size.width),
                        height: Double(f.size.height)
                    ),
                    backingElement: el
                )
            )
        }
        return out
    }

    func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult {
        guard let el = backingElement as? XCUIElement else { return .notFound }
        guard el.exists else { return .stale }
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
        guard let el = backingElement as? XCUIElement else { return .notFound }
        guard el.exists else { return .stale }
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

    func screenshot() -> Data? {
        app.screenshot().pngRepresentation
    }

    // MARK: - Helpers

    private func nonEmpty(_ s: String) -> String? {
        s.isEmpty ? nil : s
    }

    /// An absolute screen point as an `XCUICoordinate` (offset from the app's origin).
    private func coordinate(_ x: Double, _ y: Double) -> XCUICoordinate {
        app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
            .withOffset(CGVector(dx: x, dy: y))
    }

    private func traits(of el: XCUIElement) -> [String] {
        var out = [typeName(el.elementType)]
        if !el.isEnabled { out.append("notEnabled") }  // base.Trait.NOT_ENABLED
        if el.isSelected { out.append("selected") }  // base.Trait.SELECTED
        return out
    }

    /// Map `XCUIElement.ElementType` to the same lower-camel token idb derives from its AX type
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
}
