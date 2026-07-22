import SwiftUI
import UIKit  // UIApplication / UIResponder — dismiss iOS transient UI on a screen reseed (see onChange)

// BE-0114: the on-device realization of a driver-conformance screen. The conformance suite seeds
// an arbitrary set of accessibility identifiers — duplicated (an ambiguous selector), empty (a
// zero-match), or unique — and each becomes one tappable, gestureable element carrying that
// identifier. That lets the XCUITest backend be driven through the *same* backend-agnostic
// contract as FakeDriver and Playwright, against each driver's real query / act code rather than
// the shared base alone. Reached only when the SHOWCASE_CONFORMANCE launch env is set (see
// AppModel), so the normal observe-only app (BE-0079) never renders it; the suite reseeds a new
// screen by writing the spec file AppModel polls.
struct ConformanceView: View {
    let identifiers: [String]

    /// A stable marker present on every conformance screen — including the empty (zero-match) one,
    /// which seeds no identifiers. The on-device harness waits on it to confirm the app is actually
    /// in conformance mode, rather than inferring it from the absence of ids (which a transient,
    /// near-empty a11y tree could satisfy too early).
    static let readyID = "conformance.ready"

    /// The always-present editable field the text-editing and `tap_point` contract invariants act on
    /// (BE-0280) — mirrors the web `_render` field and the Compose `CONFORMANCE_FIELD_ID`. Present on
    /// every conformance screen like the marker (not seeded per screen), with a fixed frame so the
    /// coordinate tap has a known center to aim at.
    static let fieldID = "conformance.field"

    /// Backs the editable field. The empty placeholder is deliberate: an iOS text field reports its
    /// placeholder as the accessibility value when empty, which would make the field read non-empty
    /// before any typing and hide the round-trip length change the contract observes.
    @State private var fieldText = ""

    /// Focus on the editable field, so a screen reseed can resign it and dismiss any transient iOS UI
    /// the previous text-editing test left up (BE-0280) — see the `onChange` teardown below.
    @FocusState private var fieldFocused: Bool

    var body: some View {
        // Duplicates are the point (the ambiguous-selector case), so the row identity is the
        // position, never the identifier — a `\.self` id would collapse repeated identifiers.
        VStack(spacing: 8) {
            Text("ready").accessibilityID(Self.readyID)
            // The editable field, always present so the text-editing / tap_point invariants have a
            // real field on every screen. A fixed frame gives the coordinate tap a known center.
            TextField("", text: $fieldText)
                .textFieldStyle(.roundedBorder)
                .frame(width: 280, height: 44)
                .focused($fieldFocused)
                .accessibilityID(Self.fieldID)
                // No explicit `.accessibilityValue(fieldText)`: a SwiftUI TextField already surfaces
                // its bound text as its accessibility value natively (as SearchView's field does), so
                // the XCUITest `query()` reads back the round-trip length change the contract
                // observes. An explicit `.accessibilityValue` bound to the per-keystroke `fieldText`
                // made SwiftUI re-create the field's accessibility element on every change, so a
                // handle resolved just before a keystroke went stale under the element — the contract's
                // text-selection flow then failed with a stale handle (BE-0280).
            ForEach(Array(identifiers.enumerated()), id: \.offset) { _, identifier in
                // A generous, opaque hit area: the conformance contract pinches/rotates one of these
                // (the MULTI_TOUCH case), and XCUITest's two-finger gestures need real room between
                // touch points — on an intrinsically-sized text button they degenerate and crash the
                // runner. `contentShape` makes the whole frame hittable for the tap cases too.
                Button(identifier) {}
                    .frame(width: 280, height: 90)
                    .background(Color.gray.opacity(0.25))
                    .contentShape(Rectangle())
                    .accessibilityID(identifier)
            }
        }
        // A reseed (the suite writing a new spec) means the previous screen's test is done. A
        // text-editing test leaves iOS transient UI up — the keyboard, and after select-all/copy the
        // system edit menu (a `PopoverDismissRegion` backdrop) — that floats *above* this view, so
        // re-rendering the content alone does not clear it; it would obscure the next screen's marker,
        // and the reseed readiness probe would see only `PopoverDismissRegion` (BE-0280). Resigning the
        // field and ending editing app-wide dismisses both, deterministically, at the screen boundary —
        // no fixed sleep, and the backend-agnostic contract stays free of any iOS-specific teardown.
        .onChange(of: identifiers) {
            fieldFocused = false
            UIApplication.shared.sendAction(
                #selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil
            )
        }
    }
}
