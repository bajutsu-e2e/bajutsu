import SwiftUI

struct StableView: View {
    @EnvironmentObject var model: AppModel
    @State private var status = "idle"

    var body: some View {
        // Path bound to the model (a deeplink to this tab pops it to root via `handleDeepLink`).
        // Detail is pushed only by tapping a row (BE-0079): there is no deeplink that jumps
        // straight to a horse.
        NavigationStack(path: $model.stablePath) {
            List {
                if model.horses.isEmpty {
                    Text("No horses")
                        .foregroundStyle(.secondary)
                        .accessibilityID("stable.empty")
                } else {
                    ForEach(model.horses) { horse in
                        NavigationLink(value: horse.id) {
                            Text(horse.name)
                        }
                        .accessibilityID("stable.row.\(horse.id)")
                    }
                }
            }
            .navigationTitle("Stable")
            .navigationDestination(for: Int.self) { id in
                HorseDetailView(id: id)
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Refresh") { refresh() }
                        .accessibilityID("stable.refresh")
                }
            }
            .safeAreaInset(edge: .bottom) {
                Text("Status: \(status)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(8)
                    .accessibilityID("stable.status")
                    .accessibilityStateValue(status)
            }
        }
    }

    // GET SHOWCASE_API_URL + "/horses". Status mirrors to stable.status so a scenario can
    // wait on the response before asserting. Network capture (BajutsuKit) is automatic.
    private func refresh() {
        status = "loading"
        guard let url = URL(string: model.apiURL + "/horses") else { status = "error"; return }
        URLSession.shared.dataTask(with: url) { _, response, error in
            Task { @MainActor in
                if let http = response as? HTTPURLResponse, (200 ..< 400).contains(http.statusCode) {
                    status = "done"
                } else {
                    status = error == nil && response != nil ? "done" : "error"
                }
            }
        }.resume()
    }
}

struct HorseDetailView: View {
    @EnvironmentObject var model: AppModel
    let id: Int
    @State private var status = "idle"
    @State private var favorite = false

    private var horse: Horse? { model.horse(id: id) }

    var body: some View {
        // The standard system back button (pushed by the NavigationStack) pops back; the backend drives
        // it by its built-in id `BackButton`. No custom back control.
        Form {
            Section {
                Text(horse?.name ?? "Horse \(id)")
                    .font(.title2)
                    .accessibilityID("horse.title")
                Text("ID: \(id)")
                    .accessibilityID("horse.id.value")
                    .accessibilityStateValue(String(id))
            }
            Section {
                Button("Fetch detail") { fetch() }
                    .accessibilityID("horse.fetch")
                Text("Status: \(status)")
                    .foregroundStyle(.secondary)
                    .accessibilityID("horse.status")
                    .accessibilityStateValue(status)
            }
            Section {
                // selected trait reflects the toggle; value mirrors on/off for assertions.
                Button {
                    favorite.toggle()
                } label: {
                    Label("Favorite", systemImage: favorite ? "star.fill" : "star")
                }
                .accessibilityAddTraits(favorite ? .isSelected : [])
                .accessibilityID("horse.favorite")
                Text(favorite ? "Favorited" : "Not favorited")
                    .foregroundStyle(.secondary)
                    .accessibilityID("horse.favorite.value")
                    .accessibilityStateValue(favorite ? "on" : "off")
            }
        }
        .navigationTitle(horse?.name ?? "Horse \(id)")
        .navigationBarTitleDisplayMode(.inline)
    }

    // GET <base>/horses/<id>; status mirrors to horse.status.
    private func fetch() {
        status = "loading"
        guard let url = URL(string: model.apiURL + "/horses/\(id)") else { status = "error"; return }
        URLSession.shared.dataTask(with: url) { _, response, error in
            Task { @MainActor in
                if let http = response as? HTTPURLResponse, (200 ..< 400).contains(http.statusCode) {
                    status = "done"
                } else {
                    status = error == nil && response != nil ? "done" : "error"
                }
            }
        }.resume()
    }
}
