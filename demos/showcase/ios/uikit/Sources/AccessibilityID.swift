import UIKit

// SPEC §8 — the single place identifiers (and state-mirroring values) are applied.
// ACCESSIBLE is set on the a11y target only; on the -noax twin these compile to no-ops
// so the tree carries no identifiers and no mirrored values at all.
extension UIAccessibilityIdentification {
    /// Set a stable accessibility identifier in the a11y build; no-op otherwise.
    /// Named to echo UIKit's `accessibilityIdentifier` property.
    @discardableResult func accessibilityID(_ id: String) -> Self {
        #if ACCESSIBLE
        accessibilityIdentifier = id
        #endif
        return self
    }
}

extension UIView {
    /// Mirror state into `accessibilityValue` for assertions — a11y build only, so the
    /// -noax tree exposes no mirrored values either (SPEC §8).
    func accessibilityStateValue(_ value: String?) {
        #if ACCESSIBLE
        accessibilityValue = value
        #endif
    }
}

extension UIAlertAction {
    /// `UIAlertAction` is not a `UIView` and its header never declares
    /// `UIAccessibilityIdentification`, but it does implement `setAccessibilityIdentifier:`,
    /// and the identifier reaches the alert button XCUITest sees. Set it through KVC, guarded so
    /// an SDK that ever drops the setter leaves the button unidentified rather than raising
    /// `NSUnknownKeyException`. There is no silent fallback: the a11y `alert.yaml` addresses both
    /// buttons by `id`, so that case fails the gating lane loudly. Only the `-noax` twin, which
    /// never carried an identifier, still addresses them by label + traits.
    func accessibilityID(_ id: String) {
        #if ACCESSIBLE
        guard responds(to: Selector(("setAccessibilityIdentifier:"))) else { return }
        setValue(id, forKey: "accessibilityIdentifier")
        #endif
    }
}

extension UIBarItem {
    /// UIBarItem (tab/bar button items) exposes accessibilityValue but is not a UIView.
    func accessibilityStateValue(_ value: String?) {
        #if ACCESSIBLE
        accessibilityValue = value
        #endif
    }
}
