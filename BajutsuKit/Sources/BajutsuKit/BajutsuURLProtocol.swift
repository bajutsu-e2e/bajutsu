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

    // MARK: URLProtocol

    override class func canInit(with request: URLRequest) -> Bool {
        if URLProtocol.property(forKey: handledKey, in: request) != nil { return false }
        // Never intercept the loopback (the collector + any local stub server).
        if let host = request.url?.host, host == "127.0.0.1" || host == "localhost" { return false }
        return (request.url?.scheme == "http" || request.url?.scheme == "https")
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
        inner = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        innerTask = inner?.dataTask(with: mutable as URLRequest)
        innerTask?.resume()
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
        capturedResponse = response
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        responseData.append(data)
        client?.urlProtocol(self, didLoad: data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
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
