import Foundation

/// One rule of the interruption policy: the prompt's identifying labels, and the label to tap on it.
///
/// The Python mirror is `ResolvedAlertRule` (`bajutsu/common/orchestrator/types.py`). Matching
/// requires the prompt's *full* label set, since a single shared label (e.g. "Allow") cannot tell
/// two covered prompts apart.
public struct InterruptionRule: Sendable, Equatable {
    public let identify: [String]
    public let tap: String
    /// Labels whose presence rules this rule out, as `excluded_labels` does on the Python side. The
    /// one real case: `notifications` excludes the Local Network prompt's title marker, since the
    /// two prompts share their buttons.
    public let exclude: [String]
    /// Where this rule's prompt shows. Only a `native` rule reaches the interruption monitor and the
    /// SpringBoard check; only an `inTree` one reaches a resolving read's in-tree pass (BE-0461).
    public let surface: AlertSurface

    public init(
        identify: [String], tap: String, exclude: [String] = [], surface: AlertSurface = .native
    ) {
        self.identify = identify
        self.tap = tap
        self.exclude = exclude
        self.surface = surface
    }

    /// Whether every identifying label is among *labels* exactly once and no excluded one is.
    ///
    /// "Exactly once", not merely present, so a screen with two identically labelled buttons never
    /// resolves to whichever matched first (determinism first).
    func matches(_ labels: [String]) -> Bool {
        identify.allSatisfy { label in labels.filter { $0 == label }.count == 1 }
            && !exclude.contains(where: labels.contains)
    }
}

/// The two places a declared prompt can show (`ResolvedAlertRule.native` / `.in_tree` on the
/// Python side).
public enum AlertSurface: String, Sendable, Equatable {
    case native
    case inTree
}

/// How often a resolving read checks SpringBoard, and how one showing is re-pressed and given up
/// on. Every value is pushed from the Python side (`AlertGuardConfig`), which owns the policy; these
/// defaults only cover a push that predates them.
public struct AlertPacing: Sendable, Equatable {
    public var pollInterval: TimeInterval
    public var retapDelay: TimeInterval
    public var maxTaps: Int
    public var giveUp: TimeInterval

    public init(
        pollInterval: TimeInterval = 1.0, retapDelay: TimeInterval = 1.0, maxTaps: Int = 3,
        giveUp: TimeInterval = 2.0
    ) {
        self.pollInterval = pollInterval
        self.retapDelay = retapDelay
        self.maxTaps = maxTaps
        self.giveUp = giveUp
    }
}

/// The prefix of the entry an alert's title adds to the labels a rule matches against.
///
/// Mirrors `bajutsu.common.scenario.system_alerts.TITLE_MARKER`; the two must stay byte-identical,
/// since the Python side resolves the rules and this side matches them.
public let alertTitleMarkerPrefix = "title: "

/// The label-like entry an alert's title contributes: the title reduced to Apple's own template.
///
/// A title names the app, so every “…”-quoted span becomes “%@”, which is how the shipped strings
/// spell the placeholder. Mirrors `alert_title_marker` on the Python side.
public func alertTitleMarker(_ title: String) -> String {
    alertTitleMarkerPrefix
        + title.replacingOccurrences(
            of: "“[^”]*”", with: "“%@”", options: .regularExpression)
}

