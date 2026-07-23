import Foundation
import ObjCExceptionCatcher

final class Router {
    private let provider: ElementProviding
    private let store = SnapshotStore()

    init(provider: ElementProviding) {
        self.provider = provider
    }

    func handle(_ request: HTTPRequest) -> HTTPResponse {
        switch (request.method, request.path) {
        case ("GET", "/health"):
            return handleHealth()
        case ("GET", "/elements"):
            return handleElements()
        case ("POST", "/tap"):
            return handleTap(request)
        case ("POST", "/gesture"):
            return handleGesture(request)
        case ("POST", "/swipe"):
            return handleSwipe(request)
        case ("POST", "/type"):
            return handleType(request)
        case ("POST", "/deleteText"):
            return handleDeleteText(request)
        case ("POST", "/selectAll"):
            return tapResultResponse(onMainCatching(self.provider.selectAll))
        case ("POST", "/copy"):
            return tapResultResponse(onMainCatching(self.provider.copySelection))
        case ("GET", "/screenshot"):
            return handleScreenshot()
        default:
            return .error(404, "unknown endpoint")
        }
    }

    private func handleHealth() -> HTTPResponse {
        .json(200, ["status": "ready"])
    }

    private func handleElements() -> HTTPResponse {
        // A raised snapshot failure (UI in flux) maps to an empty screen, the same as the provider's
        // `try? app.snapshot()` handles a *thrown* one (BE-0105): the run fails loudly downstream when
        // nothing resolves, and the runner keeps serving rather than aborting on the raise.
        let elements = caughtOnMain([]) { self.provider.queryElements() }
        let entries = store.refreshSnapshot(elements: elements)
        let jsonElements: [[String: Any]] = entries.map { entry in
            var dict: [String: Any] = [
                "traits": entry.snapshot.traits,
                "frame": [
                    entry.snapshot.frame.x,
                    entry.snapshot.frame.y,
                    entry.snapshot.frame.width,
                    entry.snapshot.frame.height,
                ],
                "handle": entry.handle,
            ]
            dict["identifier"] = entry.snapshot.identifier
            dict["label"] = entry.snapshot.label
            dict["value"] = entry.snapshot.value
            return dict
        }
        return .json(200, ["status": "ok", "elements": jsonElements])
    }

    private func handleTap(_ request: HTTPRequest) -> HTTPResponse {
        guard let body = request.body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return .error(400, "missing or invalid JSON body")
        }

        if let rawPoint = json["point"] as? [Any], rawPoint.count == 2,
           let px = (rawPoint[0] as? NSNumber)?.doubleValue,
           let py = (rawPoint[1] as? NSNumber)?.doubleValue {
            let result = onMainCatching { self.provider.tapPoint(x: px, y: py) }
            return tapResultResponse(result)
        }

        guard let handle = json["handle"] as? String else {
            return .error(400, "missing handle or point")
        }

        let taps = max((json["taps"] as? NSNumber)?.intValue ?? 1, 1)
        let duration = max((json["duration"] as? NSNumber)?.doubleValue ?? 0, 0)

