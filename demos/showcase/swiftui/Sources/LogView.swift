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
                        .aid("log.note")

                    Stepper("Count: \(count)", value: $count, in: 0 ... 99)
                        .aid("log.count")
                    Text("Count: \(count)")
                        .foregroundStyle(.secondary)
                        .aid("log.count.value")
                        .aidValue(String(count))

                    Toggle("Intense", isOn: $intense)
                        .aid("log.intense")
                    Text(intense ? "Intense" : "Easy")
                        .foregroundStyle(.secondary)
                        .aid("log.intense.value")
                        .aidValue(intense ? "on" : "off")

                    Button("Submit") { submit() }
                        .aid("log.submit")
                    Text("Status: \(status)")
                        .foregroundStyle(.secondary)
                        .aid("log.status")
                        .aidValue(status)
                }

                Section("Modals") {
                    Button("Open Filter") { showSheet = true }
                        .aid("log.openFilter")
                    Button("Open Gallery") { showCover = true }
                        .aid("log.openGallery")
                    Button("Open Delete") { showDialog = true }
                        .aid("log.openDelete")
                    Text("Dialog: \(dialogResult)")
                        .foregroundStyle(.secondary)
                        .aid("log.dialog.value")
                        .aidValue(dialogResult)
                }

                Section("Entries") {
                    ForEach(rows, id: \.self) { n in
                        Text("Entry \(n)")
                            .aid("log.row.\(n)")
                    }
                }
            }
            .navigationTitle("Log")
        }
        .aid("log.title")
        .overlay(alignment: .top) {
            if showToast {
                Text("Saved")
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.thinMaterial, in: Capsule())
                    .aid("log.toast")
            }
        }
        // Sheet with detents (SPEC §5.3).
        .sheet(isPresented: $showSheet) {
            VStack(spacing: 16) {
                Text("Filter")
                    .font(.title)
                    .aid("log.sheet.title")
                Button("Apply") { showSheet = false }
                    .buttonStyle(.borderedProminent)
                    .aid("log.sheet.apply")
                Button("Close") { showSheet = false }
                    .aid("log.sheet.close")
            }
            .padding()
            .presentationDetents([.medium, .large])
        }
        // Full-screen cover.
        .fullScreenCover(isPresented: $showCover) {
            VStack(spacing: 16) {
                Text("Gallery")
                    .font(.title)
                    .aid("log.cover.title")
                Button("Close") { showCover = false }
                    .aid("log.cover.close")
            }
            .padding()
        }
        // Action sheet (confirmationDialog); result mirrors to log.dialog.value.
        .confirmationDialog("Delete entry", isPresented: $showDialog, titleVisibility: .visible) {
            Button("Archive") { dialogResult = "archive" }
                .aid("log.dialog.archive")
            Button("Delete", role: .destructive) { dialogResult = "delete" }
                .aid("log.dialog.delete")
            Button("Cancel", role: .cancel) {}
                .aid("log.dialog.cancel")
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