/// Which button to press on an out-of-process alert that interrupts an XCUITest interaction.
///
/// This is a *mechanism*, not a policy: every label in it is resolved on the Python side from the
/// scenario's own `systemAlertHandling.rules`. Only the rules this surface can actually meet are
/// sent — an alert raised inside the application's own process never interrupts an XCUITest
/// interaction, so its rules are dropped rather than matched here (BE-0406). What lives in Swift is
/// only the matching discipline: a rule wins when every one of its identifying labels is on the
/// alert exactly once. "Exactly once", not merely present, so an alert with two identically labelled
/// buttons never resolves to whichever matched first (determinism first).
///
/// `governs` is true for any scenario whose guard is on, independent of whether any rule survived
/// the drop above (BE-0406 Unit 2b): a real declaration filtered down to nothing this surface can
/// act on is not the same as no declaration at all, and only the latter — an absent guard — should
/// still leave a declined alert unreported.
///
/// It exists because XCUITest resolves an interrupting alert *before* it synthesizes any element
/// interaction, and with no monitor registered it falls back to its own default handler, which taps
/// the alert's **default** button — granting a permission the scenario may have refused, invisibly.
/// A monitor that declines to act cannot replace that handler: XCUITest verifies the alert is gone
/// and, finding it still up, re-invokes the monitor on the very next interaction, which in a
/// resident runner is an unbounded loop. So the monitor has to actually answer, and this is what
/// tells it how — and when it cannot, `governs` is what tells the caller whether that decline is
/// worth reporting.
public struct InterruptionPolicy: Sendable, Equatable {
    public let rules: [InterruptionRule]
    public let governs: Bool
    public let pacing: AlertPacing

    public init(rules: [InterruptionRule] = [], governs: Bool = false, pacing: AlertPacing = .init()) {
        self.rules = rules
        self.governs = governs
        self.pacing = pacing
    }

    /// The first rule on *surface* that *labels* identify, in the order the rules arrived.
    ///
    /// The order is the Python side's (`tree_dedup_rules` puts a nested shape's wider sibling
    /// first), so nothing here reorders.
    public func rule(for labels: [String], surface: AlertSurface) -> InterruptionRule? {
        rules.first { $0.surface == surface && $0.matches(labels) }
    }

    /// The label to tap on an alert offering `buttons`, or nil when no rule identifies it.
    ///
    /// Nil is the signal to decline the interruption rather than guess. Declining hands the alert
    /// back to XCUITest's default handler — no worse than before for a prompt no scenario
    /// described, and never a loop, because that handler does clear the alert. Since BE-0406 Unit 2b
    /// there is no built-in fallback here: an alert no rule identifies is always declined, and
    /// `governs` decides only whether that decline gets recorded.
    ///
    /// `buttons` may carry the alert's title marker (`alertTitleMarker`) beside its buttons, which a
    /// rule identifies or excludes like any other label.
    ///
    /// Only `native` rules answer here: an in-tree prompt never interrupts an XCUITest interaction,
    /// so matching its rule against a SpringBoard alert could only mistake one prompt for another.
    public func label(for buttons: [String]) -> String? {
        rule(for: buttons, surface: .native)?.tap
    }
}

/// The identifier `querySystemAlertButtons` gives the untappable entry carrying an alert's title,
/// which every reader keeps out of the button list (the Python driver's `_ALERT_TITLE_ID`).
public let systemAlertTitleIdentifier = "bajutsu.systemAlert.title"

/// The identifier SpringBoard gives a foreground notification banner (BE-0416 Unit 1).
///
/// Measured on iOS 26.5: the banner is a SpringBoard element carrying this identifier, with the
/// notification's own text as its label and a single descendant button — `ShortLook.Platter.Content
/// .Seamless` — whose label is that same text. That button is the *open the notification's app*
/// affordance, so nothing here may tap it; a banner is cleared by an upward swipe instead.
public let notificationBannerIdentifier = "NotificationShortLookView"

/// Whether an interrupting element is a foreground notification banner rather than an alert.
///
/// Kept beside the policy, and matched on the identifier alone, because the two facts that would
/// otherwise distinguish a banner are both unusable: its element type is an undocumented raw value,
/// and its only button's label is the notification's own text, which varies per notification and per
/// locale.
///
/// The distinction matters twice over. A banner reaching `InterruptionPolicy.label(for:)` can never
/// match a rule — the scenario declares prompts by button label, and a banner has no such button —
/// so before this existed a governed run recorded it as an *undeclared interruption* and failed an
/// otherwise-passing step, naming the notification's body text as a button it had expected to find
/// (BE-0416 Unit 1, measured). And a banner is the one interruption XCUITest's own default handler
/// cannot press a button on. When Unit 1 measured it, the handler waited the banner out instead,
/// which is why an interrupted interaction with an auto-dismissing banner cost ~9s against ~0.6s
/// undisturbed. A persistent-style banner never auto-dismisses; Unit 1 measured XCUITest clearing
/// that one by some other means, in ~3.5s — so the wait-it-out account, and the ~9s it explains,
/// held for the auto-dismissing case.
///
/// That hand-off is no longer benign on iOS 26.5 (#2156): the handler ignores the banner as a
/// non-alert element, and each lookup it makes of the banner by type misses and records a UI
/// automation failure, which can preempt the interrupted interaction and end the test case. So the
/// monitor's banner branch declines to XCUITest only as a last resort; see
/// `RunnerUITest.swipeAwayNotificationBanner`.
public func isNotificationBanner(identifier: String) -> Bool {
    identifier == notificationBannerIdentifier
}

