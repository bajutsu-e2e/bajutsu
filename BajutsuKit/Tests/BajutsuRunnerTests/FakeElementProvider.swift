import Foundation
@testable import BajutsuRunner

final class FakeElementProvider: ElementProviding {
    var elementsToReturn: [ElementSnapshot] = []
    var tapResult: TapResult = .ok
    var screenshotData: Data? = Data([0x89, 0x50, 0x4E, 0x47])
    // When set, `tap` raises this NSException instead of returning — standing in for the XCUITest
    // "No matches found" interaction failure the runner must survive (the Router catches it as stale).
    var tapRaises: NSException?
    // When set, `queryElements` raises this — standing in for an `app.snapshot()` that raises while
    // the UI is in flux (the Router catches it as an empty screen, not a runner crash).
    var queryRaises: NSException?

    var tapCalls: [(backingElement: AnyObject, taps: Int, duration: TimeInterval)] = []
    var tapPointCalls: [(x: Double, y: Double)] = []
    var gestureCalls: [(backingElement: AnyObject, kind: String, scale: Double, radians: Double)] = []
    var swipeCalls: [(fromX: Double, fromY: Double, toX: Double, toY: Double)] = []
    var typeCalls: [String] = []
    var deleteTextCalls: [Int] = []
    var selectAllCalls = 0
    var copyCalls = 0

    func queryElements() -> [ElementSnapshot] {
        if let exception = queryRaises { exception.raise() }
        return elementsToReturn
    }

    func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult {
        tapCalls.append((backingElement, taps, duration))
        if let exception = tapRaises { exception.raise() }
        return tapResult
    }

    func tapPoint(x: Double, y: Double) -> TapResult {
        tapPointCalls.append((x, y))
        return tapResult
    }

    func gesture(backingElement: AnyObject, kind: String, scale: Double, radians: Double) -> TapResult {
        gestureCalls.append((backingElement, kind, scale, radians))
        return tapResult
    }

    func swipe(fromX: Double, fromY: Double, toX: Double, toY: Double) -> TapResult {
        swipeCalls.append((fromX, fromY, toX, toY))
        return tapResult
    }

    func typeText(_ text: String) -> TapResult {
        typeCalls.append(text)
        return tapResult
    }

    func deleteText(count: Int) -> TapResult {
        deleteTextCalls.append(count)
        return tapResult
    }

    func selectAll() -> TapResult {
        selectAllCalls += 1
        return tapResult
    }

    func copySelection() -> TapResult {
        copyCalls += 1
        return tapResult
    }

    func screenshot() -> Data? { screenshotData }
}
