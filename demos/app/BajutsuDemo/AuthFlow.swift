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
            Text("Welcome to Bajutsu")
                .font(.largeTitle)
                .multilineTextAlignment(.center)
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
            // A real, masked SecureField — the secret stays dots in every screenshot. iOS's
            // "Save Password?" prompt is suppressed by clearing the field on successful
            // login (see AppModel.login), not by weakening the field.
            SecureField("Password", text: $model.password)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.asciiCapable)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
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
