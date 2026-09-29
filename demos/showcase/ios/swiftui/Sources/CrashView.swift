import SwiftUI

// BE-0424: the on-device realization of an app crash. A unit test can stub
// `~/Library/Logs/DiagnosticReports`, but only a real fault proves `ReportCrash` actually writes the
// `.ips` this item's capture depends on, and that `XCUIApplication.state` reports `notRunning`
// afterward. Reached only when the SHOWCASE_CRASH launch env is set (see AppModel), so the normal
// observe-only app (BE-0079) never renders it.
//
// Gated behind a launch env rather than a build configuration, mirroring SHOWCASE_CONFORMANCE: a
// Release build would compile a `#if DEBUG` affordance out, and the expected-to-fail scenario would
// then fail on a missing selector instead of on a crash — the exact misdiagnosis this item exists to
// remove. A launch env needs no assumption about which configuration the lane's build job compiled.
//
// Flat, like GestureView: the trigger is always in the accessibility tree, with no scroll to resolve
// it through.
struct CrashView: View {
    var body: some View {
        VStack(spacing: 24) {
            Text("Crash fixture")
                .font(.headline)
                .accessibilityID("crash.title")

            // The scenario taps this, then takes one more step against the now-dead app. It has to:
            // a crash caused by a scenario's *last* step is never probed, because the reactive check
            // only asks a driver once a step has already failed, and the tap itself is delivered
            // before the app dies (docs/evidence.md, "App-crash evidence").
            Button("Crash now") {
                fatalError("SHOWCASE_CRASH: deliberate crash for the BE-0424 app-crash fixture")
            }
            .accessibilityID("crash.trigger")

            // Tapped by the step *after* the trigger. It never actually runs — the app is gone by
            // then — which is the point: that step fails, and its failure is what gets probed.
            Text("Still alive")
                .foregroundStyle(.secondary)
                .accessibilityID("crash.alive")
        }
        .padding()
    }
}
