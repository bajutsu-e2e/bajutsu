import Foundation

/// One observed screen-transition event, surfaced to the host app. This is the same
/// data `BajutsuScreen` reports to the collector, exposed in-app so the UI can show what
/// BajutsuKit actually observed (mirrors `BajutsuExchange` / `BajutsuExchangeStore`).
public struct BajutsuScreenTransition: Identifiable {
    public let id = UUID()
    public let kind: String
    public let seq: Int
}

/// Observable store of the screen transitions BajutsuKit observed, for the host app to display.
/// `BajutsuScreen` records into the shared instance after each observed transition; mutations
/// are marshaled to the main thread so SwiftUI can bind to it directly.
public final class BajutsuScreenTransitionStore: ObservableObject {
    public static let shared = BajutsuScreenTransitionStore()

    /// Every observed transition, in occurrence order.
    @Published public private(set) var transitions: [BajutsuScreenTransition] = []

    /// The most recent transition (nil until the first is observed).
    public var latest: BajutsuScreenTransition? { transitions.last }

    private init() {}

    func record(_ transition: BajutsuScreenTransition) {
        if Thread.isMainThread {
            transitions.append(transition)
        } else {
            DispatchQueue.main.async { self.transitions.append(transition) }
        }
    }
}
