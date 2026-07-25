import Foundation

/// Re-issue a synthesized actuation until the accessibility tree reflects a change.
///
/// A two-finger gesture (pinch / rotate) that XCUITest synthesizes is occasionally dropped by the
/// Simulator before the app's gesture recognizer fires, so a single actuation can silently no-op —
/// leaving the target's mirrored a11y value at its pre-gesture state and the run's `expect` red on
/// a value that never moved (the `gestures (xcuitest)` lane's recurring "expected 'pinched' but
/// actual 'idle'"). Because the gesture is idempotent, re-issuing it until `signature` changes
/// closes that drop without weakening any assertion.
///
/// It is a bounded retry, not a wait: `maxAttempts` caps the tries so a gesture that genuinely has
/// no observable effect returns after a fixed, small number of attempts rather than looping. The
/// first attempt whose post-actuation `signature` differs from the pre-actuation one means the
/// gesture landed. `signature` must be a cheap projection of the tree that excludes anything a
/// gesture doesn't touch (layout jitter, clocks) so an unrelated change isn't mistaken for a landing.
///
/// - Parameters:
///   - maxAttempts: The most times to issue `actuate` (clamped to at least 1).
///   - signature: A projection of the observable state, sampled once before and after each attempt.
///   - actuate: The gesture to issue; called at least once, again after any attempt that left
///     `signature` unchanged.
public func actuateUntilStateChanges(
    maxAttempts: Int,
    signature: () -> String,
    actuate: () -> Void
) {
    let before = signature()
    for _ in 0..<max(1, maxAttempts) {
        actuate()
        if signature() != before { return }
    }
}
