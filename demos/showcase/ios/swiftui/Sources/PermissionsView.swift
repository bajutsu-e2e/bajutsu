import CoreLocation
import SafariServices
import SwiftUI
import UIKit
import UserNotifications

// Tab: Permissions (SPEC §5.4 / §7) — the OS-integration screen. It owns the deliberate
// OS-level alerts (notification + location prompts, both SpringBoard/out-of-process), and a
// System section: a Copy → Paste pasteboard round-trip that the backend can drive and assert.
// (Reading a pasteboard seeded by another process trips iOS's paste-consent prompt; a value this
// app itself wrote reads back silently. Both paths are exercised: system.yaml copies in-app,
// paste_system_alert.yaml seeds the pasteboard with `setClipboard` and answers the prompt.)
// Nothing here runs at launch; the prompts fire only on explicit taps.
struct PermissionsView: View {
    @EnvironmentObject var model: AppModel
    @StateObject private var location = LocationAuth()
    @StateObject private var browser = BrowserPresenter()
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
                // back into sys.paste.value — pasteboard state the backend's app-scoped query cannot see.
                Section("System") {
                    Button("Copy") { UIPasteboard.general.string = "bajutsu-clip" }
                        .accessibilityID("sys.copy")
                    Button("Paste") { readPasteboard() }
                        .accessibilityID("sys.paste")
                    Text("Pasted: \(pasted)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("sys.paste.value")
                        .accessibilityStateValue(pasted)

                    // In-app browser (SPEC §5.4): SFSafariViewController draws its UI in another
                    // process, so while it is up the app's own tree is not what the backend reads.
                    Button("Open Browser") {
                        browser.open(model.browserURL, animated: !model.animationsDisabled)
                    }
                    .accessibilityID("sys.openBrowser")
                    Text("Browser: \(browser.status)")
                        .foregroundStyle(.secondary)
                        .accessibilityID("sys.browser.value")
                        .accessibilityStateValue(browser.status)
                }
            }
            .navigationTitle("Permissions")
        }
    }

    // Read off the main thread. `UIPasteboard.general.string` blocks its caller for as long as
    // iOS's cross-process paste-consent prompt is up, and a blocked main thread never lets
    // XCUITest's tap return — so the very step that would answer that prompt could never run
    // (BE-0369). The value publishes when the read returns, which is what the scenario waits for.
    // A read that comes back with no string — a denied consent, or an empty pasteboard — publishes
    // "(none)" rather than "", so a `choice: deny` step leaves a positive condition to wait for
    // instead of a field indistinguishable from one never read.
    private func readPasteboard() {
        DispatchQueue.global(qos: .userInitiated).async {
            let text = UIPasteboard.general.string ?? "(none)"
            DispatchQueue.main.async { pasted = text }
        }
    }

    // Raises the SpringBoard notification prompt — out-of-process, so an in-app accessibility query
    // cannot see it; the run's vision alert guard / systemAlertHandling clears it.
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
        // Assigning the delegate itself triggers an immediate `locationManagerDidChangeAuthorization`
        // callback reporting the current status (CoreLocation's documented behavior since iOS 14),
        // so `status` already reflects reality — including a pre-grant (BE-0276) — before `request()`
        // is ever called; no separate read is needed here.
        manager.delegate = self
    }

    // Raises the system location prompt (SpringBoard, out-of-process). When authorization was
    // already decided before launch (BE-0276's `permissions:` pre-grant), this call is a
    // documented no-op with no state transition, so `locationManagerDidChangeAuthorization` never
    // fires for it — `status` is already correct by now regardless (from the delegate-assignment
    // announcement above), but resync here too as a defensive belt-and-suspenders rather than
    // relying on that alone.
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

// Presents the in-app browser and mirrors the one fact about it the app itself can observe: whether
// the page finished loading. While `SFSafariViewController` is up, everything on screen belongs to
// another process — the backend reads that tree from the browser's own application handle, not from
// this app's — so the app-side signal is deliberately narrow: the load outcome, published after the
// browser has been dismissed and this screen is back in the tree for a scenario to assert on.
private final class BrowserPresenter: NSObject, ObservableObject, SFSafariViewControllerDelegate {
    @Published var status = "idle"

    func open(_ raw: String, animated: Bool) {
        // Reset first: a second presentation must not let a wait for `loaded` be satisfied by the
        // previous one's outcome, before this page has loaded at all.
        status = "idle"
        guard let url = URL(string: raw) else {
            status = "badURL"
            return
        }
        let controller = SFSafariViewController(url: url)
        controller.delegate = self
        topViewController()?.present(controller, animated: animated)
    }

    func safariViewController(
        _ controller: SFSafariViewController, didCompleteInitialLoad didLoadSuccessfully: Bool
    ) {
        status = didLoadSuccessfully ? "loaded" : "loadFailed"
    }

    // SwiftUI has no presentation for a UIKit controller that is not a view, so reach the window's
    // own topmost controller and present from there.
    private func topViewController() -> UIViewController? {
        let root = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first { $0.isKeyWindow }?
            .rootViewController
        var top = root
        while let presented = top?.presentedViewController { top = presented }
        return top
    }
}
