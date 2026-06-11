import SwiftUI

// A List exercising search filtering, swipe-to-delete, edit-mode reorder,
// pull-to-refresh, push navigation, and an empty state. Row ids are data-derived
// (`lists.row.<id>`) so a backend can address any row and count them by glob.
struct ListsNavView: View {
    @State private var items: [Item] = ListsNavView.seed
    @State private var query = ""
    @State private var refreshed = false

    static let seed = (1 ... 5).map { Item(id: $0, name: "Row \($0)") }

    private var filtered: [Item] {
        query.isEmpty ? items : items.filter { $0.name.localizedCaseInsensitiveContains(query) }
    }

    var body: some View {
        NavigationStack {
            VStack {
                TextField("Search", text: $query)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .padding(.horizontal)

                if filtered.isEmpty {
                    Text("No items")
                        .foregroundStyle(.secondary)
                }

                List {
                    ForEach(filtered) { item in
                        NavigationLink(value: item.id) {
                            Text(item.name)
                        }
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) {
                                items.removeAll { $0.id == item.id }
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                    }
                    .onDelete { offsets in
                        // EditButton path: map the filtered offsets back to `items`.
                        let ids = offsets.map { filtered[$0].id }
                        items.removeAll { ids.contains($0.id) }
                    }
                    .onMove { source, dest in
                        items.move(fromOffsets: source, toOffset: dest)
                    }
                }
                .refreshable {
                    // Pull-to-refresh restores the seed set and reveals a banner.
                    items = ListsNavView.seed
                    refreshed = true
                }

                Text("Count: \(filtered.count)")
                if refreshed {
                    Text("Refreshed")
                }
            }
            .navigationTitle("Lists")
            .navigationDestination(for: Int.self) { id in
                ListDetailView(name: items.first { $0.id == id }?.name ?? "Row \(id)")
            }
            .toolbar {
                EditButton()
            }
        }
    }
}

struct ListDetailView: View {
    let name: String

    var body: some View {
        VStack(spacing: 12) {
            Text("Detail")
                .font(.title)
            Text(name)
        }
        .padding()
    }
}
