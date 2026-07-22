import UIKit

/// The five-tab main UI (SPEC §5): Stable, Search, Log, Notices, Permissions, each in its
/// own navigation controller. Also exposes the routing helpers the scene delegate uses for
/// deeplinks (pop to roots, dismiss modals) — a deeplink selects a tab but no longer pushes
/// a detail screen (BE-0079); detail is reached only by tapping a catalog row.
final class MainTabBarController: UITabBarController {
    private let model: AppModel

    private let stableNav: UINavigationController
    private let searchNav: UINavigationController
    private let logNav: UINavigationController
    private let noticesNav: UINavigationController
    private let permissionsNav: UINavigationController

    init(model: AppModel) {
        self.model = model

        stableNav = UINavigationController(rootViewController: StableController(model: model))
        searchNav = UINavigationController(rootViewController: SearchController(model: model))
        logNav = UINavigationController(rootViewController: LogController(model: model))
        noticesNav = UINavigationController(rootViewController: NoticesController(model: model))
        permissionsNav = UINavigationController(rootViewController: PermissionsController())

        super.init(nibName: nil, bundle: nil)

        for nav in [stableNav, searchNav, logNav, noticesNav, permissionsNav] {
            nav.navigationBar.prefersLargeTitles = true
        }
        // Tab tags match AppModel.Tab.rawValue so selectedIndex routing stays in sync.
        // Ids are each tab's own idNamespace (SPEC §9) — the retired idb backend collapsed the tab
        // bar into one opaque group (BE-0107, BE-0290), but the XCUITest backend reads UIBarItem's
        // accessibilityIdentifier, so a11y builds address tabs by `id` instead of falling back to `label`.
        stableNav.tabBarItem = UITabBarItem(title: "Stable", image: UIImage(systemName: "tray.full"), tag: 0)
            .accessibilityID("stable")
        searchNav.tabBarItem = UITabBarItem(title: "Search", image: UIImage(systemName: "magnifyingglass"), tag: 1)
            .accessibilityID("search")
        logNav.tabBarItem = UITabBarItem(title: "Log", image: UIImage(systemName: "square.and.pencil"), tag: 2)
            .accessibilityID("log")
        noticesNav.tabBarItem = UITabBarItem(title: "Notices", image: UIImage(systemName: "bell"), tag: 3)
            .accessibilityID("notice")
        permissionsNav.tabBarItem = UITabBarItem(title: "Permissions", image: UIImage(systemName: "lock.shield"), tag: 4)
            .accessibilityID("perm")

        viewControllers = [stableNav, searchNav, logNav, noticesNav, permissionsNav]
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    // MARK: - Routing (used by the scene delegate's deeplink tab selection)

    private var navs: [UINavigationController] { [stableNav, searchNav, logNav, noticesNav, permissionsNav] }

    func popAllToRoot() {
        for nav in navs { nav.popToRootViewController(animated: false) }
    }

    /// Recursively dismiss any presented modal across all tabs.
    func dismissPresented(animated: Bool) {
        dismiss(animated: animated)
        for nav in navs { nav.dismiss(animated: animated) }
    }
}
