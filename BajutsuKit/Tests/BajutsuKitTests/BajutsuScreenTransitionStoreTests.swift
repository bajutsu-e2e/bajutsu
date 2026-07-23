import Foundation
import XCTest

@testable import BajutsuKit

/// Unit tests for the screen-transition store — the part of BE-0310 that is pure
/// Foundation and needs no Simulator (mirrors `BajutsuMocksTests`). The appearance hook
/// itself (`BajutsuScreen`'s `viewDidAppear` swizzle) needs a live view-controller
/// transition, so it is confirmed on-device instead (BE-0310 Unit 5).
final class BajutsuScreenTransitionStoreTests: XCTestCase {
    func testRecordAppendsInOrder() {
        let store = BajutsuScreenTransitionStore.shared
        let before = store.transitions.count
        store.record(BajutsuScreenTransition(kind: "screenChanged", seq: 1))
        store.record(BajutsuScreenTransition(kind: "screenChanged", seq: 2))
        XCTAssertEqual(store.transitions.count, before + 2)
        XCTAssertEqual(store.latest?.seq, 2)
    }

    func testRecordFromBackgroundThreadIsMarshaledToMain() {
        let store = BajutsuScreenTransitionStore.shared
        let before = store.transitions.count
        let expectation = expectation(description: "recorded")
        DispatchQueue.global().async {
            store.record(BajutsuScreenTransition(kind: "screenChanged", seq: 99))
            DispatchQueue.main.async { expectation.fulfill() }
        }
        wait(for: [expectation], timeout: 1.0)
        XCTAssertEqual(store.transitions.count, before + 1)
    }
}