/// SpringBoard's own bundle identifier, shared by every site that opens an `XCUIApplication` handle
/// onto it — the system-alert query, the banner query, and the banner's own dismiss swipe.
public let springboardBundleID = "com.apple.springboard"

/// A showing the runner stopped pressing (BE-0461): the rule's identifying labels, its surface, and
/// the button it chose but could not clear the alert with.
public struct GaveUpRecord: Sendable, Equatable {
    public let identify: [String]
    public let surface: AlertSurface
    public let tap: String
}

/// Everything recorded since the last drain, oldest first within each list.
public struct InterruptionRecords: Sendable, Equatable {
    /// The label of each answer, monitor and resolving read alike.
    public var tapped: [String] = []
    /// Per entry of `tapped`, what the rule matched that alert by.
    public var tappedAlerts: [[String]] = []
    /// Per entry of `tapped`, the surface of the rule that answered it.
    public var tappedSurfaces: [AlertSurface] = []
    public var declined: [[String]] = []
    public var banners: [String] = []
    /// Per entry of `tapped`, the synthesis id of every press behind it (BE-0462): the first press,
    /// and any re-press of the same showing made before this drain. Empty for an answer recorded
    /// with no marked touch.
    public var tappedSynthesisIds: [[String]] = []
    /// Per entry of `banners`, the synthesis id of the swipe the runner synthesized for it, if it
    /// synthesized one. Empty when it did not — the banner was already gone, or never settled. Not
    /// a claim that the swipe cleared it: a banner can outlast a swipe and go on its own.
    public var bannerSynthesisIds: [[String]] = []
    /// The buttons of a SpringBoard alert a resolving read found and no rule identified.
    public var unidentified: [[String]] = []
    public var gaveUp: [GaveUpRecord] = []
}

/// What the pacing memo lets a resolving read do with a showing it found.
enum PressDecision: Equatable {
    case press
    /// Pressed inside the re-tap delay already; leave it for a later request.
    case withhold
    /// Past the tap ceiling, or declined as a changed screen: press it no more.
    case stop
}

/// One showing, as the runner identifies it across requests: a native rule by its identifying
/// labels, an in-tree prompt by the label it taps.
///
/// An in-tree showing is keyed on the tapped label rather than the rule because `savePassword`'s
/// nested shapes all tap "Not Now", and a fading sheet read with a subset of its buttons matches the
/// narrower sibling — a per-rule key would tap that sibling again inside the re-tap delay (BE-0461).
struct ShowingKey: Hashable {
    let surface: AlertSurface
    let name: [String]

    static func of(_ rule: InterruptionRule) -> ShowingKey {
        switch rule.surface {
        case .native: return ShowingKey(surface: .native, name: rule.identify.sorted())
        case .inTree: return ShowingKey(surface: .inTree, name: [rule.tap])
        }
    }
}

/// A screen's identity for the changed-screen decline: labels and identifiers, not frames, so an
/// animation settling a few pixels does not read as a new screen.
struct TreeSignatureEntry: Equatable {
    let label: String?
    let identifier: String?
}

private struct ShowingMemo {
    let rule: InterruptionRule
    var answered = false
    var taps = 0
    var lastPress: TimeInterval?
    var gaveUp = false
    var declined = false
    var signature: [TreeSignatureEntry]?
    var notHittableSince: TimeInterval?
    /// Where this showing's *answered* entry sits while it is still undrained, so a re-press lands
    /// its synthesis id on the same entry (BE-0462). Stamped with the drain generation it belongs
    /// to: once that drain has gone, a re-press has no entry left to extend.
    var entry: (generation: Int, index: Int)?
}

