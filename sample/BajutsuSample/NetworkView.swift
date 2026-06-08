import BajutsuKit
import SwiftUI

// Network demo: a Fetch button makes a real GET request. BajutsuKit (active when
// BAJUTSU_COLLECTOR is set) captures the exchange and POSTs it to bajutsu's collector,
// where a `request` assertion can check it. The status is mirrored to an a11y value so
// the scenario can wait for the response before asserting. The "Captured by BajutsuKit"
// section reads the exchange back out of BajutsuKit (its observable store), so the app
// shows exactly what the interceptor observed — distinct from its own `status` above.
struct NetworkView: View {
    @State private var status = "idle"
    @ObservedObject private var captures = BajutsuExchangeStore.shared

    private var apiURL: String {
        ProcessInfo.processInfo.environment["SAMPLE_API_URL"] ?? "https://example.com"
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Network")
                    .font(.title)
                    .accessibilityIdentifier("net.title")

                Button("Fetch") { fetch() }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("net.fetch")

                Text("Status: \(status)")
                    .accessibilityIdentifier("net.status")
                    .accessibilityValue(status)

                captured
            }
            .padding()
        }
    }

    // What BajutsuKit captured for the most recent request — read back from its store.
    @ViewBuilder private var captured: some View {
        Divider()
        Text("Captured by BajutsuKit")
            .font(.headline)
            .accessibilityIdentifier("net.captured.title")

        if let ex = captures.latest {
            VStack(alignment: .leading, spacing: 6) {
                Text("Method: \(ex.method)")
                    .accessibilityIdentifier("net.captured.method")
                    .accessibilityValue(ex.method)
                Text("Status: \(ex.status.map(String.init) ?? "—")")
                    .accessibilityIdentifier("net.captured.status")
                    .accessibilityValue(ex.status.map(String.init) ?? "")
                Text("Duration: \(Int(ex.durationMs.rounded())) ms")
                    .accessibilityIdentifier("net.captured.duration")
                Text(ex.url)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("net.captured.url")
                    .accessibilityValue(ex.url)
                if let err = ex.error {
                    Text("Error: \(err)")
                        .foregroundStyle(.red)
                        .accessibilityIdentifier("net.captured.error")
                }
            }
        } else {
            Text("No exchange captured yet")
                .foregroundStyle(.secondary)
                .accessibilityIdentifier("net.captured.empty")
        }
    }

    private func fetch() {
        status = "loading"
        guard let url = URL(string: apiURL) else { status = "bad-url"; return }
        URLSession.shared.dataTask(with: url) { _, response, error in
            Task { @MainActor in
                if let http = response as? HTTPURLResponse {
                    status = "\(http.statusCode)"
                } else {
                    status = error == nil ? "done" : "error"
                }
            }
        }.resume()
    }
}
