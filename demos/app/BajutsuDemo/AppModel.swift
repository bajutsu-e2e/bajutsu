import Foundation
import UIKit

/// App state plus the launch-env hooks Bajutsu drives. A focused onboarding -> login ->
/// home (counter) flow — the smallest app that tells the whole demo story deterministically.
final class AppModel: ObservableObject {
    enum Screen {
        case onboarding
        case login
        case home
    }

    @Published var screen: Screen
    @Published var email = ""
    @Published var password = ""
    @Published var loginError = false
    @Published var counter = 0

    init(env: [String: String]) {
        // Start screen is launch-env controlled so a scenario can begin wherever it needs:
        //   DEMO_LOGGED_IN       -> straight to Home (skip onboarding + login)
        //   DEMO_SKIP_ONBOARDING -> straight to the login screen
        //   (neither)            -> onboarding (the full flow the tour walks)
        if env["DEMO_LOGGED_IN"] != nil {
            screen = .home
        } else if env["DEMO_SKIP_ONBOARDING"] != nil {
            screen = .login
        } else {
            screen = .onboarding
        }
    }

    func finishOnboarding() {
        screen = .login
    }

    func login() {
        if email.isEmpty || password.isEmpty {
            loginError = true
        } else {
            loginError = false
            // Dismiss the keyboard so Home's accessibility tree is clean for the query.
            UIApplication.shared.sendAction(
                #selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
            screen = .home
        }
    }

    func logout() {
        counter = 0
        email = ""
        password = ""
        screen = .onboarding
    }

    func increment() {
        counter += 1
    }

    func handleDeepLink(_ url: URL) {
        switch url.host {
        case "home": screen = .home
        case "login": screen = .login
        default: break
        }
    }
}
