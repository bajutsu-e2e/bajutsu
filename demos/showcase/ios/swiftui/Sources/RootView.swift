import SwiftUI

struct RootView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        // BE-0114: the SHOWCASE_CONFORMANCE launch env swaps the whole UI for the flat conformance
        // screen; BE-0019: SHOWCASE_GESTURES swaps in the flat pinch/rotate screen. Otherwise the
        // normal tab app (BE-0079) is untouched.
        if let identifiers = model.conformanceIDs {
            ConformanceView(identifiers: identifiers)
        } else if model.gesturesMode {
            GestureView()
        } else {
            MainTabView()
        }
    }
}

struct MainTabView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        // Ids are each tab's own idNamespace (SPEC §9), set on the tabItem Label — the retired idb
        // backend collapsed the tab bar into one opaque group (BE-0107, BE-0290), but the XCUITest
        // backend reads it as the UITabBarItem's accessibilityIdentifier, so a11y builds address tabs
        // by `id` instead of falling back to `label`.
        TabView(selection: $model.selectedTab) {
            StableView()
                .tabItem { Label("Stable", systemImage: "house").accessibilityID("stable") }
                .tag(AppModel.Tab.stable)
            SearchView()
                .tabItem { Label("Search", systemImage: "magnifyingglass").accessibilityID("search") }
                .tag(AppModel.Tab.search)
            LogView()
                .tabItem { Label("Log", systemImage: "square.and.pencil").accessibilityID("log") }
                .tag(AppModel.Tab.log)
            NoticesView()
                .tabItem { Label("Notices", systemImage: "bell").accessibilityID("notice") }
                .tag(AppModel.Tab.notices)
            PermissionsView()
                .tabItem { Label("Permissions", systemImage: "lock.shield").accessibilityID("perm") }
                .tag(AppModel.Tab.permissions)
        }
    }
}
