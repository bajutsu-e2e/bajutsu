import Foundation
import ObjCExceptionCatcher

/// Re-issue a synthesized actuation until the accessibility tree reflects a change.
///
/// A two-finger gesture (pinch / rotate) that XCUITest synthesizes is occasionally dropped by the
/// Simulator before the app's gesture recognizer fires, so a single actuation can silently no-op —
/// leaving the target's mirrored a11y value at its pre-gesture state and the run's `expect` red on
/// a value that never moved (the `gestures (xcuitest)` lane's recurring "expected 'pinched' but
/// actual 'idle'"). Re-issuing the gesture until `signature` changes closes that drop.
///
/// Invariant the caller must uphold: a *landed* `actuate()` is observable in `signature()` by the
/// time it is re-read. XCUITest delivers a gesture synchronously — the call blocks until its events
/// are pumped — so this holds whenever the app mirrors the effect into the tree without trailing a
/// runloop. It matters because pinch and rotate are *accumulating*: each call zooms/rotates by the
/// amount again, so if a landed gesture's mirror read stale, the loop would issue a second one and
/// apply it twice. That double-apply is harmless when the mirror is a flag (`idle → pinched`,
/// today's only use), but a caller asserting a *magnitude* must guarantee the mirror is current on
/// return, or key on a change threshold rather than mere inequality. Corollary: only drive an
/// accumulating actuation through this when a landing is *observable* in `signature` at all — an
/// effect that never surfaces there is re-issued to the cap, applying the accumulation a
/// drop-dependent (non-deterministic) number of times. The caller gates on that (the XCUITest
/// actuator retries only for a target that mirrors its gesture result onto its own value).
///
/// It is a bounded retry, not a wait: `maxAttempts` caps the tries so a gesture that genuinely has
/// no observable effect returns after a fixed, small number of attempts rather than looping. A pair
/// of *readable, differing* samples — a non-nil `before` and a non-nil post-actuation sample that
/// differs — is taken as the gesture landing, so the stop condition is only as precise as
/// `signature`: it must project state a landed gesture is *responsible for* and gesture-independent
/// churn (a clock, a spinner) is not, else an unrelated change is mistaken for a landing and stops
/// the retry early — degrading back to the drop this closes. The caller keys it on the actuated
/// element's own mirrored value for that reason; a magnitude-sensitive caller should additionally
/// guard against a mirror that trails a runloop (see the accumulating-gesture note above).
///
/// A `nil` sample means *couldn't observe* (an element read or `app.snapshot()` that transiently
/// failed), not "changed": it never satisfies the landing condition, so a swallowed read failure
/// keeps the retry going rather than reading as a landing and stopping early. Folding such a failure
/// into a sentinel string would compare unequal to a real prior value and falsely stop the loop.
///
/// - Parameters:
///   - maxAttempts: The most times to issue `actuate` (clamped to at least 1).
///   - signature: A projection of the observable state, sampled once before and after each attempt;
///     `nil` when the state could not be read (never counted as a change).
///   - actuate: The gesture to issue; called at least once, again after any attempt that did not
///     observe a change.
public func actuateUntilStateChanges(
    maxAttempts: Int,
    signature: () -> String?,
    actuate: () -> Void
) {
    let before = signature()
    for _ in 0..<max(1, maxAttempts) {
        actuate()
        if let before, let after = signature(), after != before { return }
    }
}

/// Read a projection of observable state until it holds at `wanted` on two consecutive samples.
///
/// The read-back half of a set-a-value actuation, where the platform reports nothing: XCUITest's
/// `adjust(toPickerWheelValue:)` returns `Void`, never throws, and records a value it could not
/// reach as a soft `XCTIssue` the resident runner's `continueAfterFailure` swallows (BE-0356). The
/// only way to know whether the value landed is to look.
///
/// One read is not enough, in both directions. A wheel still settling its deceleration reports the
/// rows it passes, so a single early read would call a value the wheel does have absent — and, for
/// the same reason, a single read that happens to catch the wheel *passing through* the wanted row
/// on its way to resting one row past it would call a value that never landed present. The second
/// error is the dangerous one: it reports success for a wheel left on the wrong row, which is
/// exactly the silent, approximate outcome prime directive 2 exists to rule out. Requiring the
/// value to survive a second consecutive read costs one extra query in the common case (the wheel
/// is already at rest) and rejects a value merely passed through.
///
/// Sampling bounds the wait without a fixed sleep, which prime directive 2 also rules out: each
/// sample is a real query whose own round trip is the only pacing, so the bound is a number of
/// observations rather than a guessed duration. A wheel that genuinely has no such row costs
/// exactly `maxSamples` reads and then fails loudly, rather than looping or passing on a
/// best-effort landing.
///
/// A sample that cannot be read means *couldn't observe*, never *matched* — the same rule
/// `actuateUntilStateChanges` follows, so a transiently failed read keeps sampling instead of
/// deciding early. It also breaks a run of matches, since a value that could not be read has not
/// been shown to be holding. Both a `nil` return and a raised `NSException` count as unreadable:
/// reading an `XCUIElement` property raises while the UI is in flux, and a wheel mid-deceleration is
/// UI in flux by definition, so the raise is caught here rather than left to `Router`'s handler-wide
/// shield. Left to the shield it would resolve the whole request to `.stale`, and the driver would
/// fail the step with "element vanished (stale handle)" for a wheel that never went anywhere —
/// while this function's own unreadable-sample path, the one its tests cover, could never run.
///
/// Unlike `actuateUntilStateChanges` this actuates nothing and reports its outcome: the caller needs
/// to distinguish "landed" from "hit the cap" in order to answer the driver at all.
///
/// - Parameters:
///   - wanted: The value the state must hold at.
///   - maxSamples: The most reads to take (clamped to at least 2, the minimum a run of two needs).
///   - sample: The projection to read; `nil` when it could not be read.
/// - Returns: Whether two consecutive samples equalled `wanted`.
public func settlesTo(
    _ wanted: String,
    maxSamples: Int,
    sample: () -> String?
) -> Bool {
    var consecutiveMatches = 0
    for _ in 0..<max(2, maxSamples) {
        consecutiveMatches = caughtSample(sample) == wanted ? consecutiveMatches + 1 : 0
        if consecutiveMatches == 2 { return true }
    }
    return false
}

/// Read `sample`, reporting a raised `NSException` as `nil` — an unreadable sample, not a mismatch.
///
/// Swift's `do`/`catch` never intercepts an Objective-C `NSException`, so without this an
/// `XCUIElement` property read that raises unwinds past `settlesTo` entirely (see its doc comment
/// for what that costs).
private func caughtSample(_ sample: () -> String?) -> String? {
    var value: String?
    do {
        try ObjCExceptionCatcher.catchException { value = sample() }
    } catch {
        return nil
    }
    return value
}
