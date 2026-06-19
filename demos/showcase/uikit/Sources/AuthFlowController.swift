import UIKit

/// The modal auth gate: an embedded nav controller that runs Onboarding → Login.
/// Calls `onLoggedIn` once the model transitions to .home; the scene delegate then
/// dismisses the gate (SPEC §5.0).
final class AuthFlowController: UINavigationController {
    private let model: AppModel
    private let onLoggedIn: () -> Void

    init(model: AppModel, onLoggedIn: @escaping () -> Void) {
        self.model = model
        self.onLoggedIn = onLoggedIn
        super.init(nibName: nil, bundle: nil)
        setNavigationBarHidden(true, animated: false)

        let login = LoginController(model: model, onLoggedIn: onLoggedIn)
        if model.screen == .onboarding {
            let onboarding = OnboardingController { [weak self] in
                self?.pushViewController(login, animated: !model.animationsDisabled)
            }
            viewControllers = [onboarding]
        } else {
            viewControllers = [login]
        }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}

// MARK: - Onboarding

final class OnboardingController: UIViewController {
    private let onContinue: () -> Void

    init(onContinue: @escaping () -> Void) {
        self.onContinue = onContinue
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground

        let title = UILabel()
        title.text = "Welcome"
        title.font = .preferredFont(forTextStyle: .largeTitle)
        title.textAlignment = .center
        title.aid("onboarding.title")

        let cont = UIButton(type: .system, primaryAction: UIAction(title: "Continue") { [weak self] _ in
            self?.onContinue()
        })
        cont.configuration = .borderedProminent()
        cont.aid("onboarding.continue")

        let stack = UIStackView(arrangedSubviews: [title, cont])
        stack.axis = .vertical
        stack.spacing = 24
        stack.alignment = .center
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])
    }
}

// MARK: - Login

final class LoginController: UIViewController {
    private let model: AppModel
    private let onLoggedIn: () -> Void

    private let emailField = UITextField()
    // Secure entry, but deliberately NOT a .password/.newPassword content type —
    // that is what suppresses the iOS "Save Password?" sheet (SPEC §7).
    private let passwordField = UITextField()
    private let errorLabel = UILabel()

    init(model: AppModel, onLoggedIn: @escaping () -> Void) {
        self.model = model
        self.onLoggedIn = onLoggedIn
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground

        emailField.placeholder = "Email"
        emailField.borderStyle = .roundedRect
        emailField.keyboardType = .emailAddress
        emailField.autocapitalizationType = .none
        emailField.autocorrectionType = .no
        emailField.textContentType = .emailAddress
        emailField.aid("auth.email")

        passwordField.placeholder = "Password"
        passwordField.borderStyle = .roundedRect
        passwordField.isSecureTextEntry = true
        // textContentType intentionally left unset (SPEC §7).
        passwordField.aid("auth.password")

        let submit = UIButton(type: .system, primaryAction: UIAction(title: "Sign In") { [weak self] _ in
            self?.attemptLogin()
        })
        submit.configuration = .borderedProminent()
        submit.aid("auth.submit")

        errorLabel.text = "Email and password are required"
        errorLabel.textColor = .systemRed
        errorLabel.font = .preferredFont(forTextStyle: .footnote)
        errorLabel.numberOfLines = 0
        errorLabel.isHidden = true
        errorLabel.aid("auth.error")

        let stack = UIStackView(arrangedSubviews: [emailField, passwordField, submit, errorLabel])
        stack.axis = .vertical
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
        ])
    }

    private func attemptLogin() {
        let email = emailField.text ?? ""
        let password = passwordField.text ?? ""
        // Empty email or password → show the validation error; otherwise dismiss the
        // keyboard (clean home tree) and go Home (SPEC §5.0).
        guard !email.isEmpty, !password.isEmpty else {
            errorLabel.isHidden = false
            return
        }
        errorLabel.isHidden = true
        view.endEditing(true)
        model.login(email: email)
        onLoggedIn()
    }
}
