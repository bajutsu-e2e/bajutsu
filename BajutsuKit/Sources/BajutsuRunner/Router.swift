import Foundation
import ObjCExceptionCatcher

final class Router {
    private let provider: ElementProviding
    private let store = SnapshotStore()
    // A separate handle store for SpringBoard alert buttons (BE-0316), so their handles never
    // collide with the app tree's and a `/systemAlert/query` never disturbs the app snapshot.
    private let alertStore = SnapshotStore()
    // Serializes every XCUITest-touching operation so no two run — or *re-enter* — concurrently.
    // `app.snapshot()` / `app.screenshot()` / an `XCUIElement` interaction pumps the main run loop
    // internally while it waits on the app over XPC, and that run-loop spin drains the main dispatch
    // queue. Two concurrent `DispatchQueue.main.sync` handlers (e.g. a scenario `/elements` and an
    // evidence `/screenshot`, which the concurrent HTTP server delivers at once) would then re-enter —
    // the second XCUITest call starts on the main thread *inside* the first's snapshot — and XCUITest
    // is not re-entrant, so the XCTest host aborts (the CI-only mid-run crash: slower/contended hosts
    // widen the re-entrancy window). Held on the *connection* thread before dispatching to main, this
    // lock means a second operation never even enqueues onto main while the first is in flight, so the
    // run-loop spin has nothing re-entrant to drain. `/health` never takes it (it touches no XCUITest
    // state), so it stays answerable during a long operation — the concurrent server's whole purpose.
    // A counting semaphore rather than an `NSLock` because `/screenshot`'s deadline path releases it
    // from the main queue after acquiring it on a connection thread (see `captureScreenshotWithinDeadline`),
    // and `NSLock` requires the locking thread to be the one that unlocks.
    private let actuationLock = DispatchSemaphore(value: 1)

    // How long `/screenshot` waits for the capture before answering without it. Kept strictly below the
    // Python client's 15s read window (`_SOCKET_TIMEOUT_SECONDS`, bajutsu/drivers/xcuitest.py) so the
    // runner's own answer arrives first, with margin for the reply to reach the client. XCUITest gives
    // its own screenshot request roughly 90s — six times the client's window — so without a deadline
    // here every slow capture reaches the client as silence it can only classify as a dead runner.
    // The coupling to the client's constant is held by comment on both sides; no check spans the two
    // languages, so a change to either one has to be carried across by hand.
    static let defaultScreenshotDeadline: TimeInterval = 12

    private let screenshotDeadline: TimeInterval

    /// `screenshotDeadline` is injectable only so a test can assert the deadline path without waiting
    /// out the real one; the runner always takes the default.
    init(provider: ElementProviding, screenshotDeadline: TimeInterval = Router.defaultScreenshotDeadline) {
        self.provider = provider
        self.screenshotDeadline = screenshotDeadline
    }

    func handle(_ request: HTTPRequest) -> HTTPResponse {
        switch (request.method, request.path) {
        case ("GET", "/health"):
            return handleHealth()
        case ("GET", "/elements"):
            return handleElements()
        case ("GET", "/screen"):
            return handleScreen()
        case ("POST", "/tap"):
            return handleTap(request)
        case ("POST", "/isHittable"):
            return handleIsHittable(request)
        case ("POST", "/gesture"):
            return handleGesture(request)
        case ("POST", "/swipe"):
            return handleSwipe(request)
        case ("POST", "/scroll"):
            return handleScroll(request)
        case ("POST", "/type"):
            return handleType(request)
        case ("POST", "/deleteText"):
            return handleDeleteText(request)
        case ("POST", "/selectAll"):
            return tapResultResponse(onMainCatching(self.provider.selectAll))
        case ("POST", "/copy"):
            return tapResultResponse(onMainCatching(self.provider.copySelection))
        case ("POST", "/systemAlert/query"):
            return handleSystemAlertQuery()
        case ("POST", "/systemAlert/tap"):
            return handleSystemAlertTap(request)
        case ("GET", "/screenshot"):
            return handleScreenshot()
        default:
            return .error(404, "unknown endpoint")
        }
    }

    private func handleHealth() -> HTTPResponse {
        .json(200, ["status": "ready"])
    }

