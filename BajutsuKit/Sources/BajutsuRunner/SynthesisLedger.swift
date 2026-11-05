import Foundation
#if canImport(os)
import os
#endif

/// Brackets every touch the runner synthesizes with unified-log markers, so the host can tell a
/// touch the Simulator dropped from one it delivered (BE-0462).
///
/// XCUITest reports a synthesized touch as delivered even when `backboardd` discards it, and the
/// only per-touch evidence of the drop is `backboardd`'s own log lines. A begin/end window around
/// each synthesis, in the same log stream, lets the host attribute those lines to the touch that
/// caused them. The verdict is the host's to draw (BE-0462 Unit 3); the runner only marks.
///
/// Each marker carries `<nonce>/<id>`: the id is unique for the life of this process, and the nonce,
/// drawn once at startup, keeps a respawned runner's ids from colliding with its predecessor's in
/// the same device session's stream.
public final class SynthesisLedger: @unchecked Sendable {
    public static let shared = SynthesisLedger()

    /// The unified-log subsystem and category the markers go to, which the host's `log stream`
    /// predicate selects.
    public static let subsystem = "bajutsu.runner"
    public static let category = "synthesis"

    public let nonce: String

    private let lock = NSLock()
    private let sink: @Sendable (String) -> Void
    private var _counter = 0
    private var _lastActuation: String?
    /// Windows whose touch has begun and not yet returned, innermost last.
    private var _open: [String] = []

    /// *nonce* and *sink* are injectable so the markers can be asserted without a unified log; the
    /// runner draws a random nonce and writes to `os_log`.
    public init(
        nonce: String = String(format: "%08x", UInt32.random(in: .min ... .max)),
        sink: (@Sendable (String) -> Void)? = nil
    ) {
        self.nonce = nonce
        self.sink = sink ?? Self.unifiedLog
    }

    /// Marks that the runner has begun serving. The host waits for this marker to prove its log
    /// stream sees the runner at all, so a dead stream fails the run instead of leaving every touch
    /// unverified.
    public func announceServing() {
        sink("bajutsu.synthesis serving \(nonce)")
    }

    /// Runs *touch*, one touch synthesis, between a begin and an end marker, and returns the id it
    /// was marked with.
    ///
    /// *actuation* is false for the interruption monitor's press or banner swipe, which fires inside
    /// whatever touch it interrupted and is reported against the record it produced, never as the
    /// request's own synthesis. A resolving read's press stays an ordinary actuation: it goes through
    /// the provider like any other, and the read's own nested `collect` keeps it apart from the
    /// request.
    ///
    /// A fence follows the end marker so the stream always holds a line stamped after the window:
    /// the host needs one to know no drop line for the window is still in flight.
    @discardableResult
    public func mark<T>(actuation: Bool = true, _ touch: () throws -> T) rethrows -> (value: T, id: String) {
        let id = nextID()
        lock.lock()
        _open.append(id)
        lock.unlock()
        sink("bajutsu.synthesis begin \(id)")
        let value: T
        do {
            value = try touch()
        } catch {
            // A Swift throw unwinds past the end marker too, and no `caught` boundary sees it.
            lock.lock()
            _open.removeAll { $0 == id }
            lock.unlock()
            sink("bajutsu.synthesis abort \(id)")
            throw error
        }
        sink("bajutsu.synthesis end \(id)")
        sink("bajutsu.synthesis fence \(id)")
        lock.lock()
        _open.removeAll { $0 == id }
        if actuation { _lastActuation = id }
        lock.unlock()
        return (value, id)
    }

    /// Runs *work* and returns the id of the last actuation it synthesized, nil when it synthesized
    /// none (a stale handle, a refused element, an endpoint that types rather than touches).
    ///
    /// The last one, not every one: `gesture`'s opt-in retry synthesizes once per attempt, and only
    /// its final attempt's verdict describes what the endpoint answered (BE-0462). Nests: a resolving
    /// read collecting its own press inside a request that is collecting another leaves the outer
    /// collection as it found it.
    public func collect<T>(_ work: () throws -> T) rethrows -> (value: T, synthesisId: String?) {
        lock.lock()
        let outer = _lastActuation
        _lastActuation = nil
        lock.unlock()
        defer {
            lock.lock()
            _lastActuation = outer
            lock.unlock()
        }
        let value = try work()
        lock.lock()
        let id = _lastActuation
        lock.unlock()
        return (value, id)
    }

    /// Closes every window whose touch never returned, innermost first, with an `abort` marker.
    ///
    /// An XCUITest interaction that raises an `NSException` mid-touch unwinds past `mark` without
    /// running any Swift cleanup, so neither its end marker nor a `defer` would ever run. The
    /// handler that catches the raise calls this instead, so the stream never holds a window left
    /// open for good, which the host could otherwise read as swallowing every later drop line.
    public func abandonOpenWindows() {
        lock.lock()
        let open = _open
        _open = []
        lock.unlock()
        for id in open.reversed() { sink("bajutsu.synthesis abort \(id)") }
    }

    private func nextID() -> String {
        lock.lock()
        defer { lock.unlock() }
        _counter += 1
        return "\(nonce)/\(_counter)"
    }

    /// `.notice`, not `.debug`: a notice is persisted, so the markers survive into a later
    /// `log show` extract (the CI diagnostics) as well as a live `log stream`. Public, because the
    /// unified log redacts an interpolated string by default and the host must read the id.
    private static let unifiedLog: @Sendable (String) -> Void = {
        #if canImport(os)
        let logger = Logger(subsystem: subsystem, category: category)
        return { message in logger.notice("\(message, privacy: .public)") }
        #else
        return { _ in }
        #endif
    }()
}
