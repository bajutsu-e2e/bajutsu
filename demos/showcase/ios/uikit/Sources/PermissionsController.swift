import CoreLocation
import SafariServices
import UIKit
import UserNotifications

/// Permissions (SPEC §5.4 / §7) — the OS-integration screen. It owns the deliberate
/// OS-level alerts (the notification and location prompts, which fire only on explicit taps here,
/// never at launch: the canonical fixture for the run's vision alert guard / systemAlertHandling),
/// plus a System section: a Copy → Paste pasteboard round-trip the backend can drive and assert.
/// (Reading a pasteboard seeded by another process trips iOS's paste-consent prompt; a value this
/// app wrote reads back silently. Both paths are exercised: system.yaml copies in-app,
/// paste_system_alert.yaml seeds the pasteboard with `setClipboard` and answers the prompt.)
final class PermissionsController: UIViewController, CLLocationManagerDelegate,
    SFSafariViewControllerDelegate {
    private let model: AppModel
    private let notifValueLabel = UILabel()
    private let notifAuthorizedLabel = UILabel()
    private let locationValueLabel = UILabel()
    private let pastedValueLabel = UILabel()
    private let browserValueLabel = UILabel()

    private let locationManager = CLLocationManager()

    init(model: AppModel) {
        self.model = model
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        title = "Permissions"

        locationManager.delegate = self

        let requestNotif = UIButton(type: .system, primaryAction: UIAction(title: "Request notifications") { [weak self] _ in
            self?.requestNotifications()
        })
        requestNotif.contentHorizontalAlignment = .leading
        requestNotif.accessibilityID("perm.requestNotif")

        notifValueLabel.accessibilityID("perm.notif.value")

        // Shown only once notifications are granted — a positive condition the run can
        // wait for (SPEC §5.4). Hidden until then.
        notifAuthorizedLabel.text = "Notifications authorized"
        notifAuthorizedLabel.textColor = .systemGreen
        notifAuthorizedLabel.isHidden = true
        notifAuthorizedLabel.accessibilityID("perm.notif.authorized")

        let requestLocation = UIButton(type: .system, primaryAction: UIAction(title: "Request location") { [weak self] _ in
            self?.requestLocation()
        })
        requestLocation.contentHorizontalAlignment = .leading
        requestLocation.accessibilityID("perm.requestLocation")

        locationValueLabel.accessibilityID("perm.location.value")

        // Pasteboard round-trip (SPEC §5.4): Copy writes a known string, Paste reads it back
        // into sys.paste.value — pasteboard state the backend's app-scoped query cannot see.
        let copy = UIButton(type: .system, primaryAction: UIAction(title: "Copy") { _ in
            UIPasteboard.general.string = "bajutsu-clip"
        })
        copy.contentHorizontalAlignment = .leading
        copy.accessibilityID("sys.copy")

        let paste = UIButton(type: .system, primaryAction: UIAction(title: "Paste") { [weak self] _ in
            self?.paste()
        })
        paste.contentHorizontalAlignment = .leading
        paste.accessibilityID("sys.paste")

        pastedValueLabel.text = "Pasted: "
        pastedValueLabel.accessibilityID("sys.paste.value")
        pastedValueLabel.accessibilityStateValue("")

        // In-app browser (SPEC §5.4): SFSafariViewController draws its UI in another process, so
        // while it is up the app's own tree is not what the backend reads.
        let openBrowser = UIButton(type: .system, primaryAction: UIAction(title: "Open Browser") { [weak self] _ in
            self?.openBrowser()
            self?.armNotificationRequest()
        })
        openBrowser.contentHorizontalAlignment = .leading
        openBrowser.accessibilityID("sys.openBrowser")

        browserValueLabel.text = "Browser: idle"
        browserValueLabel.accessibilityID("sys.browser.value")
        browserValueLabel.accessibilityStateValue("idle")

        // A grouped form mirroring the SwiftUI twin: Notifications, Location, and System sections.
        installGroupedForm([
            makeSectionHeader("Notifications"),
            makeSectionCard([requestNotif, notifValueLabel, notifAuthorizedLabel]),
            makeSectionHeader("Location"),
            makeSectionCard([requestLocation, locationValueLabel]),
            makeSectionHeader("System"),
            makeSectionCard([copy, paste, pastedValueLabel, openBrowser, browserValueLabel]),
        ])

        refreshNotifStatus()
        setLocationValue(locationManager.authorizationStatus)
    }

    // MARK: - Notifications

    /// Raises the notification request on a delay once the in-app browser is up, when the run asked
    /// for it (`SHOWCASE_NOTIF_AFTER_BROWSER`). This is how the save-password alert iOS raises for a
    /// browser sign-in comes to be stacked *under* a SpringBoard alert: the scenario cannot tap its
    /// way into that state, since an element tap made while a system alert is showing fails by
    /// design, so the second prompt has to arrive on the app's own schedule. No default: every other
    /// scenario opens the browser with nothing behind it.
    private func armNotificationRequest() {
        guard let delay = model.notifyAfterBrowser else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.requestNotifications()
        }
    }

    private func requestNotifications() {
        // Raises the SpringBoard notification prompt (out-of-process).
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { [weak self] _, _ in
            DispatchQueue.main.async { self?.refreshNotifStatus() }
        }
    }

    private func refreshNotifStatus() {
        UNUserNotificationCenter.current().getNotificationSettings { [weak self] settings in
            DispatchQueue.main.async { self?.setNotifValue(settings.authorizationStatus) }
        }
    }

    private func setNotifValue(_ status: UNAuthorizationStatus) {
        let text: String
        switch status {
        case .authorized, .provisional, .ephemeral: text = "authorized"
        case .denied: text = "denied"
        default: text = "notDetermined"
        }
        notifValueLabel.text = "Notifications: \(text)"
        notifValueLabel.accessibilityStateValue(text)
        notifAuthorizedLabel.isHidden = (text != "authorized")
    }

    // MARK: - Location

    private func requestLocation() {
        // Raises the system location prompt (also SpringBoard).
        locationManager.requestWhenInUseAuthorization()
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        setLocationValue(manager.authorizationStatus)
    }

    private func setLocationValue(_ status: CLAuthorizationStatus) {
        let text: String
        switch status {
        case .authorizedWhenInUse, .authorizedAlways: text = "authorizedWhenInUse"
        case .denied, .restricted: text = "denied"
        default: text = "notDetermined"
        }
        locationValueLabel.text = "Location: \(text)"
        locationValueLabel.accessibilityStateValue(text)
    }

    // MARK: - System (device-state mirror)

    // Read off the main thread. `UIPasteboard.general.string` blocks its caller for as long as
    // iOS's cross-process paste-consent prompt is up, and a blocked main thread never lets
    // XCUITest's tap return — so the very step that would answer that prompt could never run
    // (BE-0369). The value publishes when the read returns, which is what the scenario waits for.
    // A read that comes back with no string — a denied consent, or an empty pasteboard — publishes
    // "(none)" rather than "", so a `choice: deny` step leaves a positive condition to wait for
    // instead of a field indistinguishable from one never read.
    private func paste() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let text = UIPasteboard.general.string ?? "(none)"
            DispatchQueue.main.async { self?.setPastedValue(text) }
        }
    }

    private func setPastedValue(_ text: String) {
        pastedValueLabel.text = "Pasted: \(text)"
        pastedValueLabel.accessibilityStateValue(text)
    }

    // MARK: - In-app browser

    // Mirrors the one fact about the browser this app can observe: whether the page finished
    // loading. Everything the browser draws belongs to another process — the backend reads that
    // tree from the browser's own application handle, not from this app's — so the app-side signal
    // is deliberately narrow, and it is readable again only once the browser has been dismissed and
    // this screen is back in the tree.
    private func openBrowser() {
        // Reset first: a second presentation must not let a wait for `loaded` be satisfied by the
        // previous one's outcome, before this page has loaded at all.
        setBrowserValue("idle")
        guard let url = URL(string: model.browserURL) else {
            setBrowserValue("badURL")
            return
        }
        let controller = SFSafariViewController(url: url)
        controller.delegate = self
        present(controller, animated: !model.animationsDisabled)
    }

    func safariViewController(
        _ controller: SFSafariViewController, didCompleteInitialLoad didLoadSuccessfully: Bool
    ) {
        setBrowserValue(didLoadSuccessfully ? "loaded" : "loadFailed")
    }

    private func setBrowserValue(_ text: String) {
        browserValueLabel.text = "Browser: \(text)"
        browserValueLabel.accessibilityStateValue(text)
    }
}
