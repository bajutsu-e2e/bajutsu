import SwiftUI
import UIKit
import UserNotifications

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
        MainTabView()
            .fullScreenCover(isPresented: authPresented) { AuthFlowView() }
    }
}

struct MainTabView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        TabView(selection: $model.selectedTab) {
            HomeView()
                .tabItem { Label("Home", systemImage: "house") }
                .tag(0)
            ComponentsView()
                .tabItem { Label("Components", systemImage: "square.grid.2x2") }
                .tag(1)
            ControlsView()
                .tabItem { Label("Controls", systemImage: "slider.horizontal.3") }
                .tag(2)
            TextInputView()
                .tabItem { Label("Text", systemImage: "textformat") }
                .tag(3)
            ListsNavView()
                .tabItem { Label("Lists", systemImage: "list.bullet") }
                .tag(4)
            GesturesView()
                .tabItem { Label("Gestures", systemImage: "hand.draw") }
                .tag(5)
            PresentationView()
                .tabItem { Label("Present", systemImage: "rectangle.portrait.on.rectangle.portrait") }
                .tag(6)
            AsyncView()
                .tabItem { Label("Async", systemImage: "clock.arrow.circlepath") }
                .tag(7)
            SystemView()
                .tabItem { Label("System", systemImage: "gearshape.2") }
                .tag(8)
        }
    }
}

// A grab-bag of interaction patterns: long press, an in-app confirmation alert,
// and a swipe-direction gesture.
struct ComponentsView: View {
    @State private var revealed = false
    @State private var showDeleteAlert = false
    @State private var deleted = false
    @State private var swipeDir = "none"

