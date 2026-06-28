import SwiftUI

enum AuthStep: Hashable { case signIn }

struct AuthFlowView: View {
    @EnvironmentObject var model: AppModel

    // Onboarding is the root; signing in is a NavigationStack push onto it.
    private var path: Binding<[AuthStep]> {
        Binding(get: { model.screen == .login ? [.signIn] : [] }, set: { _ in })
    }

    var body: some View {
        NavigationStack(path: path) {
            OnboardingView()
                .navigationDestination(for: AuthStep.self) { _ in
                    AuthView().navigationBarBackButtonHidden()
                }
        }
    }
}

struct OnboardingView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 16) {
            Text("Welcome")
                .font(.largeTitle)
                .accessibilityIdentifier("onboarding.title")
            Button("Get Started") { model.finishOnboarding() }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("onboarding.start")
        }
        .padding()
        .toolbar(.hidden, for: .navigationBar)
    }
}

struct AuthView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 16) {
            Text("Sign in")
                .font(.title)
                .accessibilityIdentifier("auth.title")
            TextField("Email", text: $model.email)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.asciiCapable)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityIdentifier("auth.email")
            // A real SecureField outside UI tests; iOS offers to save the password on submit, and
            // that "Save Password?" alert lives in SpringBoard (invisible to the idb query) for the
            // tool's AI alert guard to dismiss. Under SAMPLE_UITEST the deterministic CI gate has no
            // alert guard (it would need an API key, which the gate must not depend on), and the
            // `.oneTimeCode` content-type hint does NOT reliably suppress the prompt — it still
            // appeared intermittently and occluded the app's tree, stalling the run. So under UI
            // tests use a plain TextField, which never triggers the save-password heuristic, keeping
            // the on-device smoke deterministic; the real SecureField demo is preserved otherwise.
            if model.uiTest {
                TextField("Password", text: $model.password)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("auth.password")
            } else {
                SecureField("Password", text: $model.password)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(.password)
                    .accessibilityIdentifier("auth.password")
            }
            if model.loginError {
                Text("Invalid credentials")
                    .foregroundStyle(.red)
                    .accessibilityIdentifier("auth.error")
            }
            Button("Log in") { model.login() }
                .buttonStyle(.borderedProminent)
                .disabled(model.email.isEmpty || model.password.isEmpty)
                .accessibilityIdentifier("auth.submit")
        }
        .padding()
        .toolbar(.hidden, for: .navigationBar)
    }
}
