import Foundation
import XCTest

@testable import BajutsuKit

/// Unit tests for the touch-marker model — the part of the touch visualization that is pure
/// Foundation and needs no Simulator (mirrors `BajutsuScreenTransitionStoreTests`). The
/// `UIWindow.sendEvent(_:)` hook and the `CALayer` drawing need a live UIKit app, so they are
/// confirmed on-device instead.
final class BajutsuTouchMarkerTests: XCTestCase {
    private func point(_ x: Double, _ y: Double) -> BajutsuTouchPoint {
        BajutsuTouchPoint(x: x, y: y)
    }

    func testTapMarkSurvivesTheTouchLifting() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(10, 10))
        model.apply(id: 1, phase: .ended, at: point(10, 10))

        // The whole point of the design: nothing removes the mark when the touch ends, so the
        // step's screenshot still shows where the step touched.
        XCTAssertEqual(model.visibleMarks.count, 1)
        XCTAssertEqual(model.mark(for: 1)?.isActive, false)
        XCTAssertFalse(model.hasActiveTouch)
    }

    func testNextGestureClearsThePreviousMarks() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(10, 10))
        model.apply(id: 1, phase: .ended, at: point(10, 10))

        let cleared = model.apply(id: 2, phase: .began, at: point(50, 50))

        XCTAssertEqual(cleared, [1])
        XCTAssertEqual(model.visibleMarks.map { $0.id }, [2])
    }

    func testSecondFingerDoesNotEraseTheFirst() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(10, 10))

        // A pinch's second contact begins while the first is still down, so it joins the gesture
        // in progress rather than starting a new one.
        let cleared = model.apply(id: 2, phase: .began, at: point(90, 90))

        XCTAssertEqual(cleared, [])
        XCTAssertEqual(model.visibleMarks.map { $0.id }, [1, 2])
    }

    func testMovementIsRecordedAsATrail() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(0, 0))
        model.apply(id: 1, phase: .moved, at: point(0, 30))
        model.apply(id: 1, phase: .moved, at: point(0, 60))
        model.apply(id: 1, phase: .ended, at: point(0, 90))

        XCTAssertEqual(
            model.mark(for: 1)?.trail,
            [point(0, 0), point(0, 30), point(0, 60), point(0, 90)]
        )
        XCTAssertEqual(model.mark(for: 1)?.point, point(0, 90))
    }

    func testMovesCloserThanTheSpacingAreNotRecorded() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(0, 0))
        model.apply(id: 1, phase: .moved, at: point(0, 1))
        model.apply(id: 1, phase: .moved, at: point(0, 1.5))

        XCTAssertEqual(model.mark(for: 1)?.trail, [point(0, 0)])
        // The contact itself still tracks the finger even when the trail records nothing.
        XCTAssertEqual(model.mark(for: 1)?.point, point(0, 1.5))
    }

    func testTrailIsBounded() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(0, 0))
        for step in 1...(BajutsuTouchMarker.maximumTrailPoints + 50) {
            model.apply(id: 1, phase: .moved, at: point(0, Double(step) * 10))
        }

        XCTAssertEqual(model.mark(for: 1)?.trail.count, BajutsuTouchMarker.maximumTrailPoints)
    }

    func testStationaryTouchKeepsItsMarkInPlace() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(20, 20))
        model.apply(id: 1, phase: .stationary, at: point(20, 20))

        XCTAssertEqual(model.mark(for: 1)?.point, point(20, 20))
        XCTAssertTrue(model.hasActiveTouch)
    }

    func testEventsForAnUnknownTouchAreIgnored() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .moved, at: point(5, 5))
        model.apply(id: 1, phase: .ended, at: point(5, 5))

        XCTAssertTrue(model.visibleMarks.isEmpty)
    }

    func testATouchMissingFromTheEventIsEnded() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(0, 0))
        // The touch's `.ended` never arrives — a window torn down mid-gesture stops delivering
        // events. Without recovery `hasActiveTouch` would latch true and never clear again.
        model.endTouchesMissing(from: [])

        XCTAssertFalse(model.hasActiveTouch)
        XCTAssertEqual(model.visibleMarks.count, 1, "the mark stays visible; only its touch ended")
    }

    func testATouchStillInTheEventStaysActive() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(0, 0))
        model.apply(id: 2, phase: .began, at: point(9, 9))
        model.endTouchesMissing(from: [1, 2])

        XCTAssertTrue(model.hasActiveTouch)
    }

    func testAGestureClearsAgainAfterALostTouchEnd() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(0, 0))
        model.endTouchesMissing(from: [])

        XCTAssertEqual(model.apply(id: 2, phase: .began, at: point(5, 5)), [1])
    }

    func testClearAllReportsEverythingItRemoved() {
        var model = BajutsuTouchModel<Int>()
        model.apply(id: 1, phase: .began, at: point(0, 0))
        model.apply(id: 2, phase: .began, at: point(1, 1))

        XCTAssertEqual(model.clearAll(), [1, 2])
        XCTAssertTrue(model.visibleMarks.isEmpty)
    }
}