    // The device screen size, for the `scroll` action's on-screen stop condition (BE-0326). The
    // element tree excludes the app window and can hold buffered off-screen ScrollView children, so
    // the Python side cannot infer the true viewport from the tree — it reads it here instead.
    private func handleScreen() -> HTTPResponse {
        let size = onMain { self.provider.screenSize() }
        return .json(200, ["width": size.width, "height": size.height])
    }

    private func handleElements() -> HTTPResponse {
        // A raised snapshot failure (UI in flux) maps to an empty screen, the same as the provider's
        // `try? app.snapshot()` handles a *thrown* one (BE-0105): the run fails loudly downstream when
        // nothing resolves, and the runner keeps serving rather than aborting on the raise.
        let elements = caughtOnMain([]) { self.provider.queryElements() }
        return elementsResponse(store: store, elements: elements)
    }

    // The SpringBoard system-alert query (BE-0316): the same element+handle contract as `/elements`,
    // but sourced from the out-of-process permission prompt and keyed into `alertStore`. Empty when no
    // alert is up, so the Python driver polls it against the step's timeout — a condition wait.
    private func handleSystemAlertQuery() -> HTTPResponse {
        let elements = caughtOnMain([]) { self.provider.querySystemAlertButtons() }
        return elementsResponse(store: alertStore, elements: elements)
    }

