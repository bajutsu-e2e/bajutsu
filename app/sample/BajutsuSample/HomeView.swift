import SwiftUI

struct HomeView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                Text("Home")
                    .font(.title)
                    .accessibilityIdentifier("home.title")

                HStack {
                    Text("Count: \(model.counter)")
                        .accessibilityIdentifier("counter.value")
                        .accessibilityValue("\(model.counter)")
                    Button("+") { model.increment() }
                        .accessibilityIdentifier("counter.increment")
                }

                if model.isLoading {
                    ProgressView()
                        .accessibilityIdentifier("home.spinner")
                } else if model.loaded {
                    Text("Loaded")
                        .accessibilityIdentifier("home.loaded")
                } else {
                    Button("Load") { model.load() }
                        .accessibilityIdentifier("home.load")
                }

                TextField("Search", text: $model.query)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("home.search")

                List(model.filteredItems) { item in
                    Text(item.name)
                        .accessibilityIdentifier("list.row.\(item.id)")
                }
                .accessibilityIdentifier("home.list")
            }
            .padding()
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Settings") { model.showSettings = true }
                        .accessibilityIdentifier("nav.settings")
                }
            }
            .sheet(isPresented: $model.showSettings) { SettingsView() }
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("Settings")
                    .font(.title)
                    .accessibilityIdentifier("settings.title")

                Button {
                    model.toggleNormalize()
                } label: {
                    HStack {
                        Image(systemName: model.normalize ? "checkmark.square" : "square")
                        Text("Normalize")
                    }
                }
                .accessibilityIdentifier("settings.normalizeToggle")
                .accessibilityAddTraits(model.normalize ? .isSelected : [])
                // Mirror the selected state into the value so headless backends that
                // do not surface the isSelected trait (e.g. idb) can still read it.
                .accessibilityValue(model.normalize ? "on" : "off")

                if model.settingsChanged {
                    Text("Settings changed — reindex needed")
                        .font(.callout)
                        .accessibilityIdentifier("settings.banner")
                }

                Button("Reindex") { model.reindex() }
                    .accessibilityIdentifier("settings.reindex")

                Text("Status: \(model.reindexStatus)")
                    .accessibilityIdentifier("settings.status")
                    .accessibilityValue(model.reindexStatus)

                if model.reindexStatus == "done" {
                    Text("Reindex complete")
                        .accessibilityIdentifier("settings.reindexComplete")
                }

                Button("Close") { dismiss() }
                    .accessibilityIdentifier("settings.close")
                Spacer()
            }
            .padding()
        }
    }
}
