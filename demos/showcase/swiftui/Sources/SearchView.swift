import SwiftUI

struct SearchView: View {
    @EnvironmentObject var model: AppModel
    @State private var query = ""

    private var matches: [Horse] { model.horses(matching: query) }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                HStack {
                    TextField("Search horses", text: $query)
                        .textFieldStyle(.roundedBorder)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .accessibilityID("search.field")
                    Button("Clear") { query = "" }
                        .accessibilityID("search.clear")
                }
                .padding()

                Text("Matches: \(matches.count)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .accessibilityID("search.count")
                    .accessibilityStateValue(String(matches.count))

                List {
                    if matches.isEmpty {
                        Text("No matches")
                            .foregroundStyle(.secondary)
                            // SPEC §5.2: this element's id is search.results-empty.
                            .accessibilityID("search.results-empty")
                    } else {
                        ForEach(matches) { horse in
                            Text(horse.name)
                                .accessibilityID("search.row.\(horse.id)")
                        }
                    }
                }
            }
            .navigationTitle("Search")
        }
        .accessibilityID("search.title")
    }
}
