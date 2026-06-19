import SwiftUI

// Tab: Notices (SPEC §5.5). A plain vertical list of three notices; tapping a row
// pushes its detail. The smallest list → detail flow, distinct from the data-loading
// Stable catalog — a clean target for navigation scenarios and crawl.
struct NoticesView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        // Path bound to the model so deeplinks (…://notice/<id>) can push detail.
        NavigationStack(path: $model.noticesPath) {
            List {
                ForEach(model.notices) { notice in
                    NavigationLink(value: notice.id) {
                        Text(notice.title)
                    }
                    .accessibilityID("notice.row.\(notice.id)")
                }
            }
            .navigationTitle("Notices")
            .navigationDestination(for: Int.self) { id in
                NoticeDetailView(id: id)
            }
        }
        .accessibilityID("notice.title")
    }
}

struct NoticeDetailView: View {
    @EnvironmentObject var model: AppModel
    let id: Int

    private var notice: Notice? { model.notice(id: id) }

    var body: some View {
        Form {
            Text(notice?.title ?? "Notice \(id)")
                .font(.title2)
                .accessibilityID("notice.detail.title")
            Text(notice?.body ?? "")
                .foregroundStyle(.secondary)
                .accessibilityID("notice.detail.body")
        }
        .navigationTitle(notice?.title ?? "Notice \(id)")
        .navigationBarTitleDisplayMode(.inline)
        // The system back button is given the reserved nav.back id explicitly (SPEC §5.1).
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    if !model.noticesPath.isEmpty { model.noticesPath.removeLast() }
                } label: {
                    Label("Back", systemImage: "chevron.backward")
                }
                .accessibilityID("nav.back")
            }
        }
        .navigationBarBackButtonHidden()
    }
}