/// The live interruption policy, plus what the monitor and the resolving reads have answered,
/// declined and swiped away since the last drain, and the per-showing pacing memo they share.
///
/// A shared store rather than a value threaded through `ElementProviding`, because the two sides
/// that need it never meet: the policy arrives on a server thread (`POST /interruptionPolicy`),
/// while the monitor that reads it runs on XCTest's own thread whenever an interaction is
/// interrupted. Every access is under one lock for that reason.
///
/// The drained tapped labels are what lets the Python side report an interruption-time dismissal as
/// an `AlertEvent`, so a prompt answered here is not silently missing from the run's report. The
/// drained declined button lists do the same for an alert nothing answered on the scenario's behalf
/// (BE-0406 Unit 2b), and the drained banners do it for a foreground notification banner swiped away
/// (BE-0416): the failure mode this whole mechanism exists to end covers all three.
///
/// The memo is what keeps one showing to at most one *answered* and at most one *gave up* record on
/// every path that presses it (BE-0461 Unit 2).
public final class InterruptionPolicyStore: @unchecked Sendable {
    public static let shared = InterruptionPolicyStore()

    private let lock = NSLock()
    private let now: @Sendable () -> TimeInterval
    private var _policy = InterruptionPolicy()
    private var _records = InterruptionRecords()
    private var _memo: [ShowingKey: ShowingMemo] = [:]
    private var _lastSpringboardCheck: TimeInterval?
    /// Bumped whenever `_records` is replaced, so a memo's `entry` from an earlier drain is never
    /// mistaken for an index into the current one.
    private var _generation = 0

    /// *now* is injectable so the pacing can be tested without sleeping; the runner uses uptime,
    /// which a wall-clock change cannot move.
    public init(now: @escaping @Sendable () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.now = now
    }

    public var policy: InterruptionPolicy {
        lock.lock()
        defer { lock.unlock() }
        return _policy
    }

    /// Replaces the policy. Also clears the pending drain: what's queued belongs to whichever
    /// scenario set the policy it happened under, so carrying it into the next one would misreport
    /// it.
    ///
    /// Only the push at scenario start (*scenarioStart*) clears the memo and the check clock, so
    /// nothing a previous scenario answered or declined suppresses the same prompt after a
    /// relaunch. A push inside a scenario keeps them, so a showing spanning it is neither re-pressed
    /// early nor recorded twice.
    public func setPolicy(_ policy: InterruptionPolicy, scenarioStart: Bool = false) {
        lock.lock()
        defer { lock.unlock() }
        _policy = policy
        _records = InterruptionRecords()
        _generation += 1
        if scenarioStart {
            _memo = [:]
            _lastSpringboardCheck = nil
        }
    }

    public func record(
        _ label: String, alert: [String] = [], surface: AlertSurface = .native, synthesisIds: [String] = []
    ) {
        lock.lock()
        defer { lock.unlock() }
        appendAnswered(label, alert: alert, surface: surface, synthesisIds: synthesisIds)
    }

    /// Records the buttons of an alert `governs` covered but no rule identified, before declining.
    public func recordDeclined(_ buttons: [String]) {
        lock.lock()
        defer { lock.unlock() }
        _records.declined.append(buttons)
    }

    /// Records a notification banner the monitor cleared, by the notification's own text.
    ///
    /// Cleared, not necessarily swiped away: the monitor also claims a banner that went by its own
    /// auto-dismissal, before or after a swipe that missed. The runner's stderr log says which path
    /// cleared it.
    ///
    /// Separate from `record` because the two are not the same event: `record` names the *button*
    /// the policy chose, while a banner has none and is identified by its content. The Python side
    /// keeps them apart in the report for the same reason (BE-0416).
    ///
    /// *synthesisIds* names the swipe the monitor synthesized for it (BE-0462), empty when it made
    /// none.
    public func recordBanner(_ label: String, synthesisIds: [String] = []) {
        lock.lock()
        defer { lock.unlock() }
        _records.banners.append(label)
        _records.bannerSynthesisIds.append(synthesisIds)
    }

