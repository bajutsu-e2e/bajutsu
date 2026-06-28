import CoreLocation
import SwiftUI
import UserNotifications

// Tab: Permissions (SPEC §5.4 / §7) — the one screen that intentionally raises OS-level
// alerts: the notification and location prompts, both SpringBoard (out-of-process).
// Nothing here runs at launch; the prompts fire only on explicit taps.
struct PermissionsView: View {
    @StateObject private var location = LocationAuth()
    @State private var notifStatus = "notDetermined"

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

    // Raises the system location prompt (SpringBoard, out-of-process).
    func request() {
        manager.requestWhenInUseAuthorization()
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
