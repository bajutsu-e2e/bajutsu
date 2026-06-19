import Foundation
import UIKit

/// A catalog horse. `id` is data-derived and drives the `*.row.<id>` identifiers.
struct Horse: Identifiable, Hashable {
    let id: Int
    let name: String
}

/// A stable notice. Twenty are seeded; `id` drives the `notice.row.<id>` identifiers.
struct Notice: Identifiable, Hashable {
    let id: Int
    let title: String
    let body: String
}

/// The seeded notices (shared verbatim with the UIKit app — SPEC §5.5). Intentionally
/// longer than one screen so the bottom rows start *off-screen*: reaching `notice.row.20`
/// requires scrolling, the canonical scroll-to-element target.
let showcaseNotices: [Notice] = (1 ... 20).map {
    Notice(id: $0, title: "Notice \($0)", body: "Details for stable notice number \($0).")
}

/// App state plus the launch-env hooks Bajutsu drives (SPEC §3). Plain ObservableObject;
/// delayed mutations hop to the main actor. The app launches straight into the tab UI, so
/// the always-present main UI never gets rebuilt out from under a tap.
final class AppModel: ObservableObject {
    enum Tab: Hashable {
        case stable, search, log, notices, permissions
    }

    // Tab selection + per-tab navigation paths (deeplinks pop these to root)
    @Published var selectedTab: Tab
    @Published var stablePath: [Int] = []  // pushed horse ids
    @Published var noticesPath: [Int] = []  // pushed notice ids

    // The seeded notices (Notices tab list → detail).
    let notices = showcaseNotices

    // Shared catalog (Stable + Search both filter this)
    @Published var horses: [Horse]

    let animationsDisabled: Bool

    private let env: [String: String]

    init(env: [String: String]) {
        self.env = env
        animationsDisabled = env["SHOWCASE_UITEST"] != nil

        let seed = max(0, Int(env["SHOWCASE_SEED"] ?? "5") ?? 5)
        horses = seed > 0 ? (1 ... seed).map { Horse(id: $0, name: "Horse \($0)") } : []

        selectedTab = Self.tab(env["SHOWCASE_TAB"])
    }

    /// Map a `SHOWCASE_TAB` value (and deeplink host) to a tab.
    private static func tab(_ name: String?) -> Tab {
        switch name {
        case "search": return .search
        case "log": return .log
        case "notices": return .notices
        case "permissions": return .permissions
        default: return .stable
        }
    }

    // Networking config (SPEC §3, §6).
    var apiURL: String { env["SHOWCASE_API_URL"] ?? "https://example.com" }
    var httpBase: String { env["SHOWCASE_HTTP_BASE"] ?? "https://httpbin.org" }

    func horses(matching query: String) -> [Horse] {
        query.isEmpty ? horses : horses.filter { $0.name.localizedCaseInsensitiveContains(query) }
    }

    func horse(id: Int) -> Horse? { horses.first { $0.id == id } }

    func notice(id: Int) -> Notice? { notices.first { $0.id == id } }

    // Deeplinks (SPEC §4): also dismiss modals and pop nav to the tab root.
    func handleDeepLink(_ url: URL) {
        stablePath = []
        noticesPath = []

        switch url.host {
        case "stable": selectedTab = .stable
        case "search": selectedTab = .search
        case "log": selectedTab = .log
        case "notices": selectedTab = .notices
        case "permissions": selectedTab = .permissions
        case "horse":
            // …://horse/<id> — Stable tab, push Horse Detail for <id>.
            selectedTab = .stable
            if let id = Int(url.lastPathComponent) {
                stablePath = [id]
            }
        case "notice":
            // …://notice/<id> — Notices tab, push Notice Detail for <id>.
            selectedTab = .notices
            if let id = Int(url.lastPathComponent) {
                noticesPath = [id]
            }
        default: break
        }
    }
}
