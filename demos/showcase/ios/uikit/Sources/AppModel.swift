import Foundation

struct Horse: Identifiable {
    let id: Int
    let name: String
}

/// A stable notice. Twenty are seeded; `id` drives the `notice.row.<id>` identifiers.
struct Notice: Identifiable {
    let id: Int
    let title: String
    let body: String
}

/// The seeded notices (shared verbatim with the SwiftUI app — SPEC §5.5). Intentionally
/// longer than one screen so the bottom rows start *off-screen*: reaching `notice.row.20`
/// requires scrolling, the canonical scroll-to-element target.
let showcaseNotices: [Notice] = (1 ... 20).map {
    Notice(id: $0, title: "Notice \($0)", body: "Details for stable notice number \($0).")
}

/// Launch-env configuration plus the small amount of cross-screen state the showcase
/// shares (catalog seed). All env reads happen once at launch from ProcessInfo
/// (SPEC §3). Per-screen state lives in the view controllers.
final class AppModel {
    enum Tab: Int {
        case stable, search, log, notices, permissions
    }

    let animationsDisabled: Bool
    let apiBase: String
    let httpBase: String

    /// The offline catalog. Fixed at launch — no launch-env seed knob (BE-0079): a scenario
    /// cannot inject a data state, only observe the app's own. Network rows would extend this.
    private(set) var horses: [Horse] = (1 ... 5).map { Horse(id: $0, name: "Horse \($0)") }

    /// The seeded notices (Notices tab list → detail).
    let notices = showcaseNotices

    init(env: [String: String]) {
        animationsDisabled = env["SHOWCASE_UITEST"] != nil
        apiBase = env["SHOWCASE_API_URL"] ?? "https://example.com"
        httpBase = env["SHOWCASE_HTTP_BASE"] ?? "https://httpbin.org"
    }
}
