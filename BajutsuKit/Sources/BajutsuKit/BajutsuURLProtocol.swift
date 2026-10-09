import Foundation

/// Intercepts URLSession traffic, forwards it unchanged, and reports each exchange to
/// the bajutsu collector. Registered globally (covers `URLSession.shared` and
/// default-config sessions); `installIntoDefaultConfigurations` swizzles
/// `URLSessionConfiguration` so app-created sessions are covered too.
final class BajutsuURLProtocol: URLProtocol, URLSessionDataDelegate {
    private static let handledKey = "BajutsuHandled"

    private var inner: URLSession?
    private var innerTask: URLSessionDataTask?
    private var responseData = Data()
    private var capturedResponse: URLResponse?
    private var capturedRequestBody: Data?
    private var startedAt = Date()
    // Set once `willPerformHTTPRedirection` hands the load to a fresh protocol instance for the
    // redirected request. The inner task is not cancelled at that point (only told not to
    // auto-follow), so its delegate callbacks keep arriving after `client` has moved on to that
    // new instance; this flag stops them from reaching `client` or `BajutsuNet.report` a second
    // (and racy — 3xx-response-delivered vs. `stopLoading()`-cancelled) time.
    private var didRedirect = false

    // MARK: URLProtocol

    override class func canInit(with request: URLRequest) -> Bool {
        if URLProtocol.property(forKey: handledKey, in: request) != nil { return false }
        guard let url = request.url else { return false }
        // Never intercept the loopback (the collector + any local stub server). Matched on the whole
        // URL, not on `host` alone: a URL whose authority did not survive being forwarded parses with
        // a nil host, and a guard that reads only `host` waves it through — which is exactly how the
        // collector's own POSTs came to be intercepted and re-reported in an amplifying loop. The
        // string test costs nothing and holds whether or not the URL parsed the way it should have.
        let text = url.absoluteString
        for loopback in ["127.0.0.1", "localhost", "[::1]"]
        where url.host == loopback || text.contains("/\(loopback):") || text.contains("/\(loopback)/") {
            return false
        }
        // A real device reports to the collector on a host address, not the loopback, so the
        // guard above does not cover it; the chosen collector is skipped by host and port instead.
        if let collector = BajutsuNet.collectorURL, url.host == collector.host,
           url.port == collector.port {
            return false
        }
        return (url.scheme == "http" || url.scheme == "https")
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let mutable = (request as NSURLRequest).mutableCopy() as? NSMutableURLRequest else {
            client?.urlProtocolDidFinishLoading(self)
            return
        }
        URLProtocol.setProperty(true, forKey: Self.handledKey, in: mutable)
        // A request body is moved to httpBodyStream by the time a URLProtocol sees it, so
        // drain the stream to capture it, then re-attach it as httpBody so the forwarded
        // request still carries the body.
        if let body = request.httpBody {
            capturedRequestBody = body
        } else if let stream = request.httpBodyStream {
            let body = Self.drain(stream)
            capturedRequestBody = body
            mutable.httpBody = body
            mutable.httpBodyStream = nil
        }
        startedAt = Date()
        // Deterministic stub: if a mock matches, answer it and never touch the network.
        if let rule = BajutsuMocks.shared.stub(for: request, body: capturedRequestBody) {
            serveStub(rule)
            return
        }
        // `.default`, not the app's own configuration: `URLProtocol` exposes no public way to
        // recover the `URLSessionConfiguration` (or delegate) a request was issued through, so an
        // app that relies on session-scoped state — an ephemeral/custom cookie storage, a
        // non-default `httpAdditionalHeaders`, or a delegate-driven auth-challenge/TLS-pinning
        // decision — can see different behavior once intercepted. Request-scoped settings
        // (headers, method, body, cachePolicy, timeoutInterval) are unaffected: they live on the
        // `URLRequest` copied above, not on the configuration.
        inner = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        innerTask = inner?.dataTask(with: mutable as URLRequest)
        innerTask?.resume()
    }

