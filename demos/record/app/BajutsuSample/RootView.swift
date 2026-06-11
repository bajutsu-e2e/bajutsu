import SwiftUI

struct RootView: View {
    @EnvironmentObject var model: AppModel

    // The auth flow (onboarding -> login) is a modal over the always-present Home, so
    // Home's NavigationStack and toolbar are built once at launch and stay live. Tapping
    // the toolbar right after logging in no longer races a rebuilt-from-scratch view
    // (the old RootView switch replaced the whole subtree on every screen change).
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
            HomeView()
                .tabItem { Label("Home", systemImage: "house") }
                .tag(0)
            ComponentsView()
                .tabItem { Label("Components", systemImage: "square.grid.2x2") }
                .tag(1)
            ControlsView()
                .tabItem { Label("Controls", systemImage: "slider.horizontal.3") }
                .tag(2)
            TextInputView()
                .tabItem { Label("Text", systemImage: "textformat") }
                .tag(3)
            ListsNavView()
                .tabItem { Label("Lists", systemImage: "list.bullet") }
                .tag(4)
            GesturesView()
                .tabItem { Label("Gestures", systemImage: "hand.draw") }
                .tag(5)
            PresentationView()
                .tabItem { Label("Present", systemImage: "rectangle.portrait.on.rectangle.portrait") }
                .tag(6)
            AsyncView()
                .tabItem { Label("Async", systemImage: "clock.arrow.circlepath") }
                .tag(7)
            SystemView()
                .tabItem { Label("System", systemImage: "gearshape.2") }
                .tag(8)
            NetworkView()
                .tabItem { Label("Network", systemImage: "network") }
                .tag(9)
        }
    }
}
