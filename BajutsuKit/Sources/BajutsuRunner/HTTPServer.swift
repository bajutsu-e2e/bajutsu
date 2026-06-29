import Darwin
import Foundation

struct HTTPRequest {
    let method: String
    let path: String
    let body: Data?
}

struct HTTPResponse {
    let statusCode: Int
    let contentType: String
    let body: Data

    static func json(_ statusCode: Int = 200, _ object: [String: Any]) -> HTTPResponse {
        let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data()
        return HTTPResponse(statusCode: statusCode, contentType: "application/json", body: data)
    }

    static func png(_ data: Data) -> HTTPResponse {
        HTTPResponse(statusCode: 200, contentType: "image/png", body: data)
    }

    static func error(_ statusCode: Int, _ message: String) -> HTTPResponse {
        json(statusCode, ["status": "error", "message": message])
    }
}

typealias RequestHandler = (HTTPRequest) -> HTTPResponse

final class HTTPServer {
    private let handler: RequestHandler
    private var listenFD: Int32 = -1
    private let lock = NSLock()
    private let queue = DispatchQueue(label: "bajutsu.runner.http")
    private(set) var port: UInt16 = 0

    init(handler: @escaping RequestHandler) {
        self.handler = handler
    }

    @discardableResult
    func start(port requestedPort: UInt16 = 0) throws -> UInt16 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw ServerError.socketCreationFailed }

        var reuse: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = requestedPort.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let bindResult = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                bind(fd, sockPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else {
            close(fd)
            throw ServerError.bindFailed(errno)
        }

        guard listen(fd, 1) == 0 else {
            close(fd)
            throw ServerError.listenFailed(errno)
        }

        var boundAddr = sockaddr_in()
        var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &boundAddr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                getsockname(fd, sockPtr, &addrLen)
            }
        }
        port = UInt16(bigEndian: boundAddr.sin_port)
        listenFD = fd

        queue.async { [weak self] in self?.acceptLoop() }
        return port
    }

    func stop() {
        let fd = lock.withLock { () -> Int32 in
            let fd = listenFD
            listenFD = -1
            return fd
        }
        guard fd >= 0 else { return }
        close(fd)
    }

    private func acceptLoop() {
        while true {
            let fd = lock.withLock { listenFD }
            guard fd >= 0 else { break }
            var clientAddr = sockaddr_in()
            var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)
            let clientFD = withUnsafeMutablePointer(to: &clientAddr) { ptr in
                ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                    accept(fd, sockPtr, &addrLen)
                }
            }
            guard clientFD >= 0 else { break }
            handleConnection(clientFD)
            close(clientFD)
        }
    }

    private func handleConnection(_ fd: Int32) {
        guard let request = readRequest(fd) else {
            writeResponse(fd, .error(400, "bad request"))
            return
        }
        let response = handler(request)
        writeResponse(fd, response)
    }

    // MARK: - HTTP parsing

    private let maxHeaderSize = 8192
    private let maxBodySize = 65536

    private func readRequest(_ fd: Int32) -> HTTPRequest? {
        var headerBuf = Data()
        var singleByte = [UInt8](repeating: 0, count: 1)

        while headerBuf.count < maxHeaderSize {
            let n = recv(fd, &singleByte, 1, 0)
            if n <= 0 { return nil }
            headerBuf.append(singleByte[0])
            if headerBuf.count >= 4,
               headerBuf.suffix(4) == Data([0x0D, 0x0A, 0x0D, 0x0A]) {
                break
            }
        }

        guard let headerString = String(data: headerBuf, encoding: .utf8) else { return nil }
        let lines = headerString.components(separatedBy: "\r\n")
        guard let requestLine = lines.first else { return nil }
        let parts = requestLine.split(separator: " ", maxSplits: 2)
        guard parts.count >= 2 else { return nil }

        let method = String(parts[0])
        let path = String(parts[1])

        var contentLength = 0
        for line in lines.dropFirst() {
            let lower = line.lowercased()
            if lower.hasPrefix("content-length:") {
                let value = line.dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces)
                contentLength = min(Int(value) ?? 0, maxBodySize)
            }
        }

        var body: Data?
        if contentLength > 0 {
            var bodyBuf = Data(count: contentLength)
            var totalRead = 0
            bodyBuf.withUnsafeMutableBytes { ptr in
                guard let base = ptr.baseAddress else { return }
                while totalRead < contentLength {
                    let n = recv(fd, base + totalRead, contentLength - totalRead, 0)
                    if n <= 0 { break }
                    totalRead += n
                }
            }
            if totalRead == contentLength {
                body = bodyBuf
            }
        }

        return HTTPRequest(method: method, path: path, body: body)
    }

    private func writeResponse(_ fd: Int32, _ response: HTTPResponse) {
        let statusText: String
        switch response.statusCode {
        case 200: statusText = "OK"
        case 400: statusText = "Bad Request"
        case 404: statusText = "Not Found"
        case 500: statusText = "Internal Server Error"
        default: statusText = "Unknown"
        }

        var header = "HTTP/1.1 \(response.statusCode) \(statusText)\r\n"
        header += "Content-Type: \(response.contentType)\r\n"
        header += "Content-Length: \(response.body.count)\r\n"
        header += "Connection: close\r\n"
        header += "\r\n"

        sendAll(fd, Data(header.utf8))
        sendAll(fd, response.body)
    }

    private func sendAll(_ fd: Int32, _ data: Data) {
        data.withUnsafeBytes { ptr in
            guard var base = ptr.baseAddress else { return }
            var remaining = data.count
            while remaining > 0 {
                let n = send(fd, base, remaining, 0)
                if n <= 0 { break }
                base += n
                remaining -= n
            }
        }
    }

    enum ServerError: Error {
        case socketCreationFailed
        case bindFailed(Int32)
        case listenFailed(Int32)
    }
}
