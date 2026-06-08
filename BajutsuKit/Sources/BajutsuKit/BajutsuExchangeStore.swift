import Foundation

/// One captured exchange, surfaced to the host app. This is the same data BajutsuNet
/// POSTs to the collector, exposed in-app so the UI can show what BajutsuKit actually
/// observed (distinct from the app's own response handling).
public struct BajutsuExchange: Identifiable {
    public let id = UUID()
    public let method: String
    public let url: String
    public let path: String
    public let status: Int?
    public let durationMs: Double
    public let error: String?
}

/// Observable store of the exchanges BajutsuKit captured, for the host app to display.
/// `BajutsuNet` records into the shared instance after each request completes; mutations
/// are marshaled to the main thread so SwiftUI can bind to it directly.
public final class BajutsuExchangeStore: ObservableObject {
    public static let shared = BajutsuExchangeStore()

    /// Every captured exchange, in completion order.
    @Published public private(set) var exchanges: [BajutsuExchange] = []

    /// The most recent exchange (nil until the first request completes).
    public var latest: BajutsuExchange? { exchanges.last }

    private init() {}

    func record(_ exchange: BajutsuExchange) {
        if Thread.isMainThread {
            exchanges.append(exchange)
        } else {
            DispatchQueue.main.async { self.exchanges.append(exchange) }
        }
    }
}
