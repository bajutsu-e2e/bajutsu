import Foundation

/// How much of the alert resolution a `GET /elements` runs (BE-0461 Unit 2).
enum AlertResolutionMode {
    /// SpringBoard check, snapshot, in-tree pass.
    case full
    /// Snapshot and in-tree pass only: a `handleSystemAlert` step's own reads, which must leave the
    /// step's SpringBoard alert to the step (BE-0461 Unit 3).
    case inTree
}

/// What a resolving read returns besides the records it drains.
struct AlertResolutionOutcome {
    var elements: [ElementSnapshot]
    /// This request's own SpringBoard check ran and found no alert, and the in-tree pass's re-check,
    /// when it ran, found none either.
    var springboardChecked = false
    /// The pacing memo kept this request from pressing a showing that already holds a key.
    var withheld = false
}

// `ElementSnapshot` holds an opaque `AnyObject` backing that is only ever touched on the main
// thread, inside the one serialized operation that minted it.
extension AlertResolutionOutcome: @unchecked Sendable {}

/// Resolves the alert the policy names where the orchestrator reads the screen, inside the request
/// rather than on a clock of its own (BE-0461 Unit 2). Nothing records between two requests: every
/// record arises inside one, and the reply drains it.
///
/// Runs on the main thread inside one serialized operation, like every other XCUITest call.
struct AlertResolver {
    let provider: ElementProviding
    let store: InterruptionPolicyStore

    func run(mode: AlertResolutionMode, forceCheck: Bool) -> AlertResolutionOutcome {
        let policy = store.policy
        var outcome = AlertResolutionOutcome(elements: [])
        var checkedThisRequest = false
        if mode == .full, store.claimSpringboardCheck(force: forceCheck) {
            checkedThisRequest = true
            outcome.springboardChecked = resolveSpringboard(policy, &outcome)
        }
        outcome.elements = provider.queryElements()
        resolveInTree(policy, checkedThisRequest: checkedThisRequest, &outcome)
        return outcome
    }

    /// Step 1: answer the SpringBoard alert a native rule names. Returns whether the check found no
    /// alert at all.
    private func resolveSpringboard(
        _ policy: InterruptionPolicy, _ outcome: inout AlertResolutionOutcome
    ) -> Bool {
        let alert = SpringboardAlert(provider.querySystemAlertButtons())
        if alert.buttons.isEmpty {
            store.rearmNative()
            return true
        }
        let labels = alert.buttons.map { $0.label ?? "" }
        // The alert lost the race with its own dismissal between the existence check and the label
        // reads — the same benign race the monitor guards (`RunnerUITest.swift`). Nothing to answer,
        // and no evidence the screen is clear either.
        guard labels.contains(where: { !$0.isEmpty }) else { return false }
        // Every title joins as a marker, as the Python probe adds them: with two alerts up, dropping
        // one title could let a rule its marker excludes (notifications vs. Local Network) match
        // their shared buttons.
        let matched = labels + alert.titles.map(alertTitleMarker)
        guard let rule = policy.rule(for: matched, surface: .native) else {
            store.rearmNative()
            if policy.governs { store.recordUnidentified(labels) }
            return false
        }
        store.rearmNative(keeping: rule)
        if press(rule, on: alert, labels: labels, matched: matched) == .withheld {
            outcome.withheld = true
        }
        return false
    }

    /// `POST /systemAlert/resolve` (BE-0461 Unit 3): apply the policy to the SpringBoard alert a
    /// `handleSystemAlert` step judged not to be its own, but only while that alert still offers
    /// exactly the buttons the step read (*expected*). The step's own alert, arriving in place of the
    /// one it judged, is then never answered by policy.
    ///
    /// Shares step 1's per-showing key and pacing memo, so a showing step 1 or the monitor already
    /// answered is re-pressed only after the re-tap delay and up to the ceiling.
    func resolveSystemAlert(expecting expected: [String]) -> SystemAlertResolution {
        let policy = store.policy
        let alert = SpringboardAlert(provider.querySystemAlertButtons())
        if alert.buttons.isEmpty {
            store.rearmNative()
            return .absent
        }
        let labels = alert.buttons.map { $0.label ?? "" }
        // A multiset comparison over the labelled buttons, the ones the driver's read reports: a
        // label offered twice is a different alert from one offering it once. An all-empty read (the
        // alert racing its own dismissal) never equals what the step saw.
        let named = labels.filter { !$0.isEmpty }
        guard !named.isEmpty, named.sorted() == expected.sorted() else { return .changed }
        let matched = labels + alert.titles.map(alertTitleMarker)
        guard let rule = policy.rule(for: matched, surface: .native) else {
            store.rearmNative()
            if policy.governs { store.recordUnidentified(labels) }
            return .unidentified
        }
        store.rearmNative(keeping: rule)
        // Anything but a landed press reads as `pending`, so the step never mistakes a withheld,
        // given-up, or refused call for an answer.
        return press(rule, on: alert, labels: labels, matched: matched) == .pressed
            ? .answered : .pending
    }

