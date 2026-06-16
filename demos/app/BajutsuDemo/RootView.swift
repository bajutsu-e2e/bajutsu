import SwiftUI

struct RootView: View {
    @EnvironmentObject var model: AppModel

    // The auth flow (onboarding -> login) is a modal over the always-present Home, so
    // Home is built once at launch and stays live. Acting on Home right after logging in
    // no longer races a rebuilt-from-scratch view (a plain screen switch would replace
    // the whole subtree on every transition, which idb's accessibility query can briefly
    // see as an empty tree).
    private var authPresented: Binding<Bool> {
        Binding(get: { model.screen != .home }, set: { _ in })
    }

    var body: some View {
        HomeView()
            .fullScreenCover(isPresented: authPresented) { AuthFlowView() }
    }
}
