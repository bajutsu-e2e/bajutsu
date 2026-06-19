import SwiftUI

struct StableView: View {
    @EnvironmentObject var model: AppModel
    @State private var status = "idle"

    var body: some View {
        // Path bound to the model so deeplinks (…://horse/<id>) can push detail.
        NavigationStack(path: $model.stablePath) {
            List {
                if model.horses.isEmpty {
                    Text("No horses")
                        .foregroundStyle(.secondary)
                        .aid("stable.empty")
                } else {
                    ForEach(model.horses) { horse in
                        NavigationLink(value: horse.id) {
                            Text(horse.name)
                        }
                        .aid("stable.row.\(horse.id)")
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
                        .aid("stable.refresh")
                }
            }
            .safeAreaInset(edge: .bottom) {
                Text("Status: \(status)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(8)
                    .aid("stable.status")
                    .aidValue(status)
            }
        }
        .aid("stable.title")
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
        Form {
            Section {
                Text(horse?.name ?? "Horse \(id)")
                    .font(.title2)
                    .aid("horse.title")
                Text("ID: \(id)")
                    .aid("horse.id.value")
                    .aidValue(String(id))
            }
            Section {
                Button("Fetch detail") { fetch() }
                    .aid("horse.fetch")
                Text("Status: \(status)")
                    .foregroundStyle(.secondary)
                    .aid("horse.status")
                    .aidValue(status)
            }
            Section {
                // selected trait reflects the toggle; value mirrors on/off for assertions.
                Button {
                    favorite.toggle()
                } label: {
                    Label("Favorite", systemImage: favorite ? "star.fill" : "star")
                }
                .accessibilityAddTraits(favorite ? .isSelected : [])
                .aid("horse.favorite")
                Text(favorite ? "Favorited" : "Not favorited")
                    .foregroundStyle(.secondary)
                    .aid("horse.favorite.value")
                    .aidValue(favorite ? "on" : "off")
            }
        }
        .navigationTitle(horse?.name ?? "Horse \(id)")
        .navigationBarTitleDisplayMode(.inline)
        // The system back button is given the reserved nav.back id explicitly (SPEC §5.1).
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    if !model.stablePath.isEmpty { model.stablePath.removeLast() }
                } label: {
                    Label("Back", systemImage: "chevron.backward")
                }
                .aid("nav.back")
            }
        }
        .navigationBarBackButtonHidden()
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
