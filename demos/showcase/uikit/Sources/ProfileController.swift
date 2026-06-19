import UIKit

/// Tab: Profile (SPEC §5.4) — a grouped list that pushes sub-screens (the navigation-
/// depth showcase). Hosts the Normalize toggle and routes to Account / Permissions /
/// About.
final class ProfileController: UIViewController {
    private let model: AppModel

    private let normalizeSwitch = UISwitch()
    private let changedLabel = UILabel()

    init(model: AppModel) {
        self.model = model
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        title = "Profile"
        navigationItem.titleView = makeTitleView("Profile").aid("profile.title")

        normalizeSwitch.isOn = true
        normalizeSwitch.addAction(UIAction { [weak self] _ in self?.normalizeChanged() }, for: .valueChanged)
        normalizeSwitch.aid("profile.normalize")
        normalizeSwitch.mirror(value: "on")
        let normalizeLabel = UILabel()
        normalizeLabel.text = "Normalize"
        let normalizeRow = UIStackView(arrangedSubviews: [normalizeLabel, normalizeSwitch])
        normalizeRow.spacing = 12

        // Shown only after a settings change (SPEC §5.4); hidden until then.
        changedLabel.text = "Settings changed"
        changedLabel.font = .preferredFont(forTextStyle: .footnote)
        changedLabel.textColor = .secondaryLabel
        changedLabel.isHidden = true
        changedLabel.aid("profile.changed")

        let account = makeRow("Account", "profile.openAccount") { [weak self] in self?.openAccount() }
        let permissions = makeRow("Permissions", "profile.openPermissions") { [weak self] in self?.openPermissions() }
        let about = makeRow("About", "profile.openAbout") { [weak self] in self?.openAbout() }

        let stack = UIStackView(arrangedSubviews: [normalizeRow, changedLabel, account, permissions, about])
        stack.axis = .vertical
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 20),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20),
        ])
    }

    private func makeRow(_ title: String, _ id: String, _ action: @escaping () -> Void) -> UIButton {
        var config = UIButton.Configuration.plain()
        config.title = title
        config.contentInsets = .init(top: 8, leading: 0, bottom: 8, trailing: 0)
        let button = UIButton(configuration: config, primaryAction: UIAction { _ in action() })
        button.contentHorizontalAlignment = .leading
        button.aid(id)
        return button
    }

    private func normalizeChanged() {
        normalizeSwitch.mirror(value: normalizeSwitch.isOn ? "on" : "off")
        changedLabel.isHidden = false
    }

    private func openAccount() {
        navigationController?.pushViewController(
            AccountController(model: model), animated: !model.animationsDisabled)
    }

    private func openPermissions() {
        navigationController?.pushViewController(
            PermissionsController(), animated: !model.animationsDisabled)
    }

    private func openAbout() {
        navigationController?.pushViewController(
            AboutController(), animated: !model.animationsDisabled)
    }
}

/// Account sub-screen (SPEC §5.4). Shows the logged-in email; logout returns to the gate.
final class AccountController: UIViewController {
    private let model: AppModel

    init(model: AppModel) {
        self.model = model
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        title = "Account"
        installBackButton()
        navigationItem.titleView = makeTitleView("Account").aid("account.title")

        let emailLabel = UILabel()
        emailLabel.text = "Email: \(model.email)"
        emailLabel.aid("account.email.value")
        emailLabel.mirror(value: model.email)

        let logout = UIButton(type: .system, primaryAction: UIAction(title: "Log out") { [weak self] _ in
            self?.logout()
        })
        logout.configuration = .bordered()
        logout.aid("account.logout")

        let stack = UIStackView(arrangedSubviews: [emailLabel, logout])
        stack.axis = .vertical
        stack.spacing = 24
        stack.alignment = .leading
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 24),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
        ])
    }

    /// Returns to the Login gate by re-presenting the auth flow over the tab UI (SPEC §5.4).
    private func logout() {
        model.logout()
        navigationController?.popToRootViewController(animated: false)
        guard let tab = tabBarController else { return }
        let auth = AuthFlowController(model: model) { [weak tab] in
            tab?.dismiss(animated: true)
        }
        auth.modalPresentationStyle = .fullScreen
        tab.present(auth, animated: !model.animationsDisabled)
    }
}

/// About sub-screen (SPEC §5.4).
final class AboutController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        title = "About"
        installBackButton()
        navigationItem.titleView = makeTitleView("About").aid("about.title")

        let version = UILabel()
        let v = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
        version.text = "Version \(v)"
        version.aid("about.version.value")
        version.mirror(value: v)

        version.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(version)
        NSLayoutConstraint.activate([
            version.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 24),
            version.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
        ])
    }
}