    private func serveStub(_ rule: BajutsuMockRule) {
        guard let url = request.url,
              let response = HTTPURLResponse(
                  url: url, statusCode: rule.status, httpVersion: "HTTP/1.1", headerFields: rule.headers)
        else { client?.urlProtocolDidFinishLoading(self); return }
        let deliver = { [weak self] in
            guard let self else { return }
            self.client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            self.client?.urlProtocol(self, didLoad: rule.body)
            self.client?.urlProtocolDidFinishLoading(self)
            BajutsuNet.report(
                request: self.request, requestBody: self.capturedRequestBody, response: response,
                body: rule.body, startedAt: self.startedAt, error: nil, mocked: true
            )
        }
        if rule.delaySeconds > 0 {
            DispatchQueue.global().asyncAfter(deadline: .now() + rule.delaySeconds, execute: deliver)
        } else {
            deliver()
        }
    }

    private static func drain(_ stream: InputStream) -> Data {
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let n = stream.read(&buf, maxLength: buf.count)
            if n <= 0 { break }
            data.append(buf, count: n)
        }
        return data
    }

    override func stopLoading() {
        innerTask?.cancel()
        inner?.invalidateAndCancel()
    }

    // MARK: URLSessionDataDelegate (forward to the client, accumulate for the report)

    func urlSession(
        _ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        guard !didRedirect else { completionHandler(.cancel); return }
        capturedResponse = response
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        responseData.append(data)
        client?.urlProtocol(self, didLoad: data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard !didRedirect else { inner?.finishTasksAndInvalidate(); return }
        if let error {
            client?.urlProtocol(self, didFailWithError: error)
        } else {
            client?.urlProtocolDidFinishLoading(self)
        }
        BajutsuNet.report(
            request: request, requestBody: capturedRequestBody, response: capturedResponse,
            body: responseData, startedAt: startedAt, error: error
        )
        inner?.finishTasksAndInvalidate()
    }

    // A redirect followed by the forwarding session in `startLoading()` (`.default`) would carry
    // that session's cookies/config rather than the app's own, so it is not auto-followed here.
    // `wasRedirectedTo` hands it back to the URL Loading System, which starts a fresh
    // `canInit`-routed load for the new request under the app's real session — the same path an
    // unintercepted redirect would take. That hand-back does not cancel this instance's inner
    // task, so `didRedirect` mutes the delegate methods above: without it, `client` (already
    // pointed at the new instance) would keep getting stale `didReceive`/`didComplete` calls —
    // and `BajutsuNet.report` a second, racy exchange — from this one. The redirected request
    // also carries the `BajutsuHandled` marker `startLoading()` set on the request the inner
    // task issued, so it is stripped here — left in place, `canInit` would refuse the new load
    // and the redirect target would go unobserved entirely.
    func urlSession(
        _ session: URLSession, task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        didRedirect = true
        var next = request
        if let mutable = (request as NSURLRequest).mutableCopy() as? NSMutableURLRequest {
            URLProtocol.removeProperty(forKey: Self.handledKey, in: mutable)
            next = mutable as URLRequest
        }
        client?.urlProtocol(self, wasRedirectedTo: next, redirectResponse: response)
        completionHandler(nil)
    }

    // MARK: cover app-created sessions

    /// `URLProtocol.registerClass` only affects `shared` / default sessions. Apps that
    /// build their own `URLSessionConfiguration` need our protocol prepended to
    /// `protocolClasses`; swizzle the getter to do that automatically.
    static func installIntoDefaultConfigurations() {
        let cls: AnyClass = URLSessionConfiguration.self
        guard
            let original = class_getInstanceMethod(cls, #selector(getter: URLSessionConfiguration.protocolClasses)),
            let replacement = class_getInstanceMethod(cls, #selector(URLSessionConfiguration.bajutsu_protocolClasses))
        else { return }
        method_exchangeImplementations(original, replacement)
    }
}

extension URLSessionConfiguration {
    @objc fileprivate func bajutsu_protocolClasses() -> [AnyClass]? {
        // After the swizzle this calls the original getter.
        var classes = self.bajutsu_protocolClasses() ?? []
        if !classes.contains(where: { $0 == BajutsuURLProtocol.self }) {
            classes.insert(BajutsuURLProtocol.self, at: 0)
        }
        return classes
    }
}
