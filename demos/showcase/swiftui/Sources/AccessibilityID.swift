import SwiftUI

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
