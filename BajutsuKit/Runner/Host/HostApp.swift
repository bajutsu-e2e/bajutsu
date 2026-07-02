import SwiftUI

/// A minimal target application so the UI-testing bundle produces a valid `.xctestrun`.
/// XCUITest requires a target app in the test configuration even though the runner drives
/// a different app at runtime via `XCUIApplication(bundleIdentifier:)` (BE-0019). This host
/// is never the app under test; it only satisfies the build-time configuration.
@main
struct BajutsuRunnerHostApp: App {
    var body: some Scene {
        WindowGroup {
            Color.clear
        }
    }
}
