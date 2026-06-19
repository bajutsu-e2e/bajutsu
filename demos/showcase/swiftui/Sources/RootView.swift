import SwiftUI

struct RootView: View {
    @EnvironmentObject var model: AppModel

    // The auth flow (onboarding -> login) is a modal over the always-present main UI, so
    // the tabs and their NavigationStacks are built once at launch and stay live. A tap
    // right after logging in no longer races a rebuilt-from-scratch view tree.
    private var authPresented: Binding<Bool> {
        Binding(get: { model.screen != .home }, set: { _ in })
    }

    var body: some View {
        MainTabView()
            .fullScreenCover(isPresented: authPresented) { AuthFlowView() }
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
            ProfileView()
                .tabItem { Label("Profile", systemImage: "person.crop.circle") }
                .tag(AppModel.Tab.profile)
        }
    }
}
