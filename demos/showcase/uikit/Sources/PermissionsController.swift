import CoreLocation
import UIKit
import UserNotifications

/// Permissions (SPEC §5.4 / §7) — the ONE screen that intentionally raises OS-level
/// alerts. Both prompts fire only on explicit taps here, never at launch: the canonical
/// fixture for the run's vision alert guard / dismissAlerts.
final class PermissionsController: UIViewController, CLLocationManagerDelegate {
    private let notifValueLabel = UILabel()
    private let notifAuthorizedLabel = UILabel()
    private let locationValueLabel = UILabel()

    private let locationManager = CLLocationManager()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        title = "Permissions"
        installBackButton()
        navigationItem.titleView = makeTitleView("Permissions").accessibilityID("perm.title")

        locationManager.delegate = self

        let requestNotif = UIButton(type: .system, primaryAction: UIAction(title: "Request notifications") { [weak self] _ in
            self?.requestNotifications()
        })
        requestNotif.configuration = .bordered()
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
        requestLocation.configuration = .bordered()
        requestLocation.accessibilityID("perm.requestLocation")

        locationValueLabel.accessibilityID("perm.location.value")

        let stack = UIStackView(arrangedSubviews: [
            requestNotif, notifValueLabel, notifAuthorizedLabel,
            requestLocation, locationValueLabel,
        ])
        stack.axis = .vertical
        stack.spacing = 18
        stack.alignment = .leading
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 24),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
        ])

        refreshNotifStatus()
        setLocationValue(locationManager.authorizationStatus)
    }

    // MARK: - Notifications

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
}
