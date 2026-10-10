import Foundation

/// Which of the offered collector URLs this app reports to, found in the background on a real device.
///
/// A real device is offered one collector URL per host address (`BajutsuNet.candidateURLs`) and
/// keeps the first that answers its probe. The search must not block the launch, and it cannot
/// settle quickly on a fresh install: iOS holds every local-network connection until the user
/// answers the Local Network prompt, which the run's own alert guard answers only once the app is
/// up. So the search retries until a candidate answers or `deadline` passes, and reports made
/// meanwhile wait in a buffer, sent in order once a collector is known. Without that buffer the
/// launch's earliest exchanges would be lost, and a network assertion on them would fail.
final class CollectorResolution: @unchecked Sendable {
    /// A report waiting for the collector: its JSON body and the path under the collector URL.
    struct Pending {
        let payload: [String: Any]
        let path: String?
    }

    typealias Post = (_ payload: [String: Any], _ url: URL) -> Void

    private let lock = NSLock()
    private var resolved: URL?
    private var searching = false
    private var buffer: [Pending] = []
    private let capacity: Int
    private let post: Post

    /// Reports dropped because the buffer was full: a long search loses the newest, and says so in
    /// the device log, where a partial network record would otherwise have no explanation.
    private(set) var dropped = 0

    init(capacity: Int = 1000, post: @escaping Post) {
        self.capacity = capacity
        self.post = post
    }

    /// The collector, once one answered.
    var url: URL? {
        lock.lock()
        defer { lock.unlock() }
        return resolved
    }

    /// Whether a report made now will be delivered: a collector is known, or still being looked for.
    var isExpected: Bool {
        lock.lock()
        defer { lock.unlock() }
        return resolved != nil || searching
    }

    /// Take `url` as the collector at once, with no search (the Simulator's single URL).
    func settle(_ url: URL?) {
        lock.lock()
        resolved = url
        searching = false
        lock.unlock()
    }

    /// Search `candidates` in the background until one answers or `deadline` seconds pass.
    ///
    /// `onSettled` runs once with the chosen URL, or nil when the search gave up, after the buffer
    /// has been flushed or discarded.
    func search(
        _ candidates: [URL], token: String?, deadline: TimeInterval = 120, retry: TimeInterval = 1,
        probe: @escaping BajutsuNet.CollectorProbe = BajutsuNet.pingCollector,
        onSettled: @escaping (URL?) -> Void = { _ in }
    ) {
        lock.lock()
        searching = true
        lock.unlock()
        DispatchQueue.global(qos: .utility).async { [self] in
            let end = Date().addingTimeInterval(deadline)
            var found: URL?
            repeat {
                found = BajutsuNet.reachableCollector(candidates, token: token, probe: probe)
                if found == nil, Date() < end { Thread.sleep(forTimeInterval: retry) }
            } while found == nil && Date() < end
            finish(found)
            onSettled(found)
        }
    }

    /// Send `payload` to the collector now, hold it while one is being looked for, or drop it.
    func send(_ payload: [String: Any], path: String?) {
        lock.lock()
        if let resolved {
            lock.unlock()
            post(payload, Self.target(resolved, path))
            return
        }
        if searching {
            if buffer.count < capacity {
                buffer.append(Pending(payload: payload, path: path))
            } else {
                dropped += 1
                if dropped == 1 {
                    NSLog("BajutsuKit: collector buffer full (%d reports); dropping newer ones", capacity)
                }
            }
        }
        lock.unlock()
    }

    private func finish(_ url: URL?) {
        lock.lock()
        resolved = url
        searching = false
        let held = buffer
        let lost = dropped
        buffer = []
        lock.unlock()
        guard let url else {
            NSLog("BajutsuKit: no collector answered; discarding %d held reports", held.count)
            return
        }
        if lost > 0 {
            NSLog("BajutsuKit: collector found; %d reports were dropped while searching", lost)
        }
        for report in held {
            post(report.payload, Self.target(url, report.path))
        }
    }

    private static func target(_ url: URL, _ path: String?) -> URL {
        path.map { url.appendingPathComponent($0) } ?? url
    }
}
