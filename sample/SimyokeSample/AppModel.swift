import Foundation
import UIKit
import os

struct Item: Identifiable {
    let id: Int
    let name: String
}

/// App state plus the launch-env hooks Simyoke drives. Plain ObservableObject;
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

    let animationsDisabled: Bool
    private let signposter = OSSignposter(subsystem: "com.simyoke.sample", category: "actions")

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
            try? await Task.sleep(for: .milliseconds(300))
            self.reindexStatus = "done"
            self.signposter.endInterval("reindex", interval)
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
        default: break
        }
    }
}
