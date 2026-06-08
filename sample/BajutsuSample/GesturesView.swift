import SwiftUI

// Multi-touch + double-tap gestures. Each records a coarse, deterministic result
// (a count or a direction) into a `*.value` label. double-tap is drivable by idb
// (two taps); pinch / rotate need real multi-touch, so their on-device path is the
// generated XCUITest (pinch(withScale:) / rotate(_:)).
struct GesturesView: View {
    @State private var doubled = 0
    @State private var pinchDir = "none"
    @State private var rotateDir = "none"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Gestures")
                    .font(.title)
                    .accessibilityIdentifier("gest.title")

                VStack(alignment: .leading) {
                    Text("Double-tap me")
                        .padding()
                        .frame(maxWidth: .infinity, minHeight: 60)
                        .background(.blue.opacity(0.15))
                        .accessibilityIdentifier("gest.doubletap")
                        .onTapGesture(count: 2) { doubled += 1 }
                    Text("Double taps: \(doubled)")
                        .accessibilityIdentifier("gest.doubletap.value")
                        .accessibilityValue("\(doubled)")
                }

                VStack(alignment: .leading) {
                    Text("Pinch me")
                        .padding()
                        .frame(maxWidth: .infinity, minHeight: 80)
                        .background(.green.opacity(0.15))
                        .accessibilityIdentifier("gest.pinch")
                        .gesture(MagnifyGesture().onEnded { value in
                            pinchDir = value.magnification > 1 ? "in" : "out"
                        })
                    Text("Pinch: \(pinchDir)")
                        .accessibilityIdentifier("gest.pinch.value")
                        .accessibilityValue(pinchDir)
                }

                VStack(alignment: .leading) {
                    Text("Rotate me")
                        .padding()
                        .frame(maxWidth: .infinity, minHeight: 80)
                        .background(.orange.opacity(0.15))
                        .accessibilityIdentifier("gest.rotate")
                        .gesture(RotateGesture().onEnded { value in
                            rotateDir = value.rotation.radians >= 0 ? "cw" : "ccw"
                        })
                    Text("Rotate: \(rotateDir)")
                        .accessibilityIdentifier("gest.rotate.value")
                        .accessibilityValue(rotateDir)
                }
            }
            .padding()
        }
    }
}
