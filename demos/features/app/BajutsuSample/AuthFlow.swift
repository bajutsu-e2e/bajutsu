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
            // A real SecureField: outside UI tests iOS offers to save the password on
            // submit, and that system "Save Password?" alert lives in SpringBoard (invisible
            // to the idb query) for the tool's AI alert guard to dismiss. Under SAMPLE_UITEST
            // the deterministic CI gate has no alert guard (it would need an API key, which the
            // gate must not depend on), so the prompt would intermittently block the run. Mark
            // the field as a one-time code there: iOS then does not offer to save it, keeping
            // the on-device smoke deterministic without weakening the real-SecureField demo.
            SecureField("Password", text: $model.password)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.asciiCapable)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .textContentType(model.uiTest ? .oneTimeCode : .password)
                .accessibilityIdentifier("auth.password")
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
