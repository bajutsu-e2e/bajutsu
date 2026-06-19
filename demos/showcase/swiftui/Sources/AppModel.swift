import Foundation
import UIKit

/// A catalog horse. `id` is data-derived and drives the `*.row.<id>` identifiers.
struct Horse: Identifiable, Hashable {
    let id: Int
    let name: String
}

/// A stable notice. Three are seeded; `id` drives the `notice.row.<id>` identifiers.
struct Notice: Identifiable, Hashable {
    let id: Int
    let title: String
    let body: String
}

/// The three seeded notices (shared verbatim with the UIKit app — SPEC §5.5).
let showcaseNotices: [Notice] = [
    Notice(id: 1, title: "Stable closed Monday",
           body: "The stable is closed this Monday for scheduled maintenance."),
    Notice(id: 2, title: "New horse arriving",
           body: "A new horse joins the stable next week. Introductions on Saturday."),
    Notice(id: 3, title: "Vaccination schedule",
           body: "Annual vaccinations are due by the end of the month."),
]

/// App state plus the launch-env hooks Bajutsu drives (SPEC §3). Plain ObservableObject;
/// delayed mutations hop to the main actor. Tabs and the auth gate both read from here so
/// the always-present main UI never gets rebuilt out from under a tap.
final class AppModel: ObservableObject {
    enum Screen {
        case onboarding
        case login
        case home
    }

    enum Tab: Hashable {
        case stable, search, log, notices, profile
    }

    // Auth gate
    @Published var screen: Screen
    @Published var email = ""
    @Published var password = ""
    @Published var loginError = false

    // Tab selection + per-tab navigation paths (deeplinks pop these to root)
    @Published var selectedTab: Tab
    @Published var stablePath: [Int] = []  // pushed horse ids
    @Published var noticesPath: [Int] = []  // pushed notice ids
    @Published var profilePath: [ProfileRoute] = []

    // The seeded notices (Notices tab list → detail).
    let notices = showcaseNotices

    // Shared catalog (Stable + Search both filter this)
    @Published var horses: [Horse]

    // Profile settings
    @Published var normalize = true
    @Published var profileChanged = false

    let animationsDisabled: Bool

    /// The email used to log in, surfaced on the Account screen.
    var accountEmail: String { email.isEmpty ? "rider@example.com" : email }

    private let env: [String: String]

    init(env: [String: String]) {
        self.env = env
        animationsDisabled = env["SHOWCASE_UITEST"] != nil

        let seed = max(0, Int(env["SHOWCASE_SEED"] ?? "5") ?? 5)
        horses = seed > 0 ? (1 ... seed).map { Horse(id: $0, name: "Horse \($0)") } : []

        if env["SHOWCASE_LOGGED_IN"] != nil {
            screen = .home
        } else if env["SHOWCASE_SKIP_ONBOARDING"] != nil {
            screen = .login
        } else {
            screen = .onboarding
        }
        selectedTab = Self.tab(env["SHOWCASE_TAB"])
    }

    /// Map a `SHOWCASE_TAB` value (and deeplink host) to a tab.
    private static func tab(_ name: String?) -> Tab {
        switch name {
        case "search": return .search
        case "log": return .log
        case "notices": return .notices
        case "profile": return .profile
        default: return .stable
        }
    }

    // Networking config (SPEC §3, §6).
    var apiURL: String { env["SHOWCASE_API_URL"] ?? "https://example.com" }
    var httpBase: String { env["SHOWCASE_HTTP_BASE"] ?? "https://httpbin.org" }

    func finishOnboarding() {
        screen = .login
    }

    func login() {
        if email.isEmpty || password.isEmpty {
            loginError = true
        } else {
            loginError = false
            // Dismiss the keyboard so Home's accessibility tree is clean.
            UIApplication.shared.sendAction(
                #selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
            screen = .home
        }
    }

    func logout() {
        profilePath = []
        screen = .login
    }

    func toggleNormalize() {
        normalize.toggle()
        profileChanged = true
    }

    func horses(matching query: String) -> [Horse] {
        query.isEmpty ? horses : horses.filter { $0.name.localizedCaseInsensitiveContains(query) }
    }

    func horse(id: Int) -> Horse? { horses.first { $0.id == id } }

    func notice(id: Int) -> Notice? { notices.first { $0.id == id } }

    // Deeplinks (SPEC §4): also dismiss modals and pop nav to the tab root.
    func handleDeepLink(_ url: URL) {
        // Any deeplink lands on the main UI; an onboarding/login gate would hide it.
        if screen != .home { screen = .home }
        stablePath = []
        noticesPath = []
        profilePath = []

        switch url.host {
        case "stable": selectedTab = .stable
        case "search": selectedTab = .search
        case "log": selectedTab = .log
        case "notices": selectedTab = .notices
        case "profile": selectedTab = .profile
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
        case "permissions":
            // …://permissions — Profile tab, push the OS-alert screen.
            selectedTab = .profile
            profilePath = [.permissions]
        default: break
        }
    }
}

/// Profile sub-screens, used as `NavigationStack` path values.
enum ProfileRoute: Hashable {
    case account, permissions, about
}