    /// Records a SpringBoard alert a resolving read found and no rule identified — on every check,
    /// since it is what keeps the blocked-screen note alive on the Python side.
    func recordUnidentified(_ buttons: [String]) {
        lock.lock()
        defer { lock.unlock() }
        _records.unidentified.append(buttons)
    }

    /// The monitor's entry point: it has just pressed *rule*'s button, and this decides whether that
    /// press is recorded.
    ///
    /// The monitor always presses — a declined interruption hands the alert to XCUITest's default
    /// button — so the memo cannot withhold it, inside the re-tap delay and past the ceiling alike.
    /// It can only keep a showing that already holds a key from being recorded *answered* twice.
    /// Returns whether the press was recorded.
    @discardableResult
    public func recordMonitorPress(
        _ rule: InterruptionRule, alert: [String], synthesisId: String? = nil
    ) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let key = ShowingKey.of(rule)
        let first = !(_memo[key]?.answered ?? false)
        notePressLocked(key, rule: rule, alert: alert, signature: nil, synthesisId: synthesisId)
        return first
    }

    /// Whether a resolving read may query SpringBoard now, claiming the slot when it may.
    ///
    /// *force* skips the rate limit — the recovery read after a refused actuation or a failed check
    /// (BE-0461 Unit 5), and the in-tree re-check in a request whose own check just ran.
    func claimSpringboardCheck(force: Bool) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let at = now()
        if !force, let last = _lastSpringboardCheck, at - last < _policy.pacing.pollInterval {
            return false
        }
        _lastSpringboardCheck = at
        return true
    }

    /// What the memo lets a resolving read do with *rule*'s showing. Records *gave up* the first time
    /// the ceiling stops it.
    ///
    /// *signature* is the in-tree screen the read sees. Past the re-tap delay, a label that still
    /// matches on a changed screen is an application button the sheet was covering, so the showing
    /// is declined for good and records no *gave up*.
    func decide(_ rule: InterruptionRule, signature: [TreeSignatureEntry]? = nil) -> PressDecision {
        lock.lock()
        defer { lock.unlock() }
        let key = ShowingKey.of(rule)
        guard var memo = _memo[key] else { return .press }
        if memo.declined || memo.gaveUp { return .stop }
        if let last = memo.lastPress, now() - last < _policy.pacing.retapDelay { return .withhold }
        if memo.lastPress != nil, let tapped = memo.signature, let signature, tapped != signature {
            memo.declined = true
            _memo[key] = memo
            return .stop
        }
        if memo.taps >= _policy.pacing.maxTaps {
            giveUpLocked(key, &memo)
            _memo[key] = memo
            return .stop
        }
        return .press
    }

    /// A resolving read pressed *rule*'s button: records *answered* on the showing's first press.
    func notePress(
        _ rule: InterruptionRule, alert: [String], signature: [TreeSignatureEntry]? = nil,
        synthesisId: String? = nil
    ) {
        lock.lock()
        defer { lock.unlock() }
        notePressLocked(
            ShowingKey.of(rule), rule: rule, alert: alert, signature: signature, synthesisId: synthesisId
        )
    }

    /// A resolving read tried to press *rule*'s button and the press did not happen (the button was
    /// gone or refused). It counts toward the tap ceiling, so a press that keeps failing ends in
    /// *gave up* rather than being retried for the rest of the wait; it records no *answered*.
    func noteFailedPress(_ rule: InterruptionRule) {
        lock.lock()
        defer { lock.unlock() }
        let key = ShowingKey.of(rule)
        var memo = _memo[key] ?? ShowingMemo(rule: rule)
        memo.taps += 1
        memo.lastPress = now()
        _memo[key] = memo
    }

    /// *rule*'s showing cannot be pressed at all (its button is not on screen exactly once), so it
    /// records *gave up* now instead of guessing which match to press.
    func giveUp(_ rule: InterruptionRule) {
        lock.lock()
        defer { lock.unlock() }
        let key = ShowingKey.of(rule)
        var memo = _memo[key] ?? ShowingMemo(rule: rule)
        giveUpLocked(key, &memo)
        _memo[key] = memo
    }

    /// An in-tree button was present but not hittable (a scrim mid-animation). Past the give-up
    /// bound from the first such refusal, the showing records *gave up*.
    func noteNotHittable(_ rule: InterruptionRule) {
        lock.lock()
        defer { lock.unlock() }
        let key = ShowingKey.of(rule)
        var memo = _memo[key] ?? ShowingMemo(rule: rule)
        let at = now()
        if let since = memo.notHittableSince {
            if at - since >= _policy.pacing.giveUp { giveUpLocked(key, &memo) }
        } else {
            memo.notHittableSince = at
        }
        _memo[key] = memo
    }

    /// Re-arms every native key but *kept*: a check that no longer finds a showing ends it.
    func rearmNative(keeping kept: InterruptionRule? = nil) {
        lock.lock()
        defer { lock.unlock() }
        let keep = kept.map(ShowingKey.of)
        _memo = _memo.filter { $0.key.surface != .native || $0.key == keep }
    }

    /// Re-arms every in-tree key no rule matching the tree still taps (`_reset_tree_showing` in the
    /// Python gate): the showing is gone even when an application button carrying the same label
    /// stays, so the sheet returning over it is a new showing.
    func rearmInTree(armed labels: Set<String>) {
        lock.lock()
        defer { lock.unlock() }
        _memo = _memo.filter { $0.key.surface != .inTree || $0.key.name.allSatisfy(labels.contains) }
    }

    /// An actuation on the application has completed. XCUITest synthesizes one only once no alert
    /// blocks it, so every native showing pressed before it is gone, and a prompt that recurs before
    /// the next check (the paste prompt on every paste) is a new showing.
    ///
    /// A refused actuation synthesized nothing, so it re-arms nothing; neither does a press on a
    /// SpringBoard alert's own button, which never comes here, since XCUITest synthesizes that while
    /// the alert is still up. An in-tree key is never re-armed this way: an in-tree alert does not
    /// block synthesis.
    func actuationCompleted(_ result: TapResult) {
        switch result {
        case .ok, .valueNotFound:
            rearmNative()
        case .stale, .notFound, .notHittable:
            break
        }
    }

    /// Returns everything recorded since the last drain, and clears it.
    public func drain() -> InterruptionRecords {
        lock.lock()
        defer { lock.unlock() }
        let drained = _records
        _records = InterruptionRecords()
        _generation += 1
        return drained
    }

    /// Appends an *answered* entry and returns its index in the current records.
    @discardableResult
    private func appendAnswered(
        _ label: String, alert: [String], surface: AlertSurface, synthesisIds: [String]
    ) -> Int {
        _records.tapped.append(label)
        _records.tappedAlerts.append(alert)
        _records.tappedSurfaces.append(surface)
        _records.tappedSynthesisIds.append(synthesisIds)
        return _records.tapped.count - 1
    }

    private func notePressLocked(
        _ key: ShowingKey, rule: InterruptionRule, alert: [String], signature: [TreeSignatureEntry]?,
        synthesisId: String?
    ) {
        var memo = _memo[key] ?? ShowingMemo(rule: rule)
        let ids = synthesisId.map { [$0] } ?? []
        if !memo.answered {
            memo.answered = true
            let index = appendAnswered(rule.tap, alert: alert, surface: rule.surface, synthesisIds: ids)
            memo.entry = (_generation, index)
        } else if let entry = memo.entry, entry.generation == _generation {
            _records.tappedSynthesisIds[entry.index].append(contentsOf: ids)
        }
        memo.taps += 1
        memo.lastPress = now()
        memo.notHittableSince = nil
        if let signature { memo.signature = signature }
        _memo[key] = memo
    }

    private func giveUpLocked(_ key: ShowingKey, _ memo: inout ShowingMemo) {
        guard !memo.gaveUp else { return }
        memo.gaveUp = true
        _records.gaveUp.append(
            GaveUpRecord(identify: memo.rule.identify, surface: memo.rule.surface, tap: memo.rule.tap)
        )
    }
}
