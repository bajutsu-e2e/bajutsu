import SwiftUI
import UserNotifications

// BE-0416 Unit 5: the on-device fixture that raises a genuine foreground notification banner and
// taps a target inside its measured frame. Reached only when the SHOWCASE_NOTIFICATION_BANNER
// launch env is set (see AppModel), mirroring GestureView above. A flat, nav-bar-free VStack, not a
// tab's own NavigationStack: the target is the very first thing on screen, right below the safe
// area, so its frame starts close to the physical top — inside the frame Unit 1 measured for the
// banner on a 6.3-inch device — regardless of a nav bar's own (title-mode-dependent) height.
//
// `permissions` cannot pre-grant notification authorization on iOS (docs/scenarios.md), and this
// flat screen has no Permissions tab to request it from interactively, so the request fires once,
// automatically, on appear — the scenario answers the resulting SpringBoard prompt with a
// `handleSystemAlert` step, exactly as `permission_system_alert.yaml` does.
struct NotificationBannerTargetView: View {
    @State private var permissionStatus = "notDetermined"
    @State private var tapped = false
    // Flipped by `willPresent` (NotificationPresenter), the one in-process signal that fires this
    // close to the banner's own appearance — the condition a scenario waits on instead of guessing
    // a fixed delay after `push` (BE-0416 Unit 5; see `.bajutsuNotificationWillPresent`'s own doc).
    @State private var presented = false

    var body: some View {
        VStack(spacing: 16) {
            Button("Tap under the banner") { tapped = true }
                .frame(maxWidth: .infinity, minHeight: 64)
                .background(Color.gray.opacity(0.25))
                .contentShape(Rectangle())
                .accessibilityID("notif.bannerTarget")
                .accessibilityStateValue(tapped ? "tapped" : "idle")
            Text(tapped ? "tapped" : "idle")
                .foregroundStyle(.secondary)
                .accessibilityID("notif.bannerTarget.value")
                .accessibilityStateValue(tapped ? "tapped" : "idle")
            Text("Permission: \(permissionStatus)")
                .foregroundStyle(.secondary)
                .accessibilityID("notif.permission.value")
                .accessibilityStateValue(permissionStatus)
            if presented {
                Text("Presented")
                    .accessibilityID("notif.presented")
            }
            Spacer()
        }
        .padding(.top, 8)
        .onAppear { requestAuthorization() }
        .onReceive(NotificationCenter.default.publisher(for: .bajutsuNotificationWillPresent)) { _ in
            presented = true
        }
    }

    private func requestAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) {
            granted, _ in
            Task { @MainActor in
                permissionStatus = granted ? "authorized" : "denied"
            }
        }
    }
}