    private func handleSystemAlertTap(_ request: HTTPRequest) -> HTTPResponse {
        guard let body = request.body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return .error(400, "missing or invalid JSON body")
        }
        guard let handle = json["handle"] as? String else {
            return .error(400, "missing handle")
        }
        switch alertStore.lookup(handle: handle) {
        case .found(let snapshot):
            let result = onMainCatching {
                self.provider.tapSystemAlertButton(backingElement: snapshot.backingElement)
            }
            return tapResultResponse(result)
        case .stale:
            return .json(200, ["status": "stale"])
        case .notFound:
            return .json(200, ["status": "not-found"])
        }
    }

    /// Serialize a fresh element snapshot into the `{status, elements:[…handle…]}` reply both the app
    /// tree (`/elements`) and the SpringBoard alert (`/systemAlert/query`) share (BE-0316).
    private func elementsResponse(store: SnapshotStore, elements: [ElementSnapshot]) -> HTTPResponse {
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

    /// A pure query, unlike `handleTap`: reuses the same handle-resolution outcomes (`.stale` /
    /// `.notFound`), but reports `.ok`/`.notHittable` for a still-live handle without acting on it —
    /// so the driver's `scroll_until_tappable` stop condition can poll this repeatedly with no
    /// side effects.
    private func handleIsHittable(_ request: HTTPRequest) -> HTTPResponse {
        guard let body = request.body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return .error(400, "missing or invalid JSON body")
        }
        guard let handle = json["handle"] as? String else {
            return .error(400, "missing handle")
        }
        switch store.lookup(handle: handle) {
        case .found(let snapshot):
            let result = onMainCatching {
                self.provider.isHittable(backingElement: snapshot.backingElement)
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

    // A directional scroll (BE-0326). Same coordinates as `/swipe`, but the provider drags
    // non-inertially — holding at the end before lift so the scroll view settles where the gesture
    // left it, rather than flinging past the target with momentum.
    private func handleScroll(_ request: HTTPRequest) -> HTTPResponse {
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
        let result = onMainCatching { self.provider.scroll(fromX: fx, fromY: fy, toX: tx, toY: ty) }
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
        switch captureScreenshotWithinDeadline() {
        case .captured(let png):
            return .png(png)
        case .failed:
            return .error(500, "screenshot failed")
        case .overran(let startedCapture):
            // `stalled`, not `error`: the client keys on this status to re-issue the read and, if the
            // stall persists, to recover — the same path a socket timeout used to take, but now with a
            // reason instead of silence. A plain `error` stays a genuine capture failure.
            let what = startedCapture
                ? "the capture did not finish"
                : "another operation still held the runner"
            return .stalled("screenshot exceeded its \(screenshotDeadline)s deadline: \(what)")
        }
    }

    /// The outcome of a screenshot bounded by `screenshotDeadline`.
    ///
    /// `overran` is not a failed capture — it says only that no image arrived inside the deadline.
    /// `startedCapture` distinguishes the two ways that happens: the capture ran and did not finish, or
    /// the deadline expired while another operation still held the lock, so this request never dispatched.
    private enum DeadlinedCapture {
        case captured(Data)
        case failed  // the capture returned within the deadline, but produced no image
        case overran(startedCapture: Bool)
    }

    /// A box for the captured bytes, handed between the main queue that fills it and the connection
    /// thread that reads it. The `done` semaphore orders the two, so the read never races the write.
    private final class CaptureBox {
        var data: Data?
    }

    /// Capture a screenshot, giving up the *wait* — never the lock — once `screenshotDeadline` expires.
    ///
    /// Only a screenshot may be abandoned this way, and the reason is a correctness one rather than
    /// caution: a screenshot is a pure read, so reporting failure cannot be contradicted by a side
    /// effect that lands afterwards. An actuation could — a handler that reported failure at 12s while
    /// the tap it synthesized landed at 40s would tell the client the screen was untouched when it had
    /// changed — so every actuation keeps `onMain`'s unbounded wait.
    ///
    /// The serialization invariant is unchanged: at most one XCUITest operation is ever enqueued on the
    /// main queue. `actuationLock` is acquired here but released by the dispatched block once the
    /// capture actually returns, so abandoning the wait never lets a second operation enqueue onto main
    /// while this one is still pumping the run loop — the re-entrancy that aborts the XCTest host. The
    /// main thread therefore stays busy after an `overran` reply, and the next request waits for it;
    /// what this buys is an attributable answer on the request that caused the stall, and a reply the
    /// client is still listening for.
    ///
    /// One deadline covers the *whole* handler, lock wait included, because the lock is exactly what a
    /// second stalled screenshot waits on: an abandoned capture keeps the lock for as long as XCUITest
    /// gives its own request (~90s), so bounding only the capture would leave the next request blocked
    /// well past the client's window — reaching it as the silence this exists to remove. A request that
    /// cannot even acquire the lock in time never dispatches, so there is nothing to abandon.
    private func captureScreenshotWithinDeadline() -> DeadlinedCapture {
        // Already on main (a direct call): there is no second thread to wait from, so no deadline is
        // possible and nothing to serialize against — the same in-place case `onMain` handles.
        if Thread.isMainThread { return outcome(caught(Data?.none, provider.screenshot)) }
        let deadline = DispatchTime.now() + screenshotDeadline
        guard actuationLock.wait(timeout: deadline) == .success else {
            return .overran(startedCapture: false)
        }
        let box = CaptureBox()
        let done = DispatchSemaphore(value: 0)
        DispatchQueue.main.async { [self] in
            box.data = caught(Data?.none, provider.screenshot)
            // Ownership of the lock passes to this block, and it is released only now — after the
            // capture returned — so the invariant above holds even when the waiter has already replied.
            actuationLock.signal()
            done.signal()
        }
        guard done.wait(timeout: deadline) == .success else { return .overran(startedCapture: true) }
        return outcome(box.data)
    }

    private func outcome(_ data: Data?) -> DeadlinedCapture {
        data.map(DeadlinedCapture.captured) ?? .failed
    }

    private func tapResultResponse(_ result: TapResult) -> HTTPResponse {
        switch result {
        case .ok: return .json(200, ["status": "ok"])
        case .stale: return .json(200, ["status": "stale"])
        case .notFound: return .json(200, ["status": "not-found"])
        case .notHittable: return .json(200, ["status": "not-hittable"])
        }
    }

    private func onMain<T>(_ work: @escaping () -> T) -> T {
        // Already on main (a direct call, or a nested one from inside another operation's work): run
        // in place. Taking `actuationLock` here would deadlock a nested call against the outer hold,
        // and there is nothing to serialize against — the main thread runs one block at a time.
        if Thread.isMainThread { return work() }
        // Off the main thread (an HTTP connection handler): serialize on the connection side *before*
        // dispatching to main, so a second XCUITest operation never enqueues onto the main queue while
        // the first is mid-flight and pumping the run loop — the re-entrancy that aborts the XCTest
        // host (see `actuationLock`). The lock is released only after the main-thread work returns.
        actuationLock.wait()
        defer { actuationLock.signal() }
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
        onMain { self.caught(fallback, work) }
    }

    /// The exception guard itself, without the dispatch — `caughtOnMain` runs it through `onMain`, and
    /// `captureScreenshotWithinDeadline` runs it inside its own main-queue block.
    private func caught<T>(_ fallback: T, _ work: () -> T) -> T {
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
