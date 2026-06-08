import SwiftUI

// Network demo: a Fetch button makes a real GET request. BajutsuKit (active when
// BAJUTSU_COLLECTOR is set) captures the exchange and POSTs it to bajutsu's collector,
// where a `request` assertion can check it. The status is mirrored to an a11y value so
// the scenario can wait for the response before asserting.
struct NetworkView: View {
    @State private var status = "idle"

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
            }
            .padding()
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
