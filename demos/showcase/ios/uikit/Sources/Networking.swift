import Foundation

/// Thin wrapper over URLSession.shared so every request flows through BajutsuKit's
/// interceptor (network evidence + mocks work without app changes — SPEC §6). Callers
/// map the completion to a `*.status` value.
enum ShowcaseNet {
    enum Status: String {
        case idle, loading, done, error
    }

    /// GET a URL, reporting loading→done/error to `update` on the main thread.
    static func get(_ urlString: String, update: @escaping (Status) -> Void) {
        request("GET", urlString, update: update)
    }

    /// Issue a request with an explicit method and optional JSON body/headers.
    /// One caller deliberately passes an Authorization header + `password` body field
    /// so redaction has something to mask (SPEC §6).
    static func request(
        _ method: String,
        _ urlString: String,
        body: String? = nil,
        headers: [String: String] = [:],
        update: @escaping (Status) -> Void
    ) {
        update(.loading)
        guard let url = URL(string: urlString) else {
            DispatchQueue.main.async { update(.error) }
            return
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        if let body {
            req.httpBody = Data(body.utf8)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        for (key, value) in headers { req.setValue(value, forHTTPHeaderField: key) }

        URLSession.shared.dataTask(with: req) { _, response, error in
            DispatchQueue.main.async {
                if let http = response as? HTTPURLResponse {
                    update((200 ..< 400).contains(http.statusCode) ? .done : .error)
                } else {
                    update(error == nil ? .done : .error)
                }
            }
        }.resume()
    }
}
