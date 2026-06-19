import Foundation

struct Horse: Identifiable {
    let id: Int
    let name: String
}

/// A stable notice. Three are seeded; `id` drives the `notice.row.<id>` identifiers.
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
/// shares (auth, catalog seed, logged-in email). All env reads happen once at launch
/// from ProcessInfo (SPEC §3). Per-screen state lives in the view controllers.
final class AppModel {
    enum Screen {
        case onboarding
        case login
        case home
    }

    enum Tab: Int {
        case stable, search, log, notices, profile
    }

    private(set) var screen: Screen
    private(set) var email = ""

    let initialTab: Tab
    let animationsDisabled: Bool
    let apiBase: String
    let httpBase: String

    /// The offline catalog, seeded from SHOWCASE_SEED. Network rows would extend this.
    private(set) var horses: [Horse]

    /// The seeded notices (Notices tab list → detail).
    let notices = showcaseNotices

    init(env: [String: String]) {
        animationsDisabled = env["SHOWCASE_UITEST"] != nil
        apiBase = env["SHOWCASE_API_URL"] ?? "https://example.com"
        httpBase = env["SHOWCASE_HTTP_BASE"] ?? "https://httpbin.org"

        let seed = max(0, Int(env["SHOWCASE_SEED"] ?? "5") ?? 5)
        horses = seed > 0 ? (1 ... seed).map { Horse(id: $0, name: "Horse \($0)") } : []

        if env["SHOWCASE_LOGGED_IN"] != nil {
            screen = .home
            email = "rider@example.com"
        } else if env["SHOWCASE_SKIP_ONBOARDING"] != nil {
            screen = .login
        } else {
            screen = .onboarding
        }

        initialTab = Self.tab(env["SHOWCASE_TAB"]) ?? .stable
    }

    static func tab(_ name: String?) -> Tab? {
        switch name {
        case "stable": return .stable
        case "search": return .search
        case "log": return .log
        case "notices": return .notices
        case "profile": return .profile
        default: return nil
        }
    }

    func horse(id: Int) -> Horse? {
        horses.first { $0.id == id }
    }

    func notice(id: Int) -> Notice? {
        notices.first { $0.id == id }
    }

    func advanceToLogin() {
        screen = .login
    }

    func login(email: String) {
        self.email = email
        screen = .home
    }

    func logout() {
        email = ""
        screen = .login
    }
}
