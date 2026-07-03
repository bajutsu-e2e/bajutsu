import SwiftUI

struct RootView: View {
    var body: some View {
        MainTabView()
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
