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

    // The HTTP server handles connections concurrently (BE-0287), so /elements
    // and /tap can reach the store at the same time; guard the mutable state.
    private let lock = NSLock()
    private var generation: UInt64 = 0
    private var currentElements: [String: ElementSnapshot] = [:]

    func refreshSnapshot(elements: [ElementSnapshot]) -> [(handle: String, snapshot: ElementSnapshot)] {
        lock.withLock {
            generation &+= 1
            currentElements.removeAll()
            return elements.enumerated().map { index, snapshot in
                let handle = "h-\(generation)-\(index)"
                currentElements[handle] = snapshot
                return (handle, snapshot)
            }
        }
    }

    func lookup(handle: String) -> LookupResult {
        lock.withLock {
            if let snapshot = currentElements[handle] {
                return .found(snapshot)
            }
            let parts = handle.split(separator: "-")
            guard parts.count == 3,
                  parts[0] == "h",
                  let handleGen = UInt64(parts[1]),
                  handleGen <= generation else {
                return .notFound
            }
            return .stale
        }
    }
}