    var body: some View {
        VStack(spacing: 24) {
            Text("Components")
                .font(.title)
                .accessibilityIdentifier("comp.title")

            // Long press: a plain view (not a Button, whose tap gesture would consume it).
            Text("Hold me")
                .padding()
                .frame(maxWidth: .infinity)
                .background(.blue.opacity(0.15))
                .accessibilityIdentifier("comp.longpress")
                .onLongPressGesture(minimumDuration: 0.4) { revealed = true }
            if revealed {
                Text("Revealed").accessibilityIdentifier("comp.secret")
            }

            // In-app confirmation alert (a SwiftUI .alert is visible to idb).
            Button("Remove") { showDeleteAlert = true }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("comp.remove")
            if deleted {
                Text("Deleted").accessibilityIdentifier("comp.deleted")
            }

            // Swipe: a DragGesture records the direction (onEnded needs no flick velocity).
            Text("Swipe me")
                .padding()
                .frame(maxWidth: .infinity, minHeight: 80)
                .background(.green.opacity(0.15))
                .accessibilityIdentifier("comp.swipearea")
                .gesture(DragGesture(minimumDistance: 20).onEnded { v in
                    let dx = v.translation.width, dy = v.translation.height
                    if abs(dx) > abs(dy) {
                        swipeDir = dx < 0 ? "left" : "right"
                    } else {
                        swipeDir = dy < 0 ? "up" : "down"
                    }
                })
            Text("Swiped: \(swipeDir)")
                .accessibilityIdentifier("comp.swipeResult")
                .accessibilityValue(swipeDir)
        }
        .padding()
        .alert("Delete item?", isPresented: $showDeleteAlert) {
            Button("Delete", role: .destructive) { deleted = true }
            Button("Cancel", role: .cancel) {}
        }
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

// MARK: - P1 UI gallery

// A gallery of the standard value controls. Each control mirrors its current
// state into a sibling result label's accessibilityValue, so headless backends
// (e.g. idb) that don't surface a control's own value can still assert the
// outcome — the same trick settings.normalizeToggle uses.
struct ControlsView: View {
    @State private var toggleOn = false
    @State private var stepperValue = 0
    @State private var sliderValue = 0.0
    @State private var segment = 0
    @State private var menuChoice = "None"
    @State private var tapCount = 0

    private let segments = ["One", "Two", "Three"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Controls")
                    .font(.title)
                    .accessibilityIdentifier("ctrl.title")

                // The label is hidden so the Toggle's accessibility element is just
                // the switch — a coordinate backend (idb) taps its center and flips it,
                // rather than landing on a full-width row whose center is the label.
                VStack(alignment: .leading) {
                    HStack {
                        Text("Toggle")
                        Spacer()
                        Toggle("Toggle", isOn: $toggleOn)
                            .labelsHidden()
                            .accessibilityIdentifier("ctrl.toggle")
                    }
                    Text("Toggle: \(toggleOn ? "on" : "off")")
                        .accessibilityIdentifier("ctrl.toggle.value")
                        .accessibilityValue(toggleOn ? "on" : "off")
                }

                VStack(alignment: .leading) {
                    Stepper("Stepper", value: $stepperValue, in: 0 ... 10)
                        .accessibilityIdentifier("ctrl.stepper")
                    Text("Stepper: \(stepperValue)")
                        .accessibilityIdentifier("ctrl.stepper.value")
                        .accessibilityValue("\(stepperValue)")
                }

                // Stepped so the mirrored value is deterministic regardless of the
                // exact drag distance a backend produces.
                VStack(alignment: .leading) {
                    Slider(value: $sliderValue, in: 0 ... 10, step: 1)
                        .accessibilityIdentifier("ctrl.slider")
                    Text("Slider: \(Int(sliderValue))")
                        .accessibilityIdentifier("ctrl.slider.value")
                        .accessibilityValue("\(Int(sliderValue))")
                }

                // A single-select segment built from id'd buttons. A native
                // Picker(.segmented) renders as one TabGroup whose individual segments
                // idb does not surface as elements — so it can't be driven by id or
                // label headlessly. Per-segment buttons keep "pick one of N" drivable
                // by both backends (semantic on rocketsim, coordinate on idb).
                VStack(alignment: .leading) {
                    Text("Segment")
                    HStack(spacing: 8) {
                        ForEach(segments.indices, id: \.self) { i in
                            Button(segments[i]) { segment = i }
                                .buttonStyle(.bordered)
                                .tint(segment == i ? .accentColor : .gray)
                                .accessibilityIdentifier("ctrl.segment.\(segments[i].lowercased())")
                        }
                    }
                    Text("Segment: \(segments[segment])")
                        .accessibilityIdentifier("ctrl.segment.value")
                        .accessibilityValue(segments[segment])
                }

                // A Menu renders its items in a system popover; they are addressed by
                // label (like an alert button), not by identifier.
                VStack(alignment: .leading) {
                    Menu("Menu") {
                        Button("Apple") { menuChoice = "Apple" }
                        Button("Banana") { menuChoice = "Banana" }
                    }
                    .accessibilityIdentifier("ctrl.menu")
                    Text("Menu: \(menuChoice)")
                        .accessibilityIdentifier("ctrl.menu.value")
                        .accessibilityValue(menuChoice)
                }

                // One enabled button (counts taps) and one permanently disabled, to
                // exercise the enabled / disabled assertions.
                VStack(alignment: .leading) {
                    Button("Tap") { tapCount += 1 }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("ctrl.button")
                    Button("Disabled") {}
                        .buttonStyle(.bordered)
                        .disabled(true)
                        .accessibilityIdentifier("ctrl.buttonDisabled")
                    Text("Taps: \(tapCount)")
                        .accessibilityIdentifier("ctrl.button.value")
                        .accessibilityValue("\(tapCount)")
                }
            }
            .padding()
        }
    }
}

// Text-entry variants plus inline validation. The entered text and a live
// character count are mirrored to result labels so a backend can assert exactly
// what was typed without reading the field's own (sometimes redacted) value.
struct TextInputView: View {
    @State private var basic = ""
    @State private var email = ""
    @State private var multiline = ""
    @State private var required = ""
    @State private var submitted = ""

    private var requiredValid: Bool { required.count >= 3 }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Text")
                    .font(.title)
                    .accessibilityIdentifier("text.title")

                TextField("Basic", text: $basic)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("text.basic")
                Text("Value: \(basic)")
                    .accessibilityIdentifier("text.basic.value")
                    .accessibilityValue(basic)
                Text("Count: \(basic.count)")
                    .accessibilityIdentifier("text.count")
                    .accessibilityValue("\(basic.count)")
                Button("Clear") { basic = "" }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("text.clear")

