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

    func testOldHandlesBecomeStalAfterRefresh() {
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
}
