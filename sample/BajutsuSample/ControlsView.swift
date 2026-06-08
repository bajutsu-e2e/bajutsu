import SwiftUI

// A gallery of the standard value controls. Each control mirrors its current
// state into a sibling result label's accessibilityValue, so headless backends
// (e.g. idb) that don't surface a control's own value can still assert the
// outcome — the same trick settings.normalizeToggle uses.
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
                    .accessibilityIdentifier("ctrl.title")

                // The label is hidden so the Toggle's accessibility element is just
                // the switch — a coordinate backend (idb) taps its center and flips it,
                // rather than landing on a full-width row whose center is the label.
                VStack(alignment: .leading) {
                    HStack {
                        Text("Toggle")
                        Spacer()
                        Toggle("Toggle", isOn: $toggleOn)
                            .labelsHidden()
                            .accessibilityIdentifier("ctrl.toggle")
                    }
                    Text("Toggle: \(toggleOn ? "on" : "off")")
                        .accessibilityIdentifier("ctrl.toggle.value")
                        .accessibilityValue(toggleOn ? "on" : "off")
                }

                VStack(alignment: .leading) {
                    Stepper("Stepper", value: $stepperValue, in: 0 ... 10)
                        .accessibilityIdentifier("ctrl.stepper")
                    Text("Stepper: \(stepperValue)")
                        .accessibilityIdentifier("ctrl.stepper.value")
                        .accessibilityValue("\(stepperValue)")
                }

                // Stepped so the mirrored value is deterministic regardless of the
                // exact drag distance a backend produces.
                VStack(alignment: .leading) {
                    Slider(value: $sliderValue, in: 0 ... 10, step: 1)
                        .accessibilityIdentifier("ctrl.slider")
                    Text("Slider: \(Int(sliderValue))")
                        .accessibilityIdentifier("ctrl.slider.value")
                        .accessibilityValue("\(Int(sliderValue))")
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
                                .accessibilityIdentifier("ctrl.segment.\(segments[i].lowercased())")
                        }
                    }
                    Text("Segment: \(segments[segment])")
                        .accessibilityIdentifier("ctrl.segment.value")
                        .accessibilityValue(segments[segment])
                }

                // A Menu renders its items in a system popover; they are addressed by
                // label (like an alert button), not by identifier.
                VStack(alignment: .leading) {
                    Menu("Menu") {
                        Button("Apple") { menuChoice = "Apple" }
                        Button("Banana") { menuChoice = "Banana" }
                    }
                    .accessibilityIdentifier("ctrl.menu")
                    Text("Menu: \(menuChoice)")
                        .accessibilityIdentifier("ctrl.menu.value")
                        .accessibilityValue(menuChoice)
                }

                // One enabled button (counts taps) and one permanently disabled, to
                // exercise the enabled / disabled assertions.
                VStack(alignment: .leading) {
                    Button("Tap") { tapCount += 1 }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("ctrl.button")
                    Button("Disabled") {}
                        .buttonStyle(.bordered)
                        .disabled(true)
                        .accessibilityIdentifier("ctrl.buttonDisabled")
                    Text("Taps: \(tapCount)")
                        .accessibilityIdentifier("ctrl.button.value")
                        .accessibilityValue("\(tapCount)")
                }
            }
            .padding()
        }
    }
}