                TextField("Email", text: $email)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.emailAddress)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("text.email")

                TextEditor(text: $multiline)
                    .frame(height: 80)
                    .border(.gray.opacity(0.3))
                    .accessibilityIdentifier("text.editor")
                Text("Editor: \(multiline)")
                    .accessibilityIdentifier("text.editor.value")
                    .accessibilityValue(multiline)

                // Needs >= 3 chars: submit stays disabled until valid, and an error
                // label shows once the field has content but is still too short.
                TextField("Required (min 3)", text: $required)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("text.required")
                if !required.isEmpty && !requiredValid {
                    Text("Too short")
                        .foregroundStyle(.red)
                        .accessibilityIdentifier("text.error")
                }
                Button("Submit") { submitted = required }
                    .buttonStyle(.borderedProminent)
                    .disabled(!requiredValid)
                    .accessibilityIdentifier("text.submit")
                if !submitted.isEmpty {
                    Text("Submitted: \(submitted)")
                        .accessibilityIdentifier("text.submitted")
                        .accessibilityValue(submitted)
                }
            }
            .padding()
        }
    }
}

// A List exercising search filtering, swipe-to-delete, edit-mode reorder,
// pull-to-refresh, push navigation, and an empty state. Row ids are data-derived
// (`lists.row.<id>`) so a backend can address any row and count them by glob.
struct ListsNavView: View {
    @State private var items: [Item] = ListsNavView.seed
    @State private var query = ""
    @State private var refreshed = false

    static let seed = (1 ... 5).map { Item(id: $0, name: "Row \($0)") }

    private var filtered: [Item] {
        query.isEmpty ? items : items.filter { $0.name.localizedCaseInsensitiveContains(query) }
    }

    var body: some View {
        NavigationStack {
            VStack {
                TextField("Search", text: $query)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.asciiCapable)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("lists.search")
                    .padding(.horizontal)

                if filtered.isEmpty {
                    Text("No items")
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("lists.empty")
                }

                List {
                    ForEach(filtered) { item in
                        NavigationLink(value: item.id) {
                            Text(item.name)
                                .accessibilityIdentifier("lists.row.\(item.id)")
                        }
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) {
                                items.removeAll { $0.id == item.id }
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                    }
                    .onDelete { offsets in
                        // EditButton path: map the filtered offsets back to `items`.
                        let ids = offsets.map { filtered[$0].id }
                        items.removeAll { ids.contains($0.id) }
                    }
                    .onMove { source, dest in
                        items.move(fromOffsets: source, toOffset: dest)
                    }
                }
                .accessibilityIdentifier("lists.list")
                .refreshable {
                    // Pull-to-refresh restores the seed set and reveals a banner.
                    items = ListsNavView.seed
                    refreshed = true
                }

                Text("Count: \(filtered.count)")
                    .accessibilityIdentifier("lists.count")
                    .accessibilityValue("\(filtered.count)")
                if refreshed {
                    Text("Refreshed")
                        .accessibilityIdentifier("lists.refreshed")
                }
            }
            .navigationTitle("Lists")
            .navigationDestination(for: Int.self) { id in
                ListDetailView(name: items.first { $0.id == id }?.name ?? "Row \(id)")
            }
            .toolbar {
                EditButton()
                    .accessibilityIdentifier("lists.edit")
            }
        }
    }
}

struct ListDetailView: View {
    let name: String

    var body: some View {
        VStack(spacing: 12) {
            Text("Detail")
                .font(.title)
                .accessibilityIdentifier("lists.detail.title")
            Text(name)
                .accessibilityIdentifier("lists.detail.value")
                .accessibilityValue(name)
        }
        .padding()
    }
}

// Multi-touch + double-tap gestures. Each records a coarse, deterministic result
// (a count or a direction) into a `*.value` label. double-tap is drivable by idb
// (two taps); pinch / rotate need real multi-touch, so their on-device path is the
// generated XCUITest (pinch(withScale:) / rotate(_:)).
struct GesturesView: View {
    @State private var doubled = 0
    @State private var pinchDir = "none"
    @State private var rotateDir = "none"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Gestures")
                    .font(.title)
                    .accessibilityIdentifier("gest.title")

