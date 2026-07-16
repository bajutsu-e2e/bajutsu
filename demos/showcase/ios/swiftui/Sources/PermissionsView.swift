import CoreLocation
import SwiftUI
import UIKit
import UserNotifications

// Tab: Permissions (SPEC §5.4 / §7) — the OS-integration screen. It owns the two deliberate
// OS-level alerts (notification + location prompts, both SpringBoard/out-of-process), and a
// System section: an in-app Copy → Paste pasteboard round-trip that idb can drive and assert.
// (Reading a pasteboard seeded by another process trips iOS's paste-permission prompt; a value
// this app itself wrote reads back silently, so the round-trip stays deterministic.)
// Nothing here runs at launch; the prompts fire only on explicit taps.
struct PermissionsView: View {
    @StateObject private var location = LocationAuth()
    @State private var notifStatus = "notDetermined"
    @State private var pasted = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Notifications") {
                    Button("Request Notifications") { requestNotifications() }
                        .accessibilityID("perm.requestNotif")
                    Text("Notifications: \(notifStatus)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("perm.notif.value")
                        .accessibilityStateValue(notifStatus)
                    if notifStatus == "authorized" {
                        // A positive condition the run can wait for once granted.
                        Text("Granted")
                            .accessibilityID("perm.notif.authorized")
                    }
                }

                Section("Location") {
                    Button("Request Location") { location.request() }
                        .accessibilityID("perm.requestLocation")
                    Text("Location: \(location.status)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("perm.location.value")
                        .accessibilityStateValue(location.status)
                }

                // Pasteboard round-trip (SPEC §5.4): Copy writes a known string, Paste reads it
                // back into sys.paste.value — pasteboard state idb's app-scoped query cannot see.
                Section("System") {
                    Button("Copy") { UIPasteboard.general.string = "bajutsu-clip" }
                        .accessibilityID("sys.copy")
                    Button("Paste") { pasted = UIPasteboard.general.string ?? "" }
                        .accessibilityID("sys.paste")
                    Text("Pasted: \(pasted)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("sys.paste.value")
                        .accessibilityStateValue(pasted)
                }
            }
            .navigationTitle("Permissions")
        }
    }

    // Raises the SpringBoard notification prompt — idb cannot see it; the run's vision
    // alert guard / dismissAlerts clears it.
    private func requestNotifications() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge]) { granted, _ in
            Task { @MainActor in
                notifStatus = granted ? "authorized" : "denied"
            }
        }
    }
}

// Minimal CLLocationManager delegate: just enough to raise the when-in-use prompt and
// mirror the resulting authorization to perm.location.value.
final class LocationAuth: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published var status = "notDetermined"
    private let manager = CLLocationManager()

    override init() {
        super.init()
        manager.delegate = self
        status = Self.string(manager.authorizationStatus)
    }

    // Raises the system location prompt (SpringBoard, out-of-process). When authorization was
    // already decided before launch (BE-0276's `permissions:` pre-grant), this call is a
    // documented no-op with no state transition, so `locationManagerDidChangeAuthorization`
    // never fires — resync here too rather than relying on the delegate alone.
    func request() {
        manager.requestWhenInUseAuthorization()
        status = Self.string(manager.authorizationStatus)
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        status = Self.string(manager.authorizationStatus)
    }

    private static func string(_ status: CLAuthorizationStatus) -> String {
        switch status {
        case .authorizedWhenInUse, .authorizedAlways: return "authorizedWhenInUse"
        case .denied, .restricted: return "denied"
        default: return "notDetermined"
        }
    }
}
