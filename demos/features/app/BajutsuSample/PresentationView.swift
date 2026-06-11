import SwiftUI

// Modal presentation styles: a detented sheet, a full-screen cover, an action
// sheet (confirmationDialog), and an auto-dismissing toast. In-app modals are
// visible to idb; the toast exercises `wait until gone`.
struct PresentationView: View {
    @State private var showSheet = false
    @State private var showCover = false
    @State private var showDialog = false
    @State private var dialogResult = "none"
    @State private var showToast = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Presentation")
                    .font(.title)
                    .accessibilityIdentifier("pres.title")

                Button("Open Sheet") { showSheet = true }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("pres.openSheet")

                Button("Open Full-Screen") { showCover = true }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("pres.openCover")

                Button("Open Dialog") { showDialog = true }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("pres.openDialog")
                Text("Dialog: \(dialogResult)")
                    .accessibilityIdentifier("pres.dialog.value")
                    .accessibilityValue(dialogResult)

                Button("Show Toast") { showToastBriefly() }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("pres.showToast")
            }
            .padding()
        }
        .overlay(alignment: .top) {
            if showToast {
                Text("Saved")
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.thinMaterial, in: Capsule())
                    .accessibilityIdentifier("pres.toast")
            }
        }
        .sheet(isPresented: $showSheet) {
            VStack(spacing: 16) {
                Text("Sheet")
                    .font(.title)
                    .accessibilityIdentifier("pres.sheet.title")
                Button("Close") { showSheet = false }
                    .accessibilityIdentifier("pres.sheet.close")
            }
            .padding()
            .presentationDetents([.medium, .large])
        }
        .fullScreenCover(isPresented: $showCover) {
            VStack(spacing: 16) {
                Text("Full Screen")
                    .font(.title)
                    .accessibilityIdentifier("pres.cover.title")
                Button("Close") { showCover = false }
                    .accessibilityIdentifier("pres.cover.close")
            }
            .padding()
        }
        .confirmationDialog("Choose", isPresented: $showDialog, titleVisibility: .visible) {
            Button("Archive") { dialogResult = "archive" }
            Button("Delete", role: .destructive) { dialogResult = "delete" }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func showToastBriefly() {
        showToast = true
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(1200))
            showToast = false
        }
    }
}
