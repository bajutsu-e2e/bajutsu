import Foundation
import UIKit
import os

struct Item: Identifiable {
    let id: Int
    let name: String
}

/// App state plus the launch-env hooks Bajutsu drives. Plain ObservableObject;
/// delayed mutations hop to the main actor.
final class AppModel: ObservableObject {
    enum Screen {
        case onboarding
        case login
        case home
    }

    @Published var screen: Screen
    @Published var showSettings: Bool
    @Published var items: [Item]
    @Published var query = ""
    @Published var counter = 0
    @Published var normalize = true
    @Published var reindexStatus = "idle"
    @Published var settingsChanged = false
    @Published var isLoading = false
    @Published var loaded = false
    @Published var email = ""
    @Published var password = ""
    @Published var loginError = false
    @Published var selectedTab = 0  // 0=Home 1=Components 2=Controls 3=Text 4=Lists

    let animationsDisabled: Bool
    private let signposter = OSSignposter(subsystem: "com.bajutsu.sample", category: "actions")
    private let logger = Logger(subsystem: "com.bajutsu.sample", category: "actions")

    init(env: [String: String]) {
        animationsDisabled = env["SAMPLE_UITEST"] != nil

        let seed = max(0, Int(env["SAMPLE_SEED"] ?? "3") ?? 3)
        items = seed > 0 ? (1 ... seed).map { Item(id: $0, name: "Item \($0)") } : []

        let loggedIn = env["SAMPLE_LOGGED_IN"] != nil
        if loggedIn {
            screen = .home
        } else if env["SAMPLE_SKIP_ONBOARDING"] != nil {
            screen = .login
        } else {
            screen = .onboarding
        }
        showSettings = env["SAMPLE_SCREEN"] == "settings"
        selectedTab = Self.tabIndex(env["SAMPLE_TAB"])
    }

    /// Map a `SAMPLE_TAB` value (and deep-link host) to a tab index.
    private static func tabIndex(_ name: String?) -> Int {
        switch name {
        case "components": return 1
        case "controls": return 2
        case "text": return 3
        case "lists": return 4
        case "gestures": return 5
        case "presentation": return 6
        case "async": return 7
        default: return 0
        }
    }

    var filteredItems: [Item] {
        query.isEmpty ? items : items.filter { $0.name.localizedCaseInsensitiveContains(query) }
    }

    func finishOnboarding() {
        screen = .login
    }

    func login() {
        if email.isEmpty || password.isEmpty {
            loginError = true
        } else {
            loginError = false
            // Dismiss the keyboard so the home screen's accessibility tree is clean.
            UIApplication.shared.sendAction(
                #selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
            screen = .home
        }
    }

    func increment() {
        counter += 1
    }

    func toggleNormalize() {
        normalize.toggle()
        settingsChanged = true
    }

    func reindex() {
        reindexStatus = "reindexing"
        settingsChanged = false
        let interval = signposter.beginInterval("reindex")
        Task { @MainActor in
            // Emit the start/finish markers from inside the task so both land inside an
            // appTrace capture window after the log stream has warmed up (it has startup
            // latency, so a marker logged synchronously at the tap would be missed).
            try? await Task.sleep(for: .milliseconds(800))
            self.logger.notice("reindex started")
            try? await Task.sleep(for: .milliseconds(1200))
            self.reindexStatus = "done"
            self.signposter.endInterval("reindex", interval)
            self.logger.notice("reindex finished")
        }
    }

    func load() {
        isLoading = true
        loaded = false
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(1))
            self.isLoading = false
            self.loaded = true
        }
    }

    func handleDeepLink(_ url: URL) {
        switch url.host {
        case "settings": showSettings = true
        case "home": screen = .home
        case "components", "controls", "text", "lists", "gestures", "presentation", "async":
            screen = .home
            selectedTab = Self.tabIndex(url.host)
        default: break
        }
    }
}
