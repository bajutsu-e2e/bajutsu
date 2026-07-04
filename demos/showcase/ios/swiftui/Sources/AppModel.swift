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

    // Shared catalog (Stable + Search both filter this). Fixed at launch — no launch-env seed
    // knob (BE-0079): a scenario cannot inject a data state, only observe the app's own.
    @Published var horses: [Horse] = (1 ... 5).map { Horse(id: $0, name: "Horse \($0)") }

    // Driver-conformance mode (BE-0114): a test-only affordance, entirely gated on the
    // SHOWCASE_CONFORMANCE launch env. nil = the normal observe-only app (BE-0079, untouched);
    // non-nil = render exactly these identifiers (duplicates / the empty set included) so the
    // conformance suite can seed arbitrary screens on-device by relaunching. See ConformanceView.
    let conformanceIDs: [String]?

    let animationsDisabled: Bool

    private let env: [String: String]

    init(env: [String: String]) {
        self.env = env
        animationsDisabled = env["SHOWCASE_UITEST"] != nil

        selectedTab = Self.tab(env["SHOWCASE_TAB"])
        conformanceIDs = Self.conformanceIDs(env["SHOWCASE_CONFORMANCE"])
    }

    /// Parse the `SHOWCASE_CONFORMANCE` id spec. nil (env unset) leaves conformance mode off;
    /// a present-but-empty spec is the empty (zero-match) screen.
    private static func conformanceIDs(_ spec: String?) -> [String]? {
        guard let spec else { return nil }
        return spec.isEmpty ? [] : spec.components(separatedBy: ",")
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

    // Deeplinks (SPEC §4): select a tab (and pop it to root). A deeplink no longer pushes a
    // detail screen (BE-0079): a detail is reached only by tapping its catalog row, so the app
    // has no launch-env shortcut straight onto a pushed screen.
    func handleDeepLink(_ url: URL) {
        stablePath = []
        noticesPath = []

        switch url.host {
        case "stable": selectedTab = .stable
        case "search": selectedTab = .search
        case "log": selectedTab = .log
        case "notices": selectedTab = .notices
        case "permissions": selectedTab = .permissions
        default: break
        }
    }
}
