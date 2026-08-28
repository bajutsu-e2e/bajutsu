import Foundation

// The whole channel is compiled out unless this condition selects it, and that is the point rather
// than a convenience: without it a release binary that still calls `startIfEnabled()` would let a
// launch environment variable turn on remote control of the app's own behaviour. Every other
// BajutsuKit feature is guarded by its launch-env key alone; this one is guarded twice, so the
// launch env is never the only thing standing between a shipped app and an inbound command
// (BE-0365 unit 2). Build with `-Xswiftc -DBAJUTSU_ENABLE_CONTROL_CHANNEL` to select it.
#if BAJUTSU_ENABLE_CONTROL_CHANNEL

/// One command bajutsu asked this app to apply (BE-0365).
struct BajutsuAppCommand {
    let id: String

    /// The capability exactly as bajutsu spelled it, deliberately not an enum case. An app built
    /// against an older BajutsuKit than the bajutsu driving it must be able to *report* a
    /// capability it does not know, and an enum would have dropped the command at decode time —
    /// leaving the acknowledgement wait to time out with nothing to say about why.
    let capability: String

    /// The whole command object, so each capability reads its own fields. The channel's payload is
    /// not one shape: a toggle carries `enabled`, and the mid-scenario stub table (unit 4) arrives
    /// as a sibling shape discriminated on `capability`, so decoding to a fixed struct here would
    /// have to be widened for every capability that follows.
    let payload: [String: Any]
}

/// What the app has to say about one command it drained (BE-0365).
///
/// `applied` and `reason` are separate because "applied it" and "drained it and could not apply
/// it" must not reach bajutsu's acknowledgement wait as the same message: the wait fails at once on
/// the second, quoting the reason, instead of timing out blind (BE-0365 unit 3).
struct BajutsuCommandOutcome {
    let applied: Bool
    let reason: String

    static let accepted = BajutsuCommandOutcome(applied: true, reason: "")

    static func refused(_ reason: String) -> BajutsuCommandOutcome {
        BajutsuCommandOutcome(applied: false, reason: reason)
    }
}

/// The app-side half of bajutsu's in-app control channel (BE-0365).
///
/// Everything else BajutsuKit does is outbound: the app reports what happened and bajutsu listens.
/// This is the one direction that runs the other way, so that a capability bajutsu put inside the
/// app can change *within* a scenario rather than only at launch. It opens no socket. The app polls
/// the collector it already reports to over the authenticated `GET /commands` the collector serves,
/// applies what it drains, and reports the result on `/commands/ack` — so the channel needs no new
/// server, no new port, and no authentication scheme beyond the per-run token bajutsu already
/// injected.
///
/// **What it may carry.** Commands address bajutsu's own in-app instrumentation — the touch
/// visualization, the stub table — and never the application's own state. A command that seeded app
/// data or drove navigation would move per-app knowledge into the tool, which prime directive 3
/// keeps out. Nothing here influences whether a step passes, and no assertion reads from it.
///
/// **Test/debug only**, and gated twice: compiled out unless `BAJUTSU_ENABLE_CONTROL_CHANNEL`
/// selects it, and inert when compiled in unless `BAJUTSU_CONTROL_CHANNEL` is `1` on the launch
/// environment.
enum BajutsuControlChannel {
    /// The launch environment variable that turns the channel on, once it is compiled in. Off on
    /// any other value and on its absence, so an app that never sees it behaves as it does today.
    static let activationKey = "BAJUTSU_CONTROL_CHANNEL"

    /// The wire spelling of `InAppCapability.TOUCH_VISUALIZATION` in `bajutsu/evidence/network.py`.
    /// The two sides share no generated schema, so this string is the contract; changing one
    /// without the other makes every command land in `apply`'s unsupported branch.
    static let touchVisualizationCapability = "touch_visualization"