                VStack(alignment: .leading) {
                    Text("Double-tap me")
                        .padding()
                        .frame(maxWidth: .infinity, minHeight: 60)
                        .background(.blue.opacity(0.15))
                        .accessibilityIdentifier("gest.doubletap")
                        .onTapGesture(count: 2) { doubled += 1 }
                    Text("Double taps: \(doubled)")
                        .accessibilityIdentifier("gest.doubletap.value")
                        .accessibilityValue("\(doubled)")
                }

                VStack(alignment: .leading) {
                    Text("Pinch me")
                        .padding()
                        .frame(maxWidth: .infinity, minHeight: 80)
                        .background(.green.opacity(0.15))
                        .accessibilityIdentifier("gest.pinch")
                        .gesture(MagnifyGesture().onEnded { value in
                            pinchDir = value.magnification > 1 ? "in" : "out"
                        })
                    Text("Pinch: \(pinchDir)")
                        .accessibilityIdentifier("gest.pinch.value")
                        .accessibilityValue(pinchDir)
                }

                VStack(alignment: .leading) {
                    Text("Rotate me")
                        .padding()
                        .frame(maxWidth: .infinity, minHeight: 80)
                        .background(.orange.opacity(0.15))
                        .accessibilityIdentifier("gest.rotate")
                        .gesture(RotateGesture().onEnded { value in
                            rotateDir = value.rotation.radians >= 0 ? "cw" : "ccw"
                        })
                    Text("Rotate: \(rotateDir)")
                        .accessibilityIdentifier("gest.rotate.value")
                        .accessibilityValue(rotateDir)
                }
            }
            .padding()
        }
    }
}

// Modal presentation styles: a detented sheet, a full-screen cover, an action
// sheet (confirmationDialog), and an auto-dismissing toast. In-app modals are
// visible to idb; the toast exercises `wait until gone`.
struct PresentationView: View {
    @State private var showSheet = false
    @State private var showCover = false
    @State private var showDialog = false
    @State private var dialogResult = "none"
    @State private var showToast = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Presentation")
                    .font(.title)
                    .accessibilityIdentifier("pres.title")

                Button("Open Sheet") { showSheet = true }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("pres.openSheet")

                Button("Open Full-Screen") { showCover = true }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("pres.openCover")

                Button("Open Dialog") { showDialog = true }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("pres.openDialog")
                Text("Dialog: \(dialogResult)")
                    .accessibilityIdentifier("pres.dialog.value")
                    .accessibilityValue(dialogResult)

                Button("Show Toast") { showToastBriefly() }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("pres.showToast")
            }
            .padding()
        }
        .overlay(alignment: .top) {
            if showToast {
                Text("Saved")
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.thinMaterial, in: Capsule())
                    .accessibilityIdentifier("pres.toast")
            }
        }
        .sheet(isPresented: $showSheet) {
            VStack(spacing: 16) {
                Text("Sheet")
                    .font(.title)
                    .accessibilityIdentifier("pres.sheet.title")
                Button("Close") { showSheet = false }
                    .accessibilityIdentifier("pres.sheet.close")
            }
            .padding()
            .presentationDetents([.medium, .large])
        }
        .fullScreenCover(isPresented: $showCover) {
            VStack(spacing: 16) {
                Text("Full Screen")
                    .font(.title)
                    .accessibilityIdentifier("pres.cover.title")
                Button("Close") { showCover = false }
                    .accessibilityIdentifier("pres.cover.close")
            }
            .padding()
        }
        .confirmationDialog("Choose", isPresented: $showDialog, titleVisibility: .visible) {
            Button("Archive") { dialogResult = "archive" }
            Button("Delete", role: .destructive) { dialogResult = "delete" }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func showToastBriefly() {
        showToast = true
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(1200))
            showToast = false
        }
    }
}

// Asynchronous state: a determinate progress bar, a fail -> retry -> success
// flow, a debounced search, and button-driven pagination. Delays are real
// Task.sleeps (not animation), so they survive SAMPLE_UITEST's animation disable
// and exercise condition waits (`wait for`).
struct AsyncView: View {
    @State private var progress = 0.0
    @State private var loadState = "idle"  // idle | loading | error | loaded
    @State private var search = ""
    @State private var debounced = ""
    @State private var debounceTask: Task<Void, Never>?
    @State private var rows = 3

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Async")
                    .font(.title)
                    .accessibilityIdentifier("async.title")

