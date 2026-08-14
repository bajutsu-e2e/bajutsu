import Dispatch
import Foundation

/// A blocking loopback GET, shared by the tests that drive a live `HTTPServer`.
enum HTTPTestClient {
    static func get(port: UInt16, path: String) -> (status: Int?, data: Data?) {
        let sem = DispatchSemaphore(value: 0)
        var status: Int?
        var payload: Data?
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        let session = URLSession(configuration: config)
        let url = URL(string: "http://127.0.0.1:\(port)\(path)")!
        session.dataTask(with: url) { data, response, _ in
            status = (response as? HTTPURLResponse)?.statusCode
            payload = data
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 4)
        return (status, payload)
    }
}
