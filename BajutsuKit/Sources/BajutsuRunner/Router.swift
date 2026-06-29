import Foundation

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
        let elements = onMain { self.provider.queryElements() }
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

        if let point = json["point"] as? [Double], point.count == 2 {
            let result = onMain { self.provider.tapPoint(x: point[0], y: point[1]) }
            return tapResultResponse(result)
        }

        guard let handle = json["handle"] as? String else {
            return .error(400, "missing handle or point")
        }

        let taps = (json["taps"] as? Int) ?? 1
        let duration = (json["duration"] as? Double) ?? 0

        switch store.lookup(handle: handle) {
        case .found(let snapshot):
            let result = onMain {
                self.provider.tap(backingElement: snapshot.backingElement, taps: taps, duration: duration)
            }
            return tapResultResponse(result)
        case .stale:
            return .json(200, ["status": "stale"])
        case .notFound:
            return .json(200, ["status": "not-found"])
        }
    }

    private func handleScreenshot() -> HTTPResponse {
        guard let png = onMain(self.provider.screenshot) else {
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
}
