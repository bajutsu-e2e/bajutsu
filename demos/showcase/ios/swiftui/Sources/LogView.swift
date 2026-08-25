import SwiftUI

// Training-log composer: exercises every input control and all five modal styles
// (sheet, fullScreenCover, a custom action-sheet overlay, native alert, auto-dismissing toast). In-app
// modals are visible to the backend; the toast exercises `wait until gone`.
struct LogView: View {
    @EnvironmentObject var model: AppModel

    @State private var note = ""
    @State private var count = 1
    @State private var intense = false
    @State private var segment = "one"
    @State private var status = "idle"
    @State private var rows: [Int] = []

    // The segmented control's choices, in display order.
    private let segments = ["one", "two", "three"]

    // Dedicated gesture targets (SPEC §5.3): a long-press and a double-tap whose results
    // mirror to a11y values, so a scenario can assert the gesture landed.
    @State private var longPressed = false
    @State private var doubleTaps = 0

    // Modal state
    @State private var showSheet = false
    @State private var showCover = false
    @State private var showDialog = false
    @State private var showPopoverDialog = false
    @State private var dialogResult = "none"
    @State private var showAlert = false
    @State private var alertResult = "none"
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

                    // A button-backed toggle (not a SwiftUI Toggle): on iOS 26 the retired idb backend's tap did
                    // not flip a Toggle's switch (BE-0290), but a Button toggles reliably — the same
                    // pattern horse.favorite uses. `selected` trait reflects the state.
                    Button {
                        intense.toggle()
                    } label: {
                        Label("Intense", systemImage: intense ? "checkmark.square.fill" : "square")
                    }
                    .accessibilityAddTraits(intense ? .isSelected : [])
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
                    // The fixture for the TipKit guard's other direction: a real SwiftUI
                    // confirmationDialog installs the SAME `PopoverDismissRegion` scrim a TipKit tip
                    // does — measured on-device, identical identifier, label, and full-screen frame —
                    // so it is what proves the guard leaves an app's own popover alone. The
                    // duplicate-element problem noted below rules the dialog out as a *tap* fixture,
                    // not as this one: the guard's detection never taps its buttons.
                    Button("Open Popover Dialog") { showPopoverDialog = true }
                        .accessibilityID("log.openPopoverDialog")
                        .confirmationDialog(
                            "Remove this note?",
                            isPresented: $showPopoverDialog,
                            titleVisibility: .visible
                        ) {
                            Button("Copy note") { dialogResult = "copy-note" }
                            Button("Remove note", role: .destructive) { dialogResult = "remove-note" }
                            Button("Cancel", role: .cancel) {}
                        }
                    Text("Dialog: \(dialogResult)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("log.dialog.value")
                        .accessibilityStateValue(dialogResult)
                    Button("Open Alert") { showAlert = true }
                        .accessibilityID("log.openAlert")
                    Text("Alert: \(alertResult)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("log.alert.value")
                        .accessibilityStateValue(alertResult)
                }

                Section("Gestures") {
                    Text("Long-press me")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                        .onLongPressGesture(minimumDuration: 0.5) { longPressed = true }
                        .accessibilityID("log.longpress")
                    Text(longPressed ? "pressed" : "idle")
                        .foregroundStyle(.secondary)
                        .accessibilityID("log.longpress.value")
                        .accessibilityStateValue(longPressed ? "pressed" : "idle")

                    Text("Double-tap me")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                        .onTapGesture(count: 2) { doubleTaps += 1 }
                        .accessibilityID("log.doubletap")
                    Text("Double-taps: \(doubleTaps)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("log.doubletap.value")
                        .accessibilityStateValue(String(doubleTaps))
                }

                // A button-backed segmented control (not a SwiftUI Picker(.segmented): the retired idb backend's
                // tap did not switch a native segmented control on iOS 26, BE-0290). Each choice is a
                // Button whose `selected` trait reflects the current pick, the same idiom as the
                // Intense toggle; the selection mirrors to log.segment.value. Kept below the
                // modals/gestures so those sections' scroll positions are unchanged.
                Section("Controls") {
                    ForEach(segments, id: \.self) { choice in
                        Button {
                            segment = choice
                        } label: {
                            Label(choice.capitalized, systemImage: segment == choice ? "largecircle.fill.circle" : "circle")
                        }
                        .accessibilityAddTraits(segment == choice ? .isSelected : [])
                        .accessibilityID("log.segment.\(choice)")
                    }
                    Text("Segment: \(segment)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("log.segment.value")
                        .accessibilityStateValue(segment)
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
        // Action sheet: a ZStack overlay of choices. Not a SwiftUI confirmationDialog (its
        // buttons render as DUPLICATE accessibility elements on iOS 26, defeating a
        // single-match tap) and not a second `.sheet` (one view honours only one `.sheet`, so
        // it would silently not present alongside the filter sheet). Plain Buttons resolve
        // uniquely; result mirrors to log.dialog.value.
        .overlay {
            if showDialog {
                ZStack {
                    Color.black.opacity(0.2).ignoresSafeArea()
                    VStack(spacing: 16) {
                        Text("Delete entry")
                            .font(.headline)
                            .accessibilityID("log.dialog.title")
                        Button("Archive") { dialogResult = "archive"; showDialog = false }
                            .accessibilityID("log.dialog.archive")
                        Button("Delete", role: .destructive) { dialogResult = "delete"; showDialog = false }
                            .accessibilityID("log.dialog.delete")
                        Button("Cancel", role: .cancel) { showDialog = false }
                            .accessibilityID("log.dialog.cancel")
                    }
                    .padding(24)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
                }
            }
        }
        // Native alert (SwiftUI `.alert`, UIAlertController style `.alert` underneath — the
        // fifth modal style, distinct from the action sheet above): two actions, "Cancel" and
        // "OK", each carrying an `id` in the a11y build. Result mirrors to log.alert.value
        // (`none`/`cancel`/`ok`), so a scenario can tell the two actions apart by which value
        // lands.
        .alert("Sample Alert", isPresented: $showAlert) {
            Button("Cancel", role: .cancel) { alertResult = "cancel" }
                .accessibilityID("log.alert.cancel")
            Button("OK") { alertResult = "ok" }
                .accessibilityID("log.alert.ok")
        } message: {
            Text("This is a native alert.")
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