                VStack(alignment: .leading) {
                    Button("Start") { startProgress() }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("async.startProgress")
                    ProgressView(value: progress)
                        .accessibilityIdentifier("async.progress")
                    Text("Progress: \(Int(progress * 100))")
                        .accessibilityIdentifier("async.progress.value")
                        .accessibilityValue("\(Int(progress * 100))")
                    if progress >= 1 {
                        Text("Complete").accessibilityIdentifier("async.progress.done")
                    }
                }

                VStack(alignment: .leading) {
                    Button("Load (fails)") { load(succeed: false) }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("async.loadFail")
                    if loadState == "error" {
                        Text("Failed")
                            .foregroundStyle(.red)
                            .accessibilityIdentifier("async.error")
                        Button("Retry") { load(succeed: true) }
                            .accessibilityIdentifier("async.retry")
                    }
                    if loadState == "loaded" {
                        Text("Loaded").accessibilityIdentifier("async.loaded")
                    }
                }

                VStack(alignment: .leading) {
                    TextField("Search", text: $search)
                        .textFieldStyle(.roundedBorder)
                        .keyboardType(.asciiCapable)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .accessibilityIdentifier("async.search")
                        .onChange(of: search) { _, newValue in scheduleDebounce(newValue) }
                    // Appears only once the debounce fires, so a `wait for` reliably
                    // bridges the delay rather than racing the still-empty value.
                    if !debounced.isEmpty {
                        Text("Debounced: \(debounced)")
                            .accessibilityIdentifier("async.debounced.value")
                            .accessibilityValue(debounced)
                    }
                }

                VStack(alignment: .leading) {
                    Button("Load more") { rows += 3 }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("async.loadMore")
                    Text("Rows: \(rows)")
                        .accessibilityIdentifier("async.count")
                        .accessibilityValue("\(rows)")
                }
            }
            .padding()
        }
    }

    private func startProgress() {
        progress = 0
        Task { @MainActor in
            // Step by an exact fraction so the 10th tick lands on 1.0 — repeatedly
            // adding 0.1 accumulates float error and stops just shy of 1.0.
            for i in 1 ... 10 {
                try? await Task.sleep(for: .milliseconds(80))
                progress = Double(i) / 10.0
            }
        }
    }

    private func load(succeed: Bool) {
        loadState = "loading"
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(500))
            loadState = succeed ? "loaded" : "error"
        }
    }

    private func scheduleDebounce(_ value: String) {
        debounceTask?.cancel()
        debounceTask = Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(500))
            if !Task.isCancelled { debounced = value }
        }
    }
}

// System integration — the out-of-process cases that idb's app-scoped query
// cannot see. The notification prompt lives in SpringBoard and is cleared by the
// run's vision alert guard (--dismiss-alerts); pasteboard is in-app and drivable;
// ShareLink opens the system share sheet (documented, guard/AI territory).
struct SystemView: View {
    @State private var notifStatus = "notDetermined"
    @State private var pasted = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("System")
                    .font(.title)
                    .accessibilityIdentifier("sys.title")

                VStack(alignment: .leading) {
                    Button("Request Notifications") { requestNotifications() }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("sys.requestNotif")
                    Text("Notifications: \(notifStatus)")
                        .accessibilityIdentifier("sys.notif.value")
                        .accessibilityValue(notifStatus)
                    if notifStatus == "authorized" {
                        Text("Granted").accessibilityIdentifier("sys.notif.authorized")
                    }
                }

                VStack(alignment: .leading) {
                    Button("Copy") { UIPasteboard.general.string = "bajutsu-clip" }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("sys.copy")
                    Button("Paste") { pasted = UIPasteboard.general.string ?? "" }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("sys.paste")
                    Text("Pasted: \(pasted)")
                        .accessibilityIdentifier("sys.paste.value")
                        .accessibilityValue(pasted)
                }

                ShareLink(item: "Shared from Bajutsu")
                    .accessibilityIdentifier("sys.share")
            }
            .padding()
        }
    }

    private func requestNotifications() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge]) { granted, _ in
            Task { @MainActor in
                notifStatus = granted ? "authorized" : "denied"
            }
        }
    }
}
