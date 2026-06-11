import SwiftUI

// Asynchronous state: a determinate progress bar, a fail -> retry -> success
// flow, a debounced search, and button-driven pagination. Delays are real
// Task.sleeps (not animation), so they survive SAMPLE_UITEST's animation disable
// and exercise condition waits (`wait for`).
struct AsyncView: View {
    @State private var progress = 0.0
    @State private var loadState = "idle"  // idle | loading | error | loaded
    @State private var search = ""
    @State private var debounced = ""
    @State private var debounceTask: Task<Void, Never>?
    @State private var rows = 3

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Async")
                    .font(.title)

                VStack(alignment: .leading) {
                    Button("Start") { startProgress() }
                        .buttonStyle(.borderedProminent)
                    ProgressView(value: progress)
                    Text("Progress: \(Int(progress * 100))")
                        .accessibilityValue("\(Int(progress * 100))")
                    if progress >= 1 {
                        Text("Complete")
                    }
                }

                VStack(alignment: .leading) {
                    Button("Load (fails)") { load(succeed: false) }
                        .buttonStyle(.bordered)
                    if loadState == "error" {
                        Text("Failed")
                            .foregroundStyle(.red)
                        Button("Retry") { load(succeed: true) }
                    }
                    if loadState == "loaded" {
                        Text("Loaded")
                    }
                }

                VStack(alignment: .leading) {
                    TextField("Search", text: $search)
                        .textFieldStyle(.roundedBorder)
                        .keyboardType(.asciiCapable)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .onChange(of: search) { _, newValue in scheduleDebounce(newValue) }
                    // Appears only once the debounce fires, so a `wait for` reliably
                    // bridges the delay rather than racing the still-empty value.
                    if !debounced.isEmpty {
                        Text("Debounced: \(debounced)")
                            .accessibilityValue(debounced)
                    }
                }

                VStack(alignment: .leading) {
                    Button("Load more") { rows += 3 }
                        .buttonStyle(.bordered)
                    Text("Rows: \(rows)")
                        .accessibilityValue("\(rows)")
                }
            }
            .padding()
        }
    }

    private func startProgress() {
        progress = 0
        Task { @MainActor in
            // Step by an exact fraction so the 10th tick lands on 1.0 — repeatedly
            // adding 0.1 accumulates float error and stops just shy of 1.0.
            for i in 1 ... 10 {
                try? await Task.sleep(for: .milliseconds(80))
                progress = Double(i) / 10.0
            }
        }
    }

    private func load(succeed: Bool) {
        loadState = "loading"
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(500))
            loadState = succeed ? "loaded" : "error"
        }
    }

    private func scheduleDebounce(_ value: String) {
        debounceTask?.cancel()
        debounceTask = Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(500))
            if !Task.isCancelled { debounced = value }
        }
    }
}
