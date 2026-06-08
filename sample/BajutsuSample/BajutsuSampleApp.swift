import BajutsuKit
import SwiftUI
import UIKit

@main
struct BajutsuSampleApp: App {
    @StateObject private var model = AppModel(env: ProcessInfo.processInfo.environment)

    init() {
        // UI-test hook: disable animations so condition waits stay tight.
        if ProcessInfo.processInfo.environment["SAMPLE_UITEST"] != nil {
            UIView.setAnimationsEnabled(false)
        }
        // Network capture: a no-op unless bajutsu injected BAJUTSU_COLLECTOR.
        BajutsuNet.startIfEnabled()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .onOpenURL { model.handleDeepLink($0) }
        }
    }
}
