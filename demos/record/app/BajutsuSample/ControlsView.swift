import SwiftUI

// A gallery of the standard value controls. Each control mirrors its current
// state into a sibling result label's text (e.g. "Toggle: on"), so headless
// backends (e.g. idb) that don't surface a control's own value can still assert
// the outcome by reading that label — sample2 carries no accessibilityValue.
struct ControlsView: View {
    @State private var toggleOn = false
    @State private var stepperValue = 0
    @State private var sliderValue = 0.0
    @State private var segment = 0
    @State private var menuChoice = "None"
    @State private var tapCount = 0

    private let segments = ["One", "Two", "Three"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Controls")
                    .font(.title)

                // The label is hidden so the Toggle's accessibility element is just
                // the switch — a coordinate backend (idb) taps its center and flips it,
                // rather than landing on a full-width row whose center is the label.
                VStack(alignment: .leading) {
                    HStack {
                        Text("Toggle")
                        Spacer()
                        Toggle("Toggle", isOn: $toggleOn)
                            .labelsHidden()
                    }
                    Text("Toggle: \(toggleOn ? "on" : "off")")
                }

                VStack(alignment: .leading) {
                    Stepper("Stepper", value: $stepperValue, in: 0 ... 10)
                    Text("Stepper: \(stepperValue)")
                }

                // Stepped so the mirrored value is deterministic regardless of the
                // exact drag distance a backend produces.
                VStack(alignment: .leading) {
                    Slider(value: $sliderValue, in: 0 ... 10, step: 1)
                    Text("Slider: \(Int(sliderValue))")
                }

                // A single-select segment built from id'd buttons. A native
                // Picker(.segmented) renders as one TabGroup whose individual segments
                // idb does not surface as elements — so it can't be driven by id or
                // label headlessly. Per-segment buttons keep "pick one of N" drivable
                // by both backends (semantic on rocketsim, coordinate on idb).
                VStack(alignment: .leading) {
                    Text("Segment")
                    HStack(spacing: 8) {
                        ForEach(segments.indices, id: \.self) { i in
                            Button(segments[i]) { segment = i }
                                .buttonStyle(.bordered)
                                .tint(segment == i ? .accentColor : .gray)
                        }
                    }
                    Text("Segment: \(segments[segment])")
                }

                // A Menu renders its items in a system popover; they are addressed by
                // label (like an alert button), not by identifier.
                VStack(alignment: .leading) {
                    Menu("Menu") {
                        Button("Apple") { menuChoice = "Apple" }
                        Button("Banana") { menuChoice = "Banana" }
                    }
                    Text("Menu: \(menuChoice)")
                }

                // One enabled button (counts taps) and one permanently disabled, to
                // exercise the enabled / disabled assertions.
                VStack(alignment: .leading) {
                    Button("Tap") { tapCount += 1 }
                        .buttonStyle(.borderedProminent)
                    Button("Disabled") {}
                        .buttonStyle(.bordered)
                        .disabled(true)
                    Text("Taps: \(tapCount)")
                }
            }
            .padding()
        }
    }
}
