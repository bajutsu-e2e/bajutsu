import SwiftUI

enum AuthStep: Hashable { case signIn }

struct RootView: View {
    @EnvironmentObject var model: AppModel

    // The auth flow (onboarding -> login) is a modal over the always-present Home, so
    // Home's NavigationStack and toolbar are built once at launch and stay live. Tapping
    // the toolbar right after logging in no longer races a rebuilt-from-scratch view
    // (the old RootView switch replaced the whole subtree on every screen change).
    private var authPresented: Binding<Bool> {
        Binding(get: { model.screen != .home }, set: { _ in })
    }

    var body: some View {
        HomeView()
            .fullScreenCover(isPresented: authPresented) { AuthFlowView() }
    }
}

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
            // A real SecureField: iOS offers to save the password on submit. That
            // system "Save Password?" alert lives in SpringBoard (invisible to the
            // idb query) and the tool's alert guard is expected to dismiss it.
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

struct HomeView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                Text("Home")
                    .font(.title)
                    .accessibilityIdentifier("home.title")

                HStack {
                    Text("Count: \(model.counter)")
                        .accessibilityIdentifier("counter.value")
                        .accessibilityValue("\(model.counter)")
                    Button("+") { model.increment() }
                        .accessibilityIdentifier("counter.increment")
                }

                if model.isLoading {
                    ProgressView()
                        .accessibilityIdentifier("home.spinner")
                } else if model.loaded {
                    Text("Loaded")
                        .accessibilityIdentifier("home.loaded")
                } else {
                    Button("Load") { model.load() }
                        .accessibilityIdentifier("home.load")
                }

                TextField("Search", text: $model.query)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("home.search")

                List(model.filteredItems) { item in
                    Text(item.name)
                        .accessibilityIdentifier("list.row.\(item.id)")
                }
                .accessibilityIdentifier("home.list")
            }
            .padding()
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Settings") { model.showSettings = true }
                        .accessibilityIdentifier("nav.settings")
                }
            }
            .sheet(isPresented: $model.showSettings) { SettingsView() }
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("Settings")
                    .font(.title)
                    .accessibilityIdentifier("settings.title")

                Button {
                    model.toggleNormalize()
                } label: {
                    HStack {
                        Image(systemName: model.normalize ? "checkmark.square" : "square")
                        Text("Normalize")
                    }
                }
                .accessibilityIdentifier("settings.normalizeToggle")
                .accessibilityAddTraits(model.normalize ? .isSelected : [])
                // Mirror the selected state into the value so headless backends that
                // do not surface the isSelected trait (e.g. idb) can still read it.
                .accessibilityValue(model.normalize ? "on" : "off")

                if model.settingsChanged {
                    Text("Settings changed — reindex needed")
                        .font(.callout)
                        .accessibilityIdentifier("settings.banner")
                }

                Button("Reindex") { model.reindex() }
                    .accessibilityIdentifier("settings.reindex")

                Text("Status: \(model.reindexStatus)")
                    .accessibilityIdentifier("settings.status")
                    .accessibilityValue(model.reindexStatus)

                if model.reindexStatus == "done" {
                    Text("Reindex complete")
                        .accessibilityIdentifier("settings.reindexComplete")
                }

                Button("Close") { dismiss() }
                    .accessibilityIdentifier("settings.close")
                Spacer()
            }
            .padding()
        }
    }
}
