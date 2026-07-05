import UIKit

/// Owns the window and deeplink routing (SPEC §4). The app launches into the tab UI; a deeplink
/// dismisses any modal, pops to tab roots, then selects a tab. A deeplink no longer pushes a
/// detail screen (BE-0079): a detail is reached only by tapping its catalog row.
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

        // A deeplink delivered at launch arrives here, not via openURLContexts.
        if let url = connectionOptions.urlContexts.first?.url {
            handle(url: url)
        }
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        guard let url = URLContexts.first?.url else { return }
        handle(url: url)
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
        case "notices": tabController.selectedIndex = AppModel.Tab.notices.rawValue
        case "permissions": tabController.selectedIndex = AppModel.Tab.permissions.rawValue
        default:
            break
        }
    }
}
