import Foundation
import XCTest

@testable import BajutsuRunner

/// The launch environment and arguments the host forwards through the `.xctestrun`, where
/// `xcodebuild` collapses the `//` of a URL in a plain value; the encoded form must survive intact.
final class ForwardedLaunchTests: XCTestCase {
    private func encoded(_ object: Any) throws -> String {
        let json = try JSONSerialization.data(withJSONObject: object)
        return json.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
    }

    func testEncodedEnvironmentWinsAndKeepsURLsIntact() throws {
        let env = [
            "BAJUTSU_ENCODED_LAUNCH_ENV": try encoded(["SHOWCASE_HTTP_BASE": "https://postman-echo.com"]),
            // The collapsed plain twin an older host also sends; ignored once the encoded form is there.
            "BAJUTSU_LAUNCH_ENV_SHOWCASE_HTTP_BASE": "https:/postman-echo.com",
        ]
        XCTAssertEqual(
            RunnerServer.launchEnvironment(from: env), ["SHOWCASE_HTTP_BASE": "https://postman-echo.com"]
        )
    }

    func testPrefixedEnvironmentIsTheFallback() {
        let env = ["BAJUTSU_LAUNCH_ENV_DEBUG": "1", "BAJUTSU_RUNNER_PORT": "9000"]
        XCTAssertEqual(RunnerServer.launchEnvironment(from: env), ["DEBUG": "1"])
    }

    func testMalformedEncodedEnvironmentFallsBackToPrefixed() {
        let env = ["BAJUTSU_ENCODED_LAUNCH_ENV": "not base64!", "BAJUTSU_LAUNCH_ENV_DEBUG": "1"]
        XCTAssertEqual(RunnerServer.launchEnvironment(from: env), ["DEBUG": "1"])
    }

    func testEncodedArgumentsWinOverPlain() throws {
        let env = [
            "BAJUTSU_ENCODED_LAUNCH_ARGS": try encoded(["-url", "https://example.com/a"]),
            "BAJUTSU_LAUNCH_ARGS": #"["-url", "https:/example.com/a"]"#,
        ]
        XCTAssertEqual(RunnerServer.launchArguments(from: env), ["-url", "https://example.com/a"])
    }

    func testMalformedEncodedArgumentsFallBackToPlain() {
        // An empty value decodes to empty `Data`, not nil, so it has to fail on the JSON parse.
        for bad in ["", "not base64!"] {
            let env = ["BAJUTSU_ENCODED_LAUNCH_ARGS": bad, "BAJUTSU_LAUNCH_ARGS": #"["-AppleLocale", "fr_FR"]"#]
            XCTAssertEqual(RunnerServer.launchArguments(from: env), ["-AppleLocale", "fr_FR"], bad)
        }
    }

    func testPlainArgumentsAreTheFallback() {
        XCTAssertEqual(RunnerServer.launchArguments(from: ["BAJUTSU_LAUNCH_ARGS": #"["-reset"]"#]), ["-reset"])
        XCTAssertEqual(RunnerServer.launchArguments(from: [:]), [])
    }
}
