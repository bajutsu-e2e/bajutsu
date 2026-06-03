import SwiftUI
import UIKit

@main
struct SimPilotSampleApp: App {
    @StateObject private var model = AppModel(env: ProcessInfo.processInfo.environment)

    init() {
        // UI-test hook: disable animations so condition waits stay tight.
        if ProcessInfo.processInfo.environment["SAMPLE_UITEST"] != nil {
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
