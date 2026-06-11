import SwiftUI

struct HomeView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                Text("Home")
                    .font(.title)

                HStack {
                    Text("Count: \(model.counter)")
                    Button("+") { model.increment() }
                }

                if model.isLoading {
                    ProgressView()
                } else if model.loaded {
                    Text("Loaded")
                } else {
                    Button("Load") { model.load() }
                }

                TextField("Search", text: $model.query)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()

                List(model.filteredItems) { item in
                    Text(item.name)
                }
            }
            .padding()
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Settings") { model.showSettings = true }
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

                Button {
                    model.toggleNormalize()
                } label: {
                    HStack {
                        Image(systemName: model.normalize ? "checkmark.square" : "square")
                        Text("Normalize")
                    }
                }
                .accessibilityAddTraits(model.normalize ? .isSelected : [])
                // Mirror the selected state into the value so headless backends that
                // do not surface the isSelected trait (e.g. idb) can still read it.

                if model.settingsChanged {
                    Text("Settings changed — reindex needed")
                        .font(.callout)
                }

                Button("Reindex") { model.reindex() }

                Text("Status: \(model.reindexStatus)")

                if model.reindexStatus == "done" {
                    Text("Reindex complete")
                }

                Button("Close") { dismiss() }
                Spacer()
            }
            .padding()
        }
    }
}
