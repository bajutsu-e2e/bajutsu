import XCTest

// BE-0445 Unit 1: which properties of a SpringBoard permission prompt's buttons stay the same when
// the Simulator's language changes. The production query (`querySystemAlertButtons`) reads only
// the label and frame, so it cannot answer that; this probe reads each button's full snapshot.
//
// Opt-in, never part of the `ui-test` lane: it raises real permission prompts, and a prompt already
// answered on that Simulator never shows again. The measurement script
// (roadmaps/BE-0445-system-alert-locale-agnostic-answer/misc/measure.sh) passes
// `TEST_RUNNER_BE0445_PROBE=1` on a freshly created, language-pinned Simulator, and
// `TEST_RUNNER_BE0445_TAP=<ordinal>` to choose which button to tap, so the app-side authorization
// status left behind ties each ordinal to its role without trusting any label.
final class SystemAlertProbeUITests: XCTestCase {
    private let app = XCUIApplication()
    private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")

    override func setUpWithError() throws {
        let env = ProcessInfo.processInfo.environment
        try XCTSkipUnless(env["BE0445_PROBE"] == "1", "BE-0445 probe runs only from its script")
        continueAfterFailure = false
        app.launchEnvironment["SHOWCASE_UITEST"] = "1"
        app.launch()
        app.buttons.matching(NSPredicate(format: "label == %@", "Permissions")).firstMatch.tap()
    }

    func test_notifications() throws {
        try probe(prompt: "notifications", trigger: "perm.requestNotif", result: "perm.notif.value")
    }

    func test_tracking() throws {
        try probe(prompt: "tracking", trigger: "perm.requestTracking", result: "perm.tracking.value")
    }

    func test_paste() throws {
        // Written from this runner process, so the app's read is cross-process and raises the prompt.
        UIPasteboard.general.string = "be0445-probe"
        try probe(prompt: "paste", trigger: "sys.paste", result: "sys.paste.value")
    }

    private func probe(prompt: String, trigger: String, result: String) throws {
        let button = app.buttons[trigger]
        for _ in 0..<6 where !button.isHittable { app.swipeUp() }
        button.tap()
        let alert = springboard.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 15), "no SpringBoard alert for \(prompt)")

        let buttons = springboard.alerts.buttons
        let rows: [[String: Any]] = (0..<buttons.count).map { i in
            let b = buttons.element(boundBy: i)
            let snap = try? b.snapshot()
            return [
                "ordinal": i,
                "identifier": b.identifier,
                "label": b.label,
                "value": (b.value as? String) ?? NSNull(),
                "placeholderValue": b.placeholderValue ?? NSNull(),
                "elementType": b.elementType.rawValue,
                "frame": [b.frame.minX, b.frame.minY, b.frame.width, b.frame.height],
                "isEnabled": b.isEnabled,
                "isSelected": b.isSelected,
                "hasFocus": snap?.hasFocus ?? false,
            ]
        }
        let alertSnap = try alert.snapshot()
        let tap = Int(ProcessInfo.processInfo.environment["BE0445_TAP"] ?? "0") ?? 0
        buttons.element(boundBy: tap).tap()

        let value = app.descendants(matching: .any)[result]
        let settled = NSPredicate(format: "value != %@ AND value != %@", "notDetermined", "")
        _ = XCTWaiter().wait(for: [expectation(for: settled, evaluatedWith: value)], timeout: 10)

        let record: [String: Any] = [
            "prompt": prompt,
            "os": UIDevice.current.systemVersion,
            "language": Locale.preferredLanguages.first ?? "",
            "alert": [
                "identifier": alertSnap.identifier,
                "label": alertSnap.label,
                "frame": [
                    alertSnap.frame.minX, alertSnap.frame.minY, alertSnap.frame.width,
                    alertSnap.frame.height,
                ],
            ],
            "buttons": rows,
            "tapped": tap,
            "resultValue": (value.value as? String) ?? NSNull(),
        ]
        let json = try JSONSerialization.data(withJSONObject: record, options: [.sortedKeys])
        print("BE0445PROBE " + String(decoding: json, as: UTF8.self))
    }
}
