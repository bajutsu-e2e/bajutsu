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

                Button("Open Sheet") { showSheet = true }
                    .buttonStyle(.borderedProminent)

                Button("Open Full-Screen") { showCover = true }
                    .buttonStyle(.bordered)

                Button("Open Dialog") { showDialog = true }
                    .buttonStyle(.bordered)
                Text("Dialog: \(dialogResult)")

                Button("Show Toast") { showToastBriefly() }
                    .buttonStyle(.bordered)
            }
            .padding()
        }
        .overlay(alignment: .top) {
            if showToast {
                Text("Saved")
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.thinMaterial, in: Capsule())
            }
        }
        .sheet(isPresented: $showSheet) {
            VStack(spacing: 16) {
                Text("Sheet")
                    .font(.title)
                Button("Close") { showSheet = false }
            }
            .padding()
            .presentationDetents([.medium, .large])
        }
        .fullScreenCover(isPresented: $showCover) {
            VStack(spacing: 16) {
                Text("Full Screen")
                    .font(.title)
                Button("Close") { showCover = false }
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
