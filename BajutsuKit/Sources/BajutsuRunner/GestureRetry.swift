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
/// return, or key on a change threshold rather than mere inequality.
///
/// It is a bounded retry, not a wait: `maxAttempts` caps the tries so a gesture that genuinely has
/// no observable effect returns after a fixed, small number of attempts rather than looping. The
/// first attempt whose post-actuation `signature` differs from the pre-actuation one is taken as the
/// gesture landing, so the stop condition is only as precise as `signature`: a change the gesture did
/// *not* cause (a clock, a spinner, an async-loaded row) is mistaken for a landing and stops the
/// retry early, degrading back to the drop this closes. `signature` should therefore project only
/// state a landed gesture is expected to move; when the caller cannot identify that state and must
/// project the whole screen, the screen under test must carry no gesture-independent live-updating
/// content. Excluding `frame` is always right — layout jitter is not a landing.
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
