import SwiftUI

// BE-0114: the on-device realization of a driver-conformance screen. The conformance suite seeds
// an arbitrary set of accessibility identifiers — duplicated (an ambiguous selector), empty (a
// zero-match), or unique — and each becomes one tappable, gestureable element carrying that
// identifier. That lets the idb and XCUITest backends be driven through the *same* backend-agnostic
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

    var body: some View {
        // Duplicates are the point (the ambiguous-selector case), so the row identity is the
        // position, never the identifier — a `\.self` id would collapse repeated identifiers.
        VStack(spacing: 8) {
            Text("ready").accessibilityID(Self.readyID)
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
    }
}
