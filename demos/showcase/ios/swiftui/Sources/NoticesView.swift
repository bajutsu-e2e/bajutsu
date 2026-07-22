import SwiftUI

// Tab: Notices (SPEC §5.5). A plain vertical list of three notices; tapping a row
// pushes its detail. The smallest list → detail flow, distinct from the data-loading
// Stable catalog — a clean target for navigation scenarios and crawl.
struct NoticesView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        // Path bound to the model (a deeplink to this tab pops it to root via `handleDeepLink`).
        // Detail is pushed only by tapping a row (BE-0079): there is no deeplink that jumps
        // straight to a notice.
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
    }
}

struct NoticeDetailView: View {
    @EnvironmentObject var model: AppModel
    let id: Int

    private var notice: Notice? { model.notice(id: id) }

    var body: some View {
        // The standard system back button pops back; the backend drives it by its built-in id `BackButton`.
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
    }
}
