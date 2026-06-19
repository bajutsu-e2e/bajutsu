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
                .accessibilityID("onboarding.title")
            Button("Continue") { model.finishOnboarding() }
                .buttonStyle(.borderedProminent)
                .accessibilityID("onboarding.continue")
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
                .accessibilityID("auth.title")
            TextField("Email", text: $model.email)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.asciiCapable)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityID("auth.email")
            // A plain SecureField. SPEC §7: it deliberately does NOT set
            // textContentType = .password/.newPassword — that omission is what keeps
            // iOS from offering the "Save Password?" sheet, so no OS alert at login.
            SecureField("Password", text: $model.password)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.asciiCapable)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityID("auth.password")
            if model.loginError {
                Text("Enter an email and password")
                    .foregroundStyle(.red)
                    .accessibilityID("auth.error")
            }
            Button("Log in") { model.login() }
                .buttonStyle(.borderedProminent)
                .accessibilityID("auth.submit")
        }
        .padding()
        .toolbar(.hidden, for: .navigationBar)
    }
}
