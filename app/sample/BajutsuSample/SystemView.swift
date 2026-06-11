import SwiftUI
import UIKit
import UserNotifications

// System integration — the out-of-process cases that idb's app-scoped query
// cannot see. The notification prompt lives in SpringBoard and is cleared by the
// run's vision alert guard (--dismiss-alerts); pasteboard is in-app and drivable;
// ShareLink opens the system share sheet (documented, guard/AI territory).
struct SystemView: View {
    @State private var notifStatus = "notDetermined"
    @State private var pasted = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("System")
                    .font(.title)
                    .accessibilityIdentifier("sys.title")

                VStack(alignment: .leading) {
                    Button("Request Notifications") { requestNotifications() }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("sys.requestNotif")
                    Text("Notifications: \(notifStatus)")
                        .accessibilityIdentifier("sys.notif.value")
                        .accessibilityValue(notifStatus)
                    if notifStatus == "authorized" {
                        Text("Granted").accessibilityIdentifier("sys.notif.authorized")
                    }
                }

                VStack(alignment: .leading) {
                    Button("Copy") { UIPasteboard.general.string = "bajutsu-clip" }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("sys.copy")
                    Button("Paste") { pasted = UIPasteboard.general.string ?? "" }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("sys.paste")
                    Text("Pasted: \(pasted)")
                        .accessibilityIdentifier("sys.paste.value")
                        .accessibilityValue(pasted)
                }

                ShareLink(item: "Shared from Bajutsu")
                    .accessibilityIdentifier("sys.share")
            }
            .padding()
        }
    }

    private func requestNotifications() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge]) { granted, _ in
            Task { @MainActor in
                notifStatus = granted ? "authorized" : "denied"
            }
        }
    }
}
