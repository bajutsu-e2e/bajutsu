import Foundation

/// Manages content-addressed opaque handles for elements.
///
/// A handle is derived from the element's stable identity (identifier, label,
/// traits), not from a per-snapshot generation counter, so an element that is
/// still on screen keeps the same handle across snapshots. A redundant
/// `/elements` read — one the Python driver's transport-retry seam re-sends —
/// therefore re-issues the identical handle rather than stranding it, which is
/// the failure class this addresses (BE-XXXX). A handle goes stale only when the
/// element it names leaves the screen or changes identity; a handle string that
/// was never issued is not-found. `value` and `frame` are excluded from the
/// derivation on purpose: a screen settling after a transition re-snapshots
/// repeatedly, and a `frame` that shifts or a `value` that ticks (a loading
/// label, a countdown, a spinner) between two interleaved reads would give one
/// unchanged element two handles and reopen the race — and neither buys actuation
/// precision, since the runner acts through the resolved `backingElement`. This
/// is the same identity `PositionPath.attributesMatch` uses (BE-0287).
final class SnapshotStore {
    enum LookupResult {
        case found(ElementSnapshot)
        case stale
        case notFound
    }

    // The HTTP server handles connections concurrently (BE-0287), so /elements
    // and /tap can reach the store at the same time; guard the mutable state.
    private let lock = NSLock()
    private var currentElements: [String: ElementSnapshot] = [:]
    // Every handle ever issued, so a handle whose element has left the screen
    // reads as `.stale` (once issued, now gone) rather than `.notFound`. Grows
    // for the runner process's lifetime (bounded by distinct elements ever seen);
    // a per-run bound can be added later if a measurement shows it matters.
    private var everIssuedHandles: Set<String> = []

    /// Derive a stable handle from the element's identity.
    ///
    /// `backingElement`, `value`, and `frame` are excluded on purpose (see the type
    /// comment): the reference changes every query, and the value and frame change
    /// while a screen settles.
    private func contentHandle(for snapshot: ElementSnapshot) -> String {
        var hasher = Hasher()
        hasher.combine(snapshot.identifier)
        hasher.combine(snapshot.label)
        hasher.combine(snapshot.traits)
        return "h-\(hasher.finalize())"
    }

    func refreshSnapshot(elements: [ElementSnapshot]) -> [(handle: String, snapshot: ElementSnapshot)] {
        lock.withLock {
            currentElements.removeAll()
            // Two content-identical elements in one snapshot (say, two buttons both
            // labeled "Delete" with no identifier) would collide on the same content
            // handle; break the tie by query-order occurrence so each keeps a distinct
            // handle. The stored `backingElement` is always this query's fresh reference.
            var occurrence: [String: Int] = [:]
            return elements.map { snapshot in
                let base = contentHandle(for: snapshot)
                let n = occurrence[base, default: 0]
                occurrence[base] = n + 1
                let handle = n == 0 ? base : "\(base)-\(n)"
                currentElements[handle] = snapshot
                everIssuedHandles.insert(handle)
                return (handle, snapshot)
            }
        }
    }

    func lookup(handle: String) -> LookupResult {
        lock.withLock {
            if let snapshot = currentElements[handle] {
                return .found(snapshot)
            }
            return everIssuedHandles.contains(handle) ? .stale : .notFound
        }
    }
}
