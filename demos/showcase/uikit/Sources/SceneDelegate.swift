import UIKit

/// Owns the window, the auth gate (modal over the tab controller while not logged in),
/// and deeplink routing (SPEC §4). The tab controller stays mounted underneath so a
/// deeplink can both dismiss the gate and route into a tab.
final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    private let model = AppModel(env: ProcessInfo.processInfo.environment)
    private var tabController: MainTabBarController!

    func scene(
        _ scene: UIScene,
        willConnectTo session: UISceneSession,
        options connectionOptions: UIScene.ConnectionOptions
    ) {
        guard let windowScene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: windowScene)
        self.window = window

        tabController = MainTabBarController(model: model)
        tabController.selectedIndex = model.initialTab.rawValue
        window.rootViewController = tabController
        window.makeKeyAndVisible()

        presentAuthGateIfNeeded(animated: false)

        // A deeplink delivered at launch arrives here, not via openURLContexts.
        if let url = connectionOptions.urlContexts.first?.url {
            handle(url: url)
        }
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        guard let url = URLContexts.first?.url else { return }
        handle(url: url)
    }

    // MARK: - Auth gate

    /// While `screen != home`, cover the tab UI with a full-screen onboarding→login flow,
    /// exactly like the sample app's AuthFlowView (SPEC §5.0).
    private func presentAuthGateIfNeeded(animated: Bool) {
        guard model.screen != .home else { return }
        let auth = AuthFlowController(model: model) { [weak self] in
            guard let self else { return }
            tabController.dismiss(animated: !model.animationsDisabled)
        }
        auth.modalPresentationStyle = .fullScreen
        tabController.present(auth, animated: animated)
    }

    // MARK: - Deeplinks (SPEC §4)

    private func handle(url: URL) {
        // Any deeplink dismisses modals and pops to tab roots first.
        tabController.dismissPresented(animated: false)
        tabController.popAllToRoot()

        switch url.host {
        case "stable": tabController.selectedIndex = AppModel.Tab.stable.rawValue
        case "search": tabController.selectedIndex = AppModel.Tab.search.rawValue
        case "log": tabController.selectedIndex = AppModel.Tab.log.rawValue
        case "profile": tabController.selectedIndex = AppModel.Tab.profile.rawValue
        case "horse":
            // …://horse/<id> — Stable tab, push Horse Detail for <id>.
            tabController.selectedIndex = AppModel.Tab.stable.rawValue
            if let id = Int(url.lastPathComponent) {
                tabController.pushHorseDetail(id: id, model: model)
            }
        case "permissions":
            // …://permissions — Profile tab, push the OS-alert screen.
            tabController.selectedIndex = AppModel.Tab.profile.rawValue
            tabController.pushPermissions()
        default:
            break
        }
    }
}
