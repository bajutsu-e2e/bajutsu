import XCTest
@testable import BajutsuRunner

final class SnapshotStoreTests: XCTestCase {
    func testFreshHandlesResolveToFound() {
        let store = SnapshotStore()
        let backing = NSObject()
        let snapshot = ElementSnapshot(
            identifier: "ok", label: "OK", value: nil,
            traits: ["button"], frame: (0, 0, 10, 10), backingElement: backing
        )
        let entries = store.refreshSnapshot(elements: [snapshot])
        XCTAssertEqual(entries.count, 1)

        let result = store.lookup(handle: entries[0].handle)
        guard case .found(let resolved) = result else {
            XCTFail("expected .found, got \(result)")
            return
        }
        XCTAssertTrue(resolved.backingElement === backing)
    }

    func testOldHandlesBecomeStaleAfterRefresh() {
        let store = SnapshotStore()
        let entries = store.refreshSnapshot(elements: [
            ElementSnapshot(
                identifier: "a", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ])
        let oldHandle = entries[0].handle

        _ = store.refreshSnapshot(elements: [
            ElementSnapshot(
                identifier: "b", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ])

        let result = store.lookup(handle: oldHandle)
        guard case .stale = result else {
            XCTFail("expected .stale, got \(result)")
            return
        }
    }

    func testGarbageHandleReturnsNotFound() {
        let store = SnapshotStore()
        let result = store.lookup(handle: "garbage-input")
        guard case .notFound = result else {
            XCTFail("expected .notFound, got \(result)")
            return
        }
    }

    func testHandleFormatMatchesConvention() {
        let store = SnapshotStore()
        let entries = store.refreshSnapshot(elements: [
            ElementSnapshot(
                identifier: "x", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ])
        XCTAssertTrue(entries[0].handle.hasPrefix("h-"))
    }

    // BE-0312: the regression this store change exists to prevent. A redundant
    // `/elements` read (one the driver's transport-retry seam re-sends) re-runs
    // refreshSnapshot for a screen whose identity is unchanged; under the old
    // generation scheme that stranded the earlier handle as stale. The handle
    // derives from identity (identifier/label/traits), so it survives even a
    // ticking `value` between the two reads (a spinner/countdown while the screen
    // settles) and resolves to the latest query's backing reference.
    func testUnchangedRefreshKeepsFirstHandleFound() {
        let store = SnapshotStore()
        let backing1 = NSObject()
        let backing2 = NSObject()
        func snapshot(value: String?, _ backing: NSObject) -> ElementSnapshot {
            ElementSnapshot(
                identifier: "ok", label: "OK", value: value,
                traits: ["button"], frame: (0, 0, 10, 10), backingElement: backing
            )
        }
        let first = store.refreshSnapshot(elements: [snapshot(value: "50%", backing1)])
        // A redundant read while the screen settles: same identity, a ticked value,
        // a fresh backing reference — must not strand the first handle.
        let second = store.refreshSnapshot(elements: [snapshot(value: "60%", backing2)])
        XCTAssertEqual(first[0].handle, second[0].handle, "unchanged identity must map to the same handle despite a changed value")

        let result = store.lookup(handle: first[0].handle)
        guard case .found(let resolved) = result else {
            XCTFail("expected .found for the first refresh's handle after a redundant refresh, got \(result)")
            return
        }
        XCTAssertTrue(resolved.backingElement === backing2, "a live handle must resolve to the latest query's backing reference")
    }

    // BE-0312: two identity-identical elements in one snapshot must still get
    // distinct handles, so the occurrence-index tiebreak keeps them separable.
    func testContentIdenticalElementsGetDistinctHandles() {
        let store = SnapshotStore()
        let backingA = NSObject()
        let backingB = NSObject()
        func deleteButton(_ backing: NSObject) -> ElementSnapshot {
            ElementSnapshot(
                identifier: nil, label: "Delete", value: nil,
                traits: ["button"], frame: (0, 0, 1, 1), backingElement: backing
            )
        }
        let entries = store.refreshSnapshot(elements: [deleteButton(backingA), deleteButton(backingB)])
        XCTAssertEqual(entries.count, 2)
        XCTAssertNotEqual(entries[0].handle, entries[1].handle, "identity-identical elements in one snapshot must still get distinct handles")

        guard case .found(let first) = store.lookup(handle: entries[0].handle),
              case .found(let second) = store.lookup(handle: entries[1].handle) else {
            XCTFail("both occurrence handles must resolve to .found")
            return
        }
        XCTAssertTrue(first.backingElement === backingA)
        XCTAssertTrue(second.backingElement === backingB)
    }

    func testMultipleElementsGetDistinctHandles() {
        let store = SnapshotStore()
        let entries = store.refreshSnapshot(elements: [
            ElementSnapshot(
                identifier: "a", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
            ElementSnapshot(
                identifier: "b", label: nil, value: nil,
                traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
            ),
        ])
        XCTAssertEqual(entries.count, 2)
        XCTAssertNotEqual(entries[0].handle, entries[1].handle)
    }

    // BE-0287: concurrent HTTP handling lets /elements and /tap reach the store
    // at once, so refreshSnapshot and lookup must be safe to call in parallel.
    // Without the store's lock this hammering trips a data race (a crash on its
    // own, a diagnosed race under ThreadSanitizer).
    func testConcurrentRefreshAndLookupIsSafe() {
        let store = SnapshotStore()
        let snapshot = ElementSnapshot(
            identifier: "x", label: nil, value: nil,
            traits: [], frame: (0, 0, 1, 1), backingElement: NSObject()
        )
        DispatchQueue.concurrentPerform(iterations: 1000) { index in
            if index.isMultiple(of: 2) {
                _ = store.refreshSnapshot(elements: [snapshot])
            } else {
                _ = store.lookup(handle: "h-1-0")
            }
        }
    }
}
