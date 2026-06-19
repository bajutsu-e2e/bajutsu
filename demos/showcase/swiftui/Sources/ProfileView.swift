import CoreLocation
import SwiftUI
import UserNotifications

struct ProfileView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        // Path bound to the model so …://permissions can push Permissions directly.
        NavigationStack(path: $model.profilePath) {
            Form {
                Section("Settings") {
                    Toggle("Normalize", isOn: Binding(
                        get: { model.normalize },
                        set: { _ in model.toggleNormalize() }
                    ))
                    .aid("profile.normalize")
                    Text(model.normalize ? "Normalize on" : "Normalize off")
                        .foregroundStyle(.secondary)
                        .aid("profile.normalize.value")
                        .aidValue(model.normalize ? "on" : "off")
                    if model.profileChanged {
                        Text("Settings changed")
                            .foregroundStyle(.secondary)
                            .aid("profile.changed")
                    }
                }

                Section {
                    NavigationLink(value: ProfileRoute.account) {
                        Text("Account")
                    }
                    .aid("profile.openAccount")
                    NavigationLink(value: ProfileRoute.permissions) {
                        Text("Permissions")
                    }
                    .aid("profile.openPermissions")
                    NavigationLink(value: ProfileRoute.about) {
                        Text("About")
                    }
                    .aid("profile.openAbout")
                }
            }
            .navigationTitle("Profile")
            .navigationDestination(for: ProfileRoute.self) { route in
                switch route {
                case .account: AccountView()
                case .permissions: PermissionsView()
                case .about: AboutView()
                }
            }
        }
        .aid("profile.title")
    }
}

struct AccountView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Form {
            Text("Account")
                .font(.title2)
                .aid("account.title")
            Text(model.accountEmail)
                .foregroundStyle(.secondary)
                .aid("account.email.value")
                .aidValue(model.accountEmail)
            Button("Log out", role: .destructive) { model.logout() }
                .aid("account.logout")
        }
        .navigationTitle("Account")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// SPEC §5.4 / §7: the one screen that intentionally raises OS-level alerts — the
// notification and location prompts, both SpringBoard (out-of-process). Nothing here
// runs at launch; the prompts fire only on explicit taps.
struct PermissionsView: View {
    @StateObject private var location = LocationAuth()
    @State private var notifStatus = "notDetermined"

    var body: some View {
        Form {
            Text("Permissions")
                .font(.title2)
                .aid("perm.title")

            Section("Notifications") {
                Button("Request Notifications") { requestNotifications() }
                    .aid("perm.requestNotif")
                Text("Notifications: \(notifStatus)")
                    .foregroundStyle(.secondary)
                    .aid("perm.notif.value")
                    .aidValue(notifStatus)
                if notifStatus == "authorized" {
                    // A positive condition the run can wait for once granted.
                    Text("Granted")
                        .aid("perm.notif.authorized")
                }
            }

            Section("Location") {
                Button("Request Location") { location.request() }
                    .aid("perm.requestLocation")
                Text("Location: \(location.status)")
                    .foregroundStyle(.secondary)
                    .aid("perm.location.value")
                    .aidValue(location.status)
            }
        }
        .navigationTitle("Permissions")
        .navigationBarTitleDisplayMode(.inline)
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

struct AboutView: View {
    var body: some View {
        Form {
            Text("About")
                .font(.title2)
                .aid("about.title")
            Text("Version 1.0")
                .foregroundStyle(.secondary)
                .aid("about.version.value")
                .aidValue("1.0")
        }
        .navigationTitle("About")
        .navigationBarTitleDisplayMode(.inline)
        // Reserved nav.back on the explicit back control (SPEC §5.4).
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                BackButton()
            }
        }
        .navigationBarBackButtonHidden()
    }
}

// A back control carrying the reserved nav.back id; pops via the environment dismiss.
private struct BackButton: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Button {
            dismiss()
        } label: {
            Label("Back", systemImage: "chevron.backward")
        }
        .aid("nav.back")
    }
}
