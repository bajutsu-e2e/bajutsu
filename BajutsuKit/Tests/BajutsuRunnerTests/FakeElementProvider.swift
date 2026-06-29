import Foundation
@testable import BajutsuRunner

final class FakeElementProvider: ElementProviding {
    var elementsToReturn: [ElementSnapshot] = []
    var tapResult: TapResult = .ok
    var screenshotData: Data? = Data([0x89, 0x50, 0x4E, 0x47])

    var tapCalls: [(backingElement: AnyObject, taps: Int, duration: TimeInterval)] = []
    var tapPointCalls: [(x: Double, y: Double)] = []

    func queryElements() -> [ElementSnapshot] { elementsToReturn }

    func tap(backingElement: AnyObject, taps: Int, duration: TimeInterval) -> TapResult {
        tapCalls.append((backingElement, taps, duration))
        return tapResult
    }

    func tapPoint(x: Double, y: Double) -> TapResult {
        tapPointCalls.append((x, y))
        return tapResult
    }

    func screenshot() -> Data? { screenshotData }
}
