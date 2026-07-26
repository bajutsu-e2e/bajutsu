import Foundation

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
