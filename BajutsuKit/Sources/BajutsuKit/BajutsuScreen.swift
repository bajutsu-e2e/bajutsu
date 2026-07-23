import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// In-app screen-transition observation for bajutsu (BE-0310).
///
/// UIKit posts `UIAccessibility.screenChangedNotification` after a standard container
/// transition completes (a navigation push/pop, a modal presentation/dismissal, a tab
/// switch) so VoiceOver can reset focus onto the new screen — SwiftUI's `NavigationStack`
/// posts it too, since it is `UINavigationController`-backed underneath. Observing it gives
/// bajutsu a positive "the screen transition finished" signal, reported to the collector
/// alongside network exchanges, for the readiness gate and the `settled` wait to consult in
/// place of tree-diff polling.
///
/// **Test/debug only**, like `BajutsuNet`: activation is a no-op unless `BAJUTSU_COLLECTOR`
/// is present, and it observes only the notification UIKit posts automatically — never one an
/// app must post by hand (e.g. `UIAccessibility.pageScrolledNotification`, whose page-description
/// argument the app supplies) — so no app screen code is ever touched.
public enum BajutsuScreen {
    private static var observerToken: NSObjectProtocol?
    private static var seq = 0

    /// One JSON line per transition is POSTed to the collector's `/transitions` endpoint. A
    /// separate ephemeral session, like `BajutsuNet.reportSession`, so the report POST is
    /// never itself observed.
    static let reportSession = URLSession(configuration: .ephemeral)

    /// Activate the observer if `BAJUTSU_COLLECTOR` is set. Called from `BajutsuNet.startIfEnabled()`,
    /// after it has already parsed the collector URL/token — reads those directly rather than
    /// re-parsing the launch environment itself.
    static func startIfEnabled() {
        #if canImport(UIKit)
        guard observerToken == nil else { return }  // idempotent — a relaunch in-process calls this once
        guard BajutsuNet.collectorURL != nil else { return }
        observerToken = NotificationCenter.default.addObserver(
            forName: UIAccessibility.screenChangedNotification,
            object: nil,
            queue: .main
        ) { _ in
            report()
        }
        #endif
    }

    #if canImport(UIKit)
    private static func report() {
        guard let collectorURL = BajutsuNet.collectorURL else { return }
        seq += 1
        // Surface the transition to the host app's UI (same data POSTed below).
        BajutsuScreenTransitionStore.shared.record(
            BajutsuScreenTransition(kind: "screenChanged", seq: seq)
        )
        let payload: [String: Any] = [
            "kind": "screenChanged",
            "timestamp": ProcessInfo.processInfo.systemUptime,
        ]
        BajutsuNet.postJSON(
            payload,
            to: collectorURL.appendingPathComponent("transitions"),
            token: BajutsuNet.collectorToken,
            session: reportSession
        )
    }
    #endif
}
