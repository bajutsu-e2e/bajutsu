import Foundation
import XCTest

@testable import BajutsuKit

/// The background search a real device runs for its collector, and the reports that wait for it.
final class CollectorResolutionTests: XCTestCase {
    private final class Posts: @unchecked Sendable {
        private let lock = NSLock()
        private var items: [(String, URL)] = []
        func add(_ payload: [String: Any], _ url: URL) {
            lock.lock()
            items.append((payload["n"] as? String ?? "", url))
            lock.unlock()
        }
        var all: [(String, URL)] {
            lock.lock()
            defer { lock.unlock() }
            return items
        }
    }

    private let first = URL(string: "http://192.0.2.7:4100")!
    private let second = URL(string: "http://198.51.100.9:4100")!

    func testReportsWaitForTheCollectorThenGoInOrder() {
        // The first rounds find nothing (the Local Network prompt still holds every connection);
        // a later round finds the second candidate, and the held reports follow it there.
        let posts = Posts()
        let resolution = CollectorResolution { payload, url in posts.add(payload, url) }
        let rounds = Rounds()
        let settled = expectation(description: "settled")
        resolution.search(
            [first, second], credential: .bearer("t"), deadline: 5, retry: 0.01,
            probe: { url, _, done in done(rounds.answer(url, after: 2, from: self.second)) },
            onSettled: { url in
                XCTAssertEqual(url, self.second)
                settled.fulfill()
            })
        XCTAssertTrue(resolution.isExpected)
        resolution.send(["n": "a"], path: nil)
        resolution.send(["n": "b"], path: "transitions")
        wait(for: [settled], timeout: 5)
        XCTAssertEqual(posts.all.map(\.0), ["a", "b"])
        XCTAssertEqual(posts.all.map(\.1), [second, second.appendingPathComponent("transitions")])
        resolution.send(["n": "c"], path: nil)  // once known, a report goes straight out
        XCTAssertEqual(posts.all.last?.0, "c")
    }

    func testASearchThatNeverAnswersDropsWhatItHeld() {
        let posts = Posts()
        let resolution = CollectorResolution { payload, url in posts.add(payload, url) }
        let settled = expectation(description: "gave up")
        resolution.search(
            [first, second], credential: nil, deadline: 0.05, retry: 0.01,
            probe: { _, _, done in done(false) },
            onSettled: { url in
                XCTAssertNil(url)
                settled.fulfill()
            })
        resolution.send(["n": "a"], path: nil)
        wait(for: [settled], timeout: 5)
        XCTAssertFalse(resolution.isExpected)
        resolution.send(["n": "b"], path: nil)
        XCTAssertTrue(posts.all.isEmpty)
    }

    func testAFullBufferCountsWhatItCouldNotHold() {
        let resolution = CollectorResolution(capacity: 1) { _, _ in }
        resolution.search([first, second], credential: nil, deadline: 5, retry: 0.5, probe: { _, _, _ in })
        resolution.send(["n": "a"], path: nil)
        resolution.send(["n": "b"], path: nil)
        XCTAssertEqual(resolution.dropped, 1)
    }

    func testTheSimulatorsOneURLIsSettledWithoutASearch() {
        let posts = Posts()
        let resolution = CollectorResolution { payload, url in posts.add(payload, url) }
        resolution.settle(first)
        resolution.send(["n": "a"], path: nil)
        XCTAssertEqual(posts.all.map(\.1), [first])
    }
}

/// Answers `true` for `target` once `after` rounds have passed, counting rounds by `target`'s probes.
private final class Rounds: @unchecked Sendable {
    private let lock = NSLock()
    private var seen = 0
    func answer(_ url: URL, after: Int, from target: URL) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard url == target else { return false }
        seen += 1
        return seen > after
    }
}
