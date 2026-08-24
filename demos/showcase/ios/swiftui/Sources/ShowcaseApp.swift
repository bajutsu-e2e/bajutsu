import BajutsuKit
import SwiftUI
import TipKit
import UIKit

@main
struct ShowcaseApp: App {
    @StateObject private var model = AppModel(env: ProcessInfo.processInfo.environment)

    init() {
        // UI-test hook (SPEC §3): disable animations so condition waits stay tight.
        if ProcessInfo.processInfo.environment["SHOWCASE_UITEST"] != nil {
            UIView.setAnimationsEnabled(false)
        }
        // Reset TipKit's datastore so the tip shows on every launch of this mode, not once per
        // install — TipKit persists "already shown" state, which would make the run order-dependent.
        if ProcessInfo.processInfo.environment["SHOWCASE_TIPKIT"] != nil {
            try? Tips.resetDatastore()
            try? Tips.configure([
                .displayFrequency(.immediate),
                .datastoreLocation(.applicationDefault),
            ])
        }
        // Network capture (SPEC §6): a no-op unless bajutsu injected BAJUTSU_COLLECTOR.
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