    /// Press *rule*'s button on *alert* if the pacing memo lets it, recording the outcome in the memo.
    private func press(
        _ rule: InterruptionRule, on alert: SpringboardAlert, labels: [String], matched: [String]
    ) -> PressOutcome {
        switch store.decide(rule) {
        case .press:
            // The tap label must be on the alert exactly once, like every identifying label: two
            // would leave the press to whichever matched first.
            guard labels.filter({ $0 == rule.tap }).count == 1,
                  let ordinal = labels.firstIndex(of: rule.tap)
            else {
                store.giveUp(rule)
                return .notPressed
            }
            let result = provider.tapSystemAlertButton(
                backingElement: alert.buttons[ordinal].backingElement
            )
            if result == .ok {
                store.notePress(rule, alert: matched)
                return .pressed
            }
            store.noteFailedPress(rule)
            return .notPressed
        case .withhold:
            return .withheld
        case .stop:
            return .notPressed
        }
    }

    /// Step 3: answer the in-tree prompt the first matching `inTree` rule names, then re-read.
    ///
    /// It taps only after re-checking SpringBoard: an XCUITest tap made while a SpringBoard alert is
    /// up would let the monitor answer that alert first, the order today's Python gate enforces.
    private func resolveInTree(
        _ policy: InterruptionPolicy, checkedThisRequest: Bool,
        _ outcome: inout AlertResolutionOutcome
    ) {
        defer {
            let labels = treeButtons(outcome.elements).map(\.label)
            let armed = policy.rules.filter { $0.surface == .inTree && $0.matches(labels) }
            store.rearmInTree(armed: Set(armed.map(\.tap)))
        }
        let buttons = treeButtons(outcome.elements)
        let labels = buttons.map(\.label)
        guard let rule = policy.rule(for: labels, surface: .inTree) else { return }
        let targets = buttons.filter { $0.label == rule.tap }
        // Two buttons carrying the tap label would leave the press to whichever matched first, so
        // this read presses neither. Unlike a SpringBoard alert's, an in-tree screen with a passing
        // duplicate (a fading sheet over its replacement) usually settles, so it gives up nothing —
        // as the Python gate's own in-tree pass skips just that poll.
        guard targets.count == 1 else { return }
        let signature = outcome.elements.map {
            TreeSignatureEntry(label: $0.label, identifier: $0.identifier)
        }
        switch store.decide(rule, signature: signature) {
        case .withhold:
            outcome.withheld = true
            return
        case .stop:
            return
        case .press:
            break
        }
        // Counts toward the check's rate limit, except in a request whose own check just ran: that
        // one always re-checks, since the snapshot in between may have taken long enough for an
        // alert to arrive.
        guard store.claimSpringboardCheck(force: checkedThisRequest) else { return }
        if !SpringboardAlert(provider.querySystemAlertButtons()).buttons.isEmpty {
            outcome.springboardChecked = false
            return
        }
        let result = provider.tap(backingElement: targets[0].element.backingElement, taps: 1, duration: 0)
        switch result {
        case .ok:
            store.notePress(rule, alert: rule.identify, signature: signature)
            store.actuationCompleted(result)
            outcome.elements = provider.queryElements()
        case .notHittable:
            store.noteNotHittable(rule)
        case .stale, .notFound, .valueNotFound:
            store.noteFailedPress(rule)
        }
    }

    /// The identifier-less, labelled buttons a rule matches against (`tree_buttons` in Python).
    private func treeButtons(_ elements: [ElementSnapshot]) -> [(label: String, element: ElementSnapshot)] {
        elements.compactMap { element in
            guard let label = element.label, !label.isEmpty,
                  element.identifier?.isEmpty ?? true,
                  element.traits.contains("button")  // base.Trait.BUTTON
            else { return nil }
            return (label, element)
        }
    }
}

/// What `POST /systemAlert/resolve` did with the alert showing (BE-0461 Unit 3).
enum SystemAlertResolution: String, Sendable {
    /// A native rule identified the alert and the runner pressed its button.
    case answered
    /// The alert showing no longer offers the label set the step read, so nothing was pressed.
    case changed
    /// No SpringBoard alert is showing.
    case absent
    /// An alert is showing and no rule identifies it; recorded *unidentified* when the policy governs.
    case unidentified
    /// A rule identified the alert but nothing landed: the pacing memo withheld or stopped the
    /// press, or the press itself was refused.
    case pending
}

private enum PressOutcome {
    case pressed
    case withheld
    case notPressed
}

/// `querySystemAlertButtons`'s reply split into the tappable buttons and the titles of the alerts
/// they sit on.
private struct SpringboardAlert {
    let buttons: [ElementSnapshot]
    let titles: [String]

    init(_ entries: [ElementSnapshot]) {
        buttons = entries.filter { $0.identifier != systemAlertTitleIdentifier }
        titles = entries.compactMap { $0.identifier == systemAlertTitleIdentifier ? $0.label : nil }
    }
}
