import Foundation

/// Collects the XCTest failures recorded while one call runs, so a route can report them.
///
/// The resident runner keeps `continueAfterFailure` on, so a call such as
/// `XCUIApplication.open(_:)` that fails by recording an issue returns normally, and its reply
/// would read as success. `RunnerUITest.record(_:)` forwards every issue here, and only a
/// `collect` in progress keeps it (BE-0487). Every caller runs on the main thread, serialized by
/// `APIHandler`, and the lock keeps a stray issue from another thread from racing the buffer.
final class RecordedIssues: @unchecked Sendable {
    static let shared = RecordedIssues()

    private let lock = NSLock()
    private var buffer: [String]?

    /// Run *work* and return the description of every issue recorded meanwhile, oldest first.
    func collect(_ work: () -> Void) -> [String] {
        locked { buffer = [] }
        work()
        // An NSException unwinding through *work* skips this reset, leaving the buffer collecting
        // until the next `collect` starts it afresh: bounded, and read by no one meanwhile.
        return locked {
            let issues = buffer ?? []
            buffer = nil
            return issues
        }
    }

    /// Keep *description* when a `collect` is in progress; drop it otherwise.
    func record(_ description: String) {
        locked { buffer?.append(description) }
    }

    // `NSLock.withLock` needs iOS 16; the runner deploys to iOS 15.
    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}
