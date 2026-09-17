import UserNotifications

extension Notification.Name {
    /// Posted from `willPresent`, right before iOS raises the banner (BE-0416 Unit 5): `simctl
    /// push` itself returns once the payload reaches the device, not once the banner is actually on
    /// screen, so a scenario tapping immediately after `push` races the banner and usually wins —
    /// exercising nothing. `willPresent` is the one real, in-process signal that fires this close to
    /// the banner's own appearance, so a scenario can wait on it deterministically instead of
    /// guessing a fixed delay (prime directive 2).
    static let bajutsuNotificationWillPresent = Notification.Name("bajutsuNotificationWillPresent")
}

/// Opts the app into showing a foreground notification banner (BE-0416 Unit 5).
///
/// Without a `UNUserNotificationCenterDelegate`, iOS never presents anything while the app itself
/// is frontmost — `push.yaml`'s own scenario documents that today's push "does not change the
/// foreground UI" for exactly this reason. `willPresent`'s `.banner` option is what raises the
/// SpringBoard banner BE-0416 dismisses; `ShowcaseApp` holds this delegate as a `static let`, since
/// `UNUserNotificationCenter.delegate` is a weak reference.
final class NotificationPresenter: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        NotificationCenter.default.post(name: .bajutsuNotificationWillPresent, object: nil)
        completionHandler([.banner, .sound])
    }
}
