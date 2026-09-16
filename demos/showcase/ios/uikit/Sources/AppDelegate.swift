import BajutsuKit
import UIKit
import UserNotifications

@main
final class AppDelegate: UIResponder, UIApplicationDelegate, UNUserNotificationCenterDelegate {
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
        // Without this, iOS never presents anything while the app itself is frontmost — the
        // foreground banner BE-0416 dismisses (BE-0416 Unit 5).
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
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