    /// How long the app waits between drains.
    ///
    /// Polling buys the absence of a listener at the price of latency: a command takes effect no
    /// sooner than the next poll, and bajutsu pays that once per command, serialized behind its
    /// acknowledgement wait. Short enough that the wait is not dominated by it, long enough that an
    /// idle channel is not a busy loop inside the app whose timing a test is measuring.
    static let pollInterval: TimeInterval = 0.15

    /// The two endpoints and the token that reaches them — resolved once at start, then carried
    /// through the poll round as a value so no shared mutable state is read off `queue`.
    struct Endpoint {
        let commands: URL
        let acknowledge: URL
        let token: String
    }

    /// Serial, and every mutation below happens on it. The poll round hops between this queue, a
    /// `URLSession` completion, and the main thread, so confining the state to one queue is what
    /// keeps that from being a race.
    private static let queue = DispatchQueue(label: "com.bajutsu.control-channel")
    private static var endpoint: Endpoint?
    /// Bumped by every start and every stop, and carried through a poll round so a round started
    /// before a `stop()` cannot schedule the next one after a later `start` — which would leave two
    /// loops draining the same queue, one of them against the endpoint the stopped run resolved.
    private static var currentGeneration = 0

    /// Whether a poll loop is live. Read through `queue`, so a caller that has just started or
    /// stopped the channel sees the settled answer rather than a half-applied one.
    static var isRunning: Bool { queue.sync { endpoint != nil } }

    /// The endpoints a collector root serves, split out so the paths are checkable without a run.
    static func endpoints(collector: URL, token: String) -> Endpoint {
        let commands = collector.appendingPathComponent("commands")
        return Endpoint(
            commands: commands, acknowledge: commands.appendingPathComponent("ack"), token: token
        )
    }

    /// Start polling when the launch env asks for the channel and a collector is there to poll.
    ///
    /// The collector is passed in rather than read back off `BajutsuNet` so the dependency is one
    /// direction only: this type never reaches into the observation half it is unrelated to.
    ///
    /// Args are the app's launch environment, the collector root `BajutsuNet` resolved, and the
    /// per-run token; a nil collector or an empty token leaves the channel inert, because a command
    /// nobody can acknowledge is worse than no channel at all.
    static func startIfEnabled(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        collector: URL?,
        token: String?
    ) {
        guard environment[activationKey] == "1" else { return }
        guard let collector, let token, !token.isEmpty else { return }
        let resolved = endpoints(collector: collector, token: token)
        queue.async {
            guard endpoint == nil else { return }  // idempotent, like every other startIfEnabled
            endpoint = resolved
            currentGeneration += 1
            poll(resolved, generation: currentGeneration)
        }
    }

    /// Stop polling. Idempotent, and synchronous so a caller can rely on no further drain landing.
    static func stop() {
        queue.sync {
            endpoint = nil
            currentGeneration += 1
        }
    }

    /// Whether a drain answered with something no later poll could recover from.
    ///
    /// A rejected token and a path the collector does not serve are both misconfiguration, not
    /// weather: retrying either cannot turn it into a working channel, and unit 1 answers 404 on an
    /// unknown `GET` precisely so a version-skewed poll is legible rather than a silent hang.
    /// Everything else — a transport error, a 5xx — keeps polling, since a collector can be busy.
    static func isTerminal(status: Int?) -> Bool {
        status == 401 || status == 404
    }

    // --- the poll round ---

