import SwiftUI

// BE-0019: the on-device realization of a two-finger gesture screen. Pinch and rotate are the one
// class of actuation the retired idb backend could not perform (it was single-touch and raised UnsupportedAction, BE-0290), so they
// are the honest proof that the XCUITest actuator drives elements end to end. Each target flips its
// mirrored a11y value once its gesture is recognized, so the `gestures (xcuitest)` run can assert
// the actuation landed. Reached only when the SHOWCASE_GESTURES launch env is set (see AppModel), so
// the normal observe-only app (BE-0079) never renders it. The screen is a flat VStack — no scroll —
// so both targets are always in the accessibility tree; XCUITest's swipe-to-scroll on a deep Form
// row is unreliable, and a two-finger target must be fully on-screen for its touch points anyway.
struct GestureView: View {
    @State private var pinched = false
    @State private var rotated = false

    var body: some View {
        VStack(spacing: 24) {
            // A generous, opaque hit area (as in ConformanceView): XCUITest drives a pinch/rotate as
            // two touch points that need real room, and the gesture degenerates on an
            // intrinsically-sized view. `contentShape` makes the whole frame hittable.
            Text("Pinch me")
                .frame(width: 280, height: 120)
                .background(Color.gray.opacity(0.25))
                .contentShape(Rectangle())
                .gesture(MagnifyGesture().onChanged { _ in pinched = true })
                .accessibilityID("log.pinch")
            Text(pinched ? "pinched" : "idle")
                .foregroundStyle(.secondary)
                .accessibilityID("log.pinch.value")
                .accessibilityStateValue(pinched ? "pinched" : "idle")

            Text("Rotate me")
                .frame(width: 280, height: 120)
                .background(Color.gray.opacity(0.25))
                .contentShape(Rectangle())
                .gesture(RotateGesture().onChanged { _ in rotated = true })
                .accessibilityID("log.rotate")
            Text(rotated ? "rotated" : "idle")
                .foregroundStyle(.secondary)
                .accessibilityID("log.rotate.value")
                .accessibilityStateValue(rotated ? "rotated" : "idle")
        }
    }
}
