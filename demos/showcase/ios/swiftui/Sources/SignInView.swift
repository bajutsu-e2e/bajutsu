import SwiftUI
import UIKit

// The app's own sign-in screen (SPEC §5.4) — the *native* route iOS raises its "Save Password" alert
// from, as opposed to the in-app browser's. Filling `.username` / `.password` fields and submitting
// is what makes Password AutoFill offer to save the credential.
//
// Two things about the construction are load-bearing, both measured: the fields are plain
// `UITextField`s in a plain view controller (AutoFill's heuristics did not engage for the same
// content types wrapped inside a SwiftUI `Form`), and submitting **pushes a different view
// controller** so the credential-bearing view actually goes away, which is the event AutoFill
// watches for. Resigning the fields in place, or swapping a SwiftUI section under them, produced no
// prompt at all. So the screen is UIKit even here, and SwiftUI only hosts it.
//
// Unlike the browser route, this one works only where iOS can verify a `webcredentials:` associated
// domain: declaring the entitlement is not enough, and with no HTTPS server publishing
// `apple-app-site-association` and no certificate authority in the Simulator's keychain no prompt
// appears. `make -C demos/showcase e2e-savepassword-native` stands all of that up around the run;
// every other lane simply sees a sign-in screen whose submit advances the state and nothing more.
struct SignInView: View {
    var body: some View {
        SignInHost()
            .ignoresSafeArea()
    }
}

/// Hosts the UIKit sign-in flow, navigation controller and all.
private struct SignInHost: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> UINavigationController {
        UINavigationController(rootViewController: SignInController())
    }

    func updateUIViewController(_ controller: UINavigationController, context: Context) {}
}

/// The sign-in form: two content-typed fields and a submit that pushes the signed-in screen.
private final class SignInController: UIViewController {
    private let username = UITextField()
    private let password = UITextField()

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Sign In"
        view.backgroundColor = .systemBackground

        username.placeholder = "Username"
        username.borderStyle = .roundedRect
        username.textContentType = .username
        username.autocapitalizationType = .none
        username.autocorrectionType = .no
        username.accessibilityID("signin.username")

        password.placeholder = "Password"
        password.borderStyle = .roundedRect
        password.isSecureTextEntry = true
        password.textContentType = .password
        password.accessibilityID("signin.password")

        let submit = UIButton(
            type: .system,
            primaryAction: UIAction(title: "Sign In") { [weak self] _ in self?.signIn() }
        )
        submit.accessibilityID("signin.submit")

        let status = UILabel()
        status.text = "Sign in: idle"
        status.accessibilityID("signin.value")
        status.accessibilityStateValue("idle")

        let stack = UIStackView(arrangedSubviews: [username, password, submit, status])
        stack.axis = .vertical
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 32),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -32),
        ])
    }

    private func signIn() {
        view.endEditing(true)
        navigationController?.pushViewController(SignedInController(), animated: false)
    }
}

/// Where the sign-in lands. Pushing it is what takes the credential-bearing view off screen.
private final class SignedInController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground

        let signedIn = UILabel()
        signedIn.text = "Signed in"
        signedIn.accessibilityID("signin.signedIn")

        let status = UILabel()
        status.text = "Sign in: signedIn"
        status.accessibilityID("signin.value")
        status.accessibilityStateValue("signedIn")

        let stack = UIStackView(arrangedSubviews: [signedIn, status])
        stack.axis = .vertical
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])
    }
}
