import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// In-app screen-transition observation for bajutsu (BE-0310).
///
/// Reports each completed view-controller appearance to the collector, giving bajutsu a positive
/// "a screen transition finished" signal — recorded alongside network exchanges, for the readiness
/// gate and the `settled` wait to consult in place of tree-diff polling.
///
/// **Mechanism (and why this one).** The item's proposal aimed to *observe*
/// `UIAccessibility.screenChangedNotification`, but that symbol is not an observable
/// `NSNotification.Name`: iOS vends `.screenChanged` only as an *outbound* enum case for
/// `UIAccessibility.post(notification:argument:)` (the app→VoiceOver direction), with no public
/// notification for other code to subscribe to. The proposal's *Alternatives considered* had
/// weighed swizzling `UIViewController.viewDidAppear` and set it aside for feared SwiftUI coverage
/// gaps; with the notification route unavailable, this is the remaining public-API option, and its
/// SwiftUI reach is in fact good: `NavigationStack` pushes, `.sheet` / `.fullScreenCover`
/// presentations, and tab switches are each backed by a `UIHostingController` whose `viewDidAppear`
/// fires when the transition settles — the same standard container transitions the notification
/// would have covered. `viewDidAppear` is also called *after* the appearance transition completes,
/// matching the timing readiness / `settled` want.
///
/// The boundary is an in-place SwiftUI view swap *within one hosting controller* (an `if`/state
/// change that presents no new controller), which posts no appearance and is not a container
/// transition anyway — the same out-of-scope case the proposal already documents (a within-screen
/// update, [BE-0299]).
///
/// **Test/debug only**, like `BajutsuNet`: the swizzle is installed only when `BAJUTSU_COLLECTOR`
/// is present, and it hooks a framework lifecycle method — no app screen code is ever touched, so
/// the signal stays app-agnostic.
public enum BajutsuScreen {
    private static var installed = false
    private static var seq = 0

    /// One JSON line per transition is POSTed to the collector's `/transitions` endpoint. A
    /// separate ephemeral session, like `BajutsuNet.reportSession`, so the report POST is
    /// never itself observed.
    static let reportSession = URLSession(configuration: .ephemeral)

    /// Install the appearance hook if `BAJUTSU_COLLECTOR` is set. Called from
    /// `BajutsuNet.startIfEnabled()`, after it has parsed the collector URL/token — reads those
    /// directly rather than re-parsing the launch environment itself.
    static func startIfEnabled() {
        #if canImport(UIKit)
        guard !installed else { return }  // idempotent — a relaunch in-process calls this once
        guard BajutsuNet.collectorURL != nil else { return }
        installed = true
        UIViewController.bajutsu_installAppearanceHook()
        #endif
    }

    #if canImport(UIKit)
    static func report() {
        guard let collectorURL = BajutsuNet.collectorURL else { return }
        seq += 1
        // Surface the transition to the host app's UI (same data POSTed below). `kind` stays
        // "screenChanged" — the semantic event, and the wire contract the Python collector reads —
        // even though the mechanism is a `viewDidAppear` hook rather than the accessibility
        // notification the proposal first named.
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

#if canImport(UIKit)
extension UIViewController {
    /// Swizzle `viewDidAppear(_:)` so every completed controller appearance reports a transition,
    /// mirroring the `method_exchangeImplementations` idiom `BajutsuURLProtocol` already uses. Called
    /// once (guarded by `BajutsuScreen.installed`); a second call would swap the implementations back.
    static func bajutsu_installAppearanceHook() {
        guard
            let original = class_getInstanceMethod(self, #selector(UIViewController.viewDidAppear(_:))),
            let replacement = class_getInstanceMethod(self, #selector(UIViewController.bajutsu_viewDidAppear(_:)))
        else { return }
        method_exchangeImplementations(original, replacement)
    }

    @objc fileprivate func bajutsu_viewDidAppear(_ animated: Bool) {
        // After the swizzle this calls the original `viewDidAppear`, then reports the appearance.
        self.bajutsu_viewDidAppear(animated)
        BajutsuScreen.report()
    }
}
#endif
