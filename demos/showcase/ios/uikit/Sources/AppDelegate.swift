import BajutsuKit
import UIKit

// BE-0416 Unit 5's foreground-banner fixture (NotificationBannerTargetView, a flat SwiftUI-only
// screen) has no UIKit counterpart, so this target gains no UNUserNotificationCenterDelegate: one
// here would opt every launch into showing a banner with no screen to raise or tap it from,
// unlike the SwiftUI target's own env-gated delegate — a cost this target has no matching benefit
// for.
@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        // UI-test hook: disable animations so condition waits stay tight (SPEC §3).
        if ProcessInfo.processInfo.environment["SHOWCASE_UITEST"] != nil {
            UIView.setAnimationsEnabled(false)
        }
        // Network capture: a no-op unless bajutsu injected BAJUTSU_COLLECTOR.
        BajutsuNet.startIfEnabled()
        return true
    }

    func application(
        _ application: UIApplication,
        configurationForConnecting connectingSceneSession: UISceneSession,
        options: UIScene.ConnectionOptions
    ) -> UISceneConfiguration {
        let config = UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)
        config.delegateClass = SceneDelegate.self
        return config
    }
}