    private static func poll(_ channel: Endpoint, generation: Int) {
        var request = URLRequest(url: channel.commands)
        request.httpMethod = "GET"
        // The channel's only `GET`, and the report session carries a cache. Unit 1's 200 sends no
        // cache directive and no validator, so the policy is pinned here rather than left to
        // CFNetwork's heuristics: a cached empty drain is a channel that silently never delivers,
        // which the acknowledgement wait can report only as a blind timeout.
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("Bearer \(channel.token)", forHTTPHeaderField: "Authorization")
        BajutsuNet.reportSession.dataTask(with: request) { data, response, _ in
            let status = (response as? HTTPURLResponse)?.statusCode
            // Every step below runs on `queue`, applying included: a drain still in flight when
            // `stop()` lands must not reach the app or acknowledge against the stopped run's
            // endpoint, which is what `stop()` promises its caller.
            queue.async {
                // The next round is scheduled from this round's completion rather than by a
                // repeating timer, so a slow or unreachable collector cannot stack polls on top of
                // each other.
                guard generation == currentGeneration else { return }
                // Ending the loop here rather than hammering an answer that will never change: an
                // unrecoverable drain would otherwise leave a 150 ms timer running inside the app
                // under test for the rest of the process's life, which is the perturbation the
                // channel is supposed to avoid. bajutsu still learns of it, through the
                // acknowledgement wait's own loud timeout (unit 3).
                guard !isTerminal(status: status) else {
                    endpoint = nil
                    currentGeneration += 1
                    return
                }
                queue.asyncAfter(deadline: .now() + pollInterval) {
                    guard generation == currentGeneration else { return }
                    poll(channel, generation: generation)
                }
                guard let data, status == 200 else { return }
                let drained = commands(from: data)
                // An empty drain is the overwhelmingly common case, and it stops here: an idle
                // channel never schedules main-thread work, so it cannot perturb the app's own
                // timing — the very thing the test around it is measuring.
                guard !drained.isEmpty else { return }
                DispatchQueue.main.async {
                    for command in drained {
                        acknowledge(command, apply(command), to: channel)
                    }
                }
            }
        }.resume()
    }

    /// The commands in one drained response, in the order bajutsu queued them.
    ///
    /// A response that is not a JSON array, and an element that is not an object carrying a
    /// non-empty string `id`, yield nothing: an id is the only thing that ties a command to the
    /// wait expecting it, so a command without one cannot be acknowledged and reporting it would
    /// mean inventing the id to report it under. Everything else survives decoding on purpose and
    /// is refused by `apply` instead, where the refusal reaches bajutsu with its reason.
    static func commands(from data: Data) -> [BajutsuAppCommand] {
        let parsed = try? JSONSerialization.jsonObject(with: data)
        guard let items = parsed as? [Any] else { return [] }
        return items.compactMap { item in
            guard let object = item as? [String: Any],
                  let id = object["id"] as? String, !id.isEmpty
            else { return nil }
            return BajutsuAppCommand(
                id: id, capability: object["capability"] as? String ?? "", payload: object
            )
        }
    }

    /// Apply one command, on the main thread — the same thread the instrumentation it addresses
    /// already runs on.
    static func apply(_ command: BajutsuAppCommand) -> BajutsuCommandOutcome {
        switch command.capability {
        case touchVisualizationCapability:
            guard let enabled = command.payload["enabled"] as? Bool else {
                return .refused("\(touchVisualizationCapability) command carries no boolean 'enabled'")
            }
            BajutsuTouch.setMarkersVisible(enabled)
            return .accepted
        case "":
            return .refused("command names no capability")
        default:
            // Version skew, not corruption: a newer bajutsu naming a capability this build predates.
            // Refusing it by name is what turns that into a legible failure on the bajutsu side.
            return .refused("unsupported capability '\(command.capability)'")
        }
    }

    /// The acknowledgement body for one applied-or-refused command, shaped for the collector's
    /// `AppCommandReport` (`id`, `applied`, `reason`).
    static func report(
        for command: BajutsuAppCommand, outcome: BajutsuCommandOutcome
    ) -> [String: Any] {
        ["id": command.id, "applied": outcome.applied, "reason": outcome.reason]
    }

    private static func acknowledge(
        _ command: BajutsuAppCommand, _ outcome: BajutsuCommandOutcome, to channel: Endpoint
    ) {
        // The report goes out on the session the exchange and transition reports already use, so
        // the channel adds no second way for the app to reach the collector.
        BajutsuNet.postJSON(
            report(for: command, outcome: outcome),
            to: channel.acknowledge,
            token: channel.token,
            session: BajutsuNet.reportSession
        )
    }
}

#endif
