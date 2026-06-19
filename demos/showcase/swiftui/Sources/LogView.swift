import SwiftUI

// Training-log composer: exercises every input control and all four modal styles
// (sheet, fullScreenCover, confirmationDialog, auto-dismissing toast). In-app modals
// are visible to idb; the toast exercises `wait until gone`.
struct LogView: View {
    @EnvironmentObject var model: AppModel

    @State private var note = ""
    @State private var count = 1
    @State private var intense = false
    @State private var status = "idle"
    @State private var rows: [Int] = []

    // Modal state
    @State private var showSheet = false
    @State private var showCover = false
    @State private var showDialog = false
    @State private var dialogResult = "none"
    @State private var showToast = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Entry") {
                    TextField("Note", text: $note, axis: .vertical)
                        .lineLimit(3 ... 6)
                        .accessibilityID("log.note")

                    Stepper("Count: \(count)", value: $count, in: 0 ... 99)
                        .accessibilityID("log.count")
                    Text("Count: \(count)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("log.count.value")
                        .accessibilityStateValue(String(count))

                    Toggle("Intense", isOn: $intense)
                        .accessibilityID("log.intense")
                    Text(intense ? "Intense" : "Easy")
                        .foregroundStyle(.secondary)
                        .accessibilityID("log.intense.value")
                        .accessibilityStateValue(intense ? "on" : "off")

                    Button("Submit") { submit() }
                        .accessibilityID("log.submit")
                    Text("Status: \(status)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("log.status")
                        .accessibilityStateValue(status)
                }

                Section("Modals") {
                    Button("Open Filter") { showSheet = true }
                        .accessibilityID("log.openFilter")
                    Button("Open Gallery") { showCover = true }
                        .accessibilityID("log.openGallery")
                    Button("Open Delete") { showDialog = true }
                        .accessibilityID("log.openDelete")
                    Text("Dialog: \(dialogResult)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("log.dialog.value")
                        .accessibilityStateValue(dialogResult)
                }

                Section("Entries") {
                    ForEach(rows, id: \.self) { n in
                        Text("Entry \(n)")
                            .accessibilityID("log.row.\(n)")
                    }
                }
            }
            .navigationTitle("Log")
        }
        .accessibilityID("log.title")
        .overlay(alignment: .top) {
            if showToast {
                Text("Saved")
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.thinMaterial, in: Capsule())
                    .accessibilityID("log.toast")
            }
        }
        // Sheet with detents (SPEC §5.3).
        .sheet(isPresented: $showSheet) {
            VStack(spacing: 16) {
                Text("Filter")
                    .font(.title)
                    .accessibilityID("log.sheet.title")
                Button("Apply") { showSheet = false }
                    .buttonStyle(.borderedProminent)
                    .accessibilityID("log.sheet.apply")
                Button("Close") { showSheet = false }
                    .accessibilityID("log.sheet.close")
            }
            .padding()
            .presentationDetents([.medium, .large])
        }
        // Full-screen cover.
        .fullScreenCover(isPresented: $showCover) {
            VStack(spacing: 16) {
                Text("Gallery")
                    .font(.title)
                    .accessibilityID("log.cover.title")
                Button("Close") { showCover = false }
                    .accessibilityID("log.cover.close")
            }
            .padding()
        }
        // Action sheet (confirmationDialog); result mirrors to log.dialog.value.
        .confirmationDialog("Delete entry", isPresented: $showDialog, titleVisibility: .visible) {
            Button("Archive") { dialogResult = "archive" }
                .accessibilityID("log.dialog.archive")
            Button("Delete", role: .destructive) { dialogResult = "delete" }
                .accessibilityID("log.dialog.delete")
            Button("Cancel", role: .cancel) {}
                .accessibilityID("log.dialog.cancel")
        }
    }

    // POST SHOWCASE_HTTP_BASE/post with the note/count as JSON. Carries a secret header
    // (Authorization: Bearer …) and a password body field so redaction has something to
    // mask (SPEC §6). On success: toast (~1.2s auto-dismiss) and a new row.
    private func submit() {
        status = "loading"
        guard let url = URL(string: model.httpBase + "/post") else { status = "error"; return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer demo-secret-abc123", forHTTPHeaderField: "Authorization")
        let payload: [String: Any] = [
            "note": note, "count": count, "intense": intense, "password": "hunter2",
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        URLSession.shared.dataTask(with: req) { _, response, error in
            Task { @MainActor in
                if let http = response as? HTTPURLResponse, (200 ..< 400).contains(http.statusCode) {
                    status = "done"
                    appendRow()
                } else if error == nil, response != nil {
                    status = "done"
                    appendRow()
                } else {
                    status = "error"
                }
            }
        }.resume()
    }

    private func appendRow() {
        rows.append((rows.last ?? 0) + 1)
        showToast = true
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(1200))
            showToast = false
        }
    }
}
