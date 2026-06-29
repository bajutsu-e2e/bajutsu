import Foundation

/// Manages per-snapshot opaque handles for elements.
///
/// Each `refreshSnapshot` replaces the previous snapshot: all prior handles
/// become stale. Matches the Python driver's expectation that a handle from
/// one `/elements` call is invalidated by the next.
final class SnapshotStore {
    enum LookupResult {
        case found(ElementSnapshot)
        case stale
        case notFound
    }

    private var generation: UInt64 = 0
    private var currentElements: [String: ElementSnapshot] = [:]

    func refreshSnapshot(elements: [ElementSnapshot]) -> [(handle: String, snapshot: ElementSnapshot)] {
        generation &+= 1
        currentElements.removeAll()
        return elements.enumerated().map { index, snapshot in
            let handle = "h-\(generation)-\(index)"
            currentElements[handle] = snapshot
            return (handle, snapshot)
        }
    }

    func lookup(handle: String) -> LookupResult {
        if let snapshot = currentElements[handle] {
            return .found(snapshot)
        }
        if handle.hasPrefix("h-") {
            return .stale
        }
        return .notFound
    }
}
