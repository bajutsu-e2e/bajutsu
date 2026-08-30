import SwiftUI
import UIKit

// SPEC §8: the single place identifiers (and state-mirroring values) enter the tree.
// Both are gated on ACCESSIBLE so the -noax build compiles to a tree with neither —
// the honest "we skipped accessibility" app that `record` must cope with.
extension View {
    /// Attach a stable accessibility identifier in the a11y build; no-op otherwise.
    /// Named to echo SwiftUI's own `.accessibilityIdentifier(_:)` without shadowing it.
    func accessibilityID(_ id: String) -> some View {
        #if ACCESSIBLE
        return AnyView(self.accessibilityIdentifier(id))
        #else
        return AnyView(self)
        #endif
    }

    /// Mirror state into `accessibilityValue` in the a11y build so assertions can read it;
    /// no-op otherwise (the -noax tree exposes no mirrored values).
    func accessibilityStateValue(_ value: String) -> some View {
        #if ACCESSIBLE
        return AnyView(self.accessibilityValue(value))
        #else
        return AnyView(self)
        #endif
    }
}

// The same two helpers for the UIKit views this app hosts. `SignInView` wraps a plain UIKit sign-in
// controller because Password AutoFill did not engage for the same content types inside a SwiftUI
// `Form`, so its labels and fields need the UIKit form of these — gated on ACCESSIBLE exactly as the
// SwiftUI ones are, so the -noax twin still carries neither.
extension UIAccessibilityIdentification {
    @discardableResult func accessibilityID(_ id: String) -> Self {
        #if ACCESSIBLE
        accessibilityIdentifier = id
        #endif
        return self
    }
}

extension UIView {
    func accessibilityStateValue(_ value: String?) {
        #if ACCESSIBLE
        accessibilityValue = value
        #endif
    }
}
