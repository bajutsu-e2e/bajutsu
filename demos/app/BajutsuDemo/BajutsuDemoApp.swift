import SwiftUI
import UIKit

@main
struct BajutsuDemoApp: App {
    @StateObject private var model = AppModel(env: ProcessInfo.processInfo.environment)

    init() {
        // UI-test hook: disable animations so Bajutsu's condition waits stay tight and
        // each screen transition settles deterministically.
        if ProcessInfo.processInfo.environment["DEMO_UITEST"] != nil {
            UIView.setAnimationsEnabled(false)
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .onOpenURL { model.handleDeepLink($0) }
        }
    }
}
