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
                    .accessibilityIdentifier("async.title")

                VStack(alignment: .leading) {
                    Button("Start") { startProgress() }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("async.startProgress")
                    ProgressView(value: progress)
                        .accessibilityIdentifier("async.progress")
                    Text("Progress: \(Int(progress * 100))")
                        .accessibilityIdentifier("async.progress.value")
                        .accessibilityValue("\(Int(progress * 100))")
                    if progress >= 1 {
                        Text("Complete").accessibilityIdentifier("async.progress.done")
                    }
                }

                VStack(alignment: .leading) {
                    Button("Load (fails)") { load(succeed: false) }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("async.loadFail")
                    if loadState == "error" {
                        Text("Failed")
                            .foregroundStyle(.red)
                            .accessibilityIdentifier("async.error")
                        Button("Retry") { load(succeed: true) }
                            .accessibilityIdentifier("async.retry")
                    }
                    if loadState == "loaded" {
                        Text("Loaded").accessibilityIdentifier("async.loaded")
                    }
                }

                VStack(alignment: .leading) {
                    TextField("Search", text: $search)
                        .textFieldStyle(.roundedBorder)
                        .keyboardType(.asciiCapable)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .accessibilityIdentifier("async.search")
                        .onChange(of: search) { _, newValue in scheduleDebounce(newValue) }
                    // Appears only once the debounce fires, so a `wait for` reliably
                    // bridges the delay rather than racing the still-empty value.
                    if !debounced.isEmpty {
                        Text("Debounced: \(debounced)")
                            .accessibilityIdentifier("async.debounced.value")
                            .accessibilityValue(debounced)
                    }
                }

                VStack(alignment: .leading) {
                    Button("Load more") { rows += 3 }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("async.loadMore")
                    Text("Rows: \(rows)")
                        .accessibilityIdentifier("async.count")
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
