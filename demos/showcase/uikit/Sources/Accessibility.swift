import UIKit

// SPEC §8 — the single place identifiers are applied. ACCESSIBLE is set on the
// a11y target only; on the -noax twin aid(...) compiles to a no-op so the tree
// carries no identifiers at all.
extension UIAccessibilityIdentification {
    /// Set a stable accessibility identifier in the a11y build; no-op otherwise.
    @discardableResult func aid(_ id: String) -> Self {
        #if ACCESSIBLE
        accessibilityIdentifier = id
        #endif
        return self
    }
}

extension UIView {
    /// Mirror state into accessibilityValue for assertions — a11y build only, so the
    /// -noax tree exposes no mirrored values either (SPEC §8).
    func mirror(value: String?) {
        #if ACCESSIBLE
        accessibilityValue = value
        #endif
    }
}

extension UIBarItem {
    /// UIBarItem (tab/bar button items) exposes accessibilityValue but is not a UIView.
    func mirror(value: String?) {
        #if ACCESSIBLE
        accessibilityValue = value
        #endif
    }
}

extension UIAlertAction {
    /// UIAlertAction does not conform to UIAccessibilityIdentification and has no public
    /// identifier API, but its private `accessibilityIdentifier` is the only way to give an
    /// action-sheet button a stable id — set it via KVC, gated to the a11y build (SPEC §5.3).
    @discardableResult func aid(_ id: String) -> Self {
        #if ACCESSIBLE
        setValue(id, forKey: "accessibilityIdentifier")
        #endif
        return self
    }
}