        switch store.lookup(handle: handle) {
        case .found(let snapshot):
            let result = onMainCatching {
                self.provider.tap(backingElement: snapshot.backingElement, taps: taps, duration: duration)
            }
            return tapResultResponse(result)
        case .stale:
            return .json(200, ["status": "stale"])
        case .notFound:
            return .json(200, ["status": "not-found"])
        }
    }

    private static let knownGestureKinds: Set<String> = ["pinch", "rotate"]

    private func handleGesture(_ request: HTTPRequest) -> HTTPResponse {
        guard let body = request.body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return .error(400, "missing or invalid JSON body")
        }
        guard let handle = json["handle"] as? String else {
            return .error(400, "missing handle")
        }
        guard let kind = json["kind"] as? String, Self.knownGestureKinds.contains(kind) else {
            return .error(400, "missing or unknown gesture kind")
        }
        let scale = (json["scale"] as? NSNumber)?.doubleValue ?? 1.0
        let radians = (json["radians"] as? NSNumber)?.doubleValue ?? 0.0

        switch store.lookup(handle: handle) {
        case .found(let snapshot):
            let result = onMainCatching {
                self.provider.gesture(
                    backingElement: snapshot.backingElement, kind: kind, scale: scale, radians: radians
                )
            }
            return tapResultResponse(result)
        case .stale:
            return .json(200, ["status": "stale"])
        case .notFound:
            return .json(200, ["status": "not-found"])
        }
    }

    private func handleSwipe(_ request: HTTPRequest) -> HTTPResponse {
        guard let body = request.body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return .error(400, "missing or invalid JSON body")
        }
        guard let rawFrom = json["from"] as? [Any], rawFrom.count == 2,
              let fx = (rawFrom[0] as? NSNumber)?.doubleValue,
              let fy = (rawFrom[1] as? NSNumber)?.doubleValue,
              let rawTo = json["to"] as? [Any], rawTo.count == 2,
              let tx = (rawTo[0] as? NSNumber)?.doubleValue,
              let ty = (rawTo[1] as? NSNumber)?.doubleValue else {
            return .error(400, "missing or invalid from/to coordinates")
        }
        let result = onMainCatching { self.provider.swipe(fromX: fx, fromY: fy, toX: tx, toY: ty) }
        return tapResultResponse(result)
    }

    private func handleType(_ request: HTTPRequest) -> HTTPResponse {
        guard let body = request.body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return .error(400, "missing or invalid JSON body")
        }
        guard let text = json["text"] as? String else {
            return .error(400, "missing text")
        }
        let result = onMainCatching { self.provider.typeText(text) }
        return tapResultResponse(result)
    }

    private func handleDeleteText(_ request: HTTPRequest) -> HTTPResponse {
        guard let body = request.body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return .error(400, "missing or invalid JSON body")
        }
        guard let count = (json["count"] as? NSNumber)?.intValue, count > 0 else {
            return .error(400, "missing or non-positive count")
        }
        return tapResultResponse(onMainCatching { self.provider.deleteText(count: count) })
    }

    private func handleScreenshot() -> HTTPResponse {
        guard let png = caughtOnMain(Data?.none, self.provider.screenshot) else {
            return .error(500, "screenshot failed")
        }
        return .png(png)
    }

    private func tapResultResponse(_ result: TapResult) -> HTTPResponse {
        switch result {
        case .ok: return .json(200, ["status": "ok"])
        case .stale: return .json(200, ["status": "stale"])
        case .notFound: return .json(200, ["status": "not-found"])
        }
    }

    private func onMain<T>(_ work: @escaping () -> T) -> T {
        if Thread.isMainThread { return work() }
        var result: T!
        DispatchQueue.main.sync { result = work() }
        return result
    }

    /// Run `work` on the main thread, returning `fallback` if it raises an `NSException`.
    ///
    /// An `XCUIElement` interaction — or an `app.snapshot()` query — raises an `NSException` when the
    /// UI is in flux ("No matches found", a failed/timed-out snapshot). Uncaught it unwinds past Swift
    /// and aborts the resident runner's serve method, so every later request gets "connection
    /// refused"; `continueAfterFailure` does not help, because this is a raised exception, not a
    /// recorded soft failure. Catching it here keeps the runner serving and reports a deterministic
    /// fallback the caller turns into a normal reply. The caught reason (the XCUITest diagnostic the
    /// shim preserves) goes to the runner's stderr, which `BAJUTSU_XCUITEST_RUNNER_LOG` captures.
    private func caughtOnMain<T>(_ fallback: T, _ work: @escaping () -> T) -> T {
        onMain {
            var result = fallback
            do {
                try ObjCExceptionCatcher.catchException { result = work() }
            } catch {
                FileHandle.standardError.write(
                    Data("bajutsu runner: handler raised, reporting fallback: \(error.localizedDescription)\n".utf8)
                )
                return fallback
            }
            return result
        }
    }

    /// A caught actuation, reported as `.stale` on a raised interaction failure.
    ///
    /// `.stale` is safe for the driver's retried actuations (tap/gesture): XCUITest resolves the
    /// element — and raises if it is gone — before synthesizing any event, so a caught interaction
    /// failure precedes any side effect and the re-resolve-and-retry (BE-0289) cannot double-actuate.
    /// The coordinate/keyboard actuations the driver does not retry (`tapPoint`/`swipe`/`type`/…)
    /// surface `.stale` as a loud failure, so they carry no double-actuation risk either.
    private func onMainCatching(_ work: @escaping () -> TapResult) -> TapResult {
        caughtOnMain(.stale, work)
    }
}
