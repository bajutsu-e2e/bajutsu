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
        TabView(selection: $model.selectedTab) {
            StableView()
                .tabItem { Label("Stable", systemImage: "house") }
                .tag(AppModel.Tab.stable)
            SearchView()
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
                .tag(AppModel.Tab.search)
            LogView()
                .tabItem { Label("Log", systemImage: "square.and.pencil") }
                .tag(AppModel.Tab.log)
            NoticesView()
                .tabItem { Label("Notices", systemImage: "bell") }
                .tag(AppModel.Tab.notices)
            PermissionsView()
                .tabItem { Label("Permissions", systemImage: "lock.shield") }
                .tag(AppModel.Tab.permissions)
        }
    }
}
