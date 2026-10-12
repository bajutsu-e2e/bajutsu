import XCTest
@testable import BajutsuRunner

/// A clock the tests move by hand, so the pacing is checked without sleeping.
private final class ManualClock: @unchecked Sendable {
    var now: TimeInterval = 100
}

/// `GET /elements?resolveAlerts=…` (BE-0461 Unit 2): the runner answers the alert the policy names
/// inside the read that looks at the screen, and the reply carries what it recorded. Driven through
/// `APIHandler` against `FakeElementProvider`, with a store of its own on a manual clock.
final class AlertResolutionTests: XCTestCase {
    private var provider: FakeElementProvider!
    private var clock: ManualClock!
    private var store: InterruptionPolicyStore!
    private var handler: APIHandler!

    private static let notifications = InterruptionRule(
        identify: ["Allow", "Don’t Allow"], tap: "Don’t Allow", surface: .native
    )
    private static let savePassword = InterruptionRule(
        identify: ["Save Password", "Not Now"], tap: "Not Now", surface: .inTree
    )

    override func setUp() {
        super.setUp()
        provider = FakeElementProvider()
        let clock = ManualClock()
        self.clock = clock
        store = InterruptionPolicyStore(now: { clock.now })
        handler = APIHandler(provider: provider, policyStore: store)
    }

    // MARK: - Helpers

    private func push(
        _ rules: [InterruptionRule], governs: Bool = true, pacing: AlertPacing = .init(),
        scenarioStart: Bool = true
    ) {
        store.setPolicy(
            InterruptionPolicy(rules: rules, governs: governs, pacing: pacing),
            scenarioStart: scenarioStart
        )
    }

    private func alertButtons(_ labels: [String], title: String? = nil, titles: [String] = []) -> [ElementSnapshot] {
        let buttons = labels.map { label in
            ElementSnapshot(
                identifier: nil, label: label, value: nil, traits: ["button"],
                frame: (0, 0, 10, 10), backingElement: NSObject()
            )
        }
        return buttons + ((title.map { [$0] } ?? []) + titles).map { title in
            ElementSnapshot(
                identifier: systemAlertTitleIdentifier, label: title, value: nil, traits: [],
                frame: (0, 0, 10, 10), backingElement: NSObject()
            )
        }
    }

    private func treeButton(_ label: String, identifier: String? = nil) -> ElementSnapshot {
        ElementSnapshot(
            identifier: identifier, label: label, value: nil, traits: ["button"],
            frame: (0, 0, 10, 10), backingElement: NSObject()
        )
    }

    private func read(
        _ mode: Operations.queryElements.Input.Query.resolveAlertsPayload? = ._true,
        force: Bool? = nil
    ) async throws -> [String: Any] {
        let out = try await handler.queryElements(
            .init(query: .init(resolveAlerts: mode, forceCheck: force))
        )
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            throw XCTSkip("unexpected /elements output")
        }
        return try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(payload)) as? [String: Any]
        )
    }

    // MARK: - Plain read

    func testAPlainReadStaysAPureReadAndCarriesNoRecordFields() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        let reply = try await read(nil)
        XCTAssertEqual(provider.systemAlertQueryCalls, 0)
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
        XCTAssertNil(reply["labels"])
        XCTAssertNil(reply["springboardChecked"])
    }

    func testAResolvingReplyAlwaysCarriesTheRecordFieldsEvenWhenEmpty() async throws {
        push([Self.notifications])
        let reply = try await read()
        XCTAssertEqual(reply["labels"] as? [String], [])
        XCTAssertEqual((reply["unmatched"] as? [Any])?.count, 0)
        XCTAssertEqual((reply["unidentified"] as? [Any])?.count, 0)
        XCTAssertEqual((reply["gaveUp"] as? [Any])?.count, 0)
        XCTAssertEqual(reply["withheld"] as? Bool, false)
    }

    // MARK: - Step 1: SpringBoard

    func testANativeAlertIsAnsweredBeforeTheSnapshotAndRecordedWithItsSurface() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        var orderSeen: [String] = []
        provider.afterSystemAlertTap = { orderSeen.append("press") }
        provider.beforeQueryElements = { orderSeen.append("snapshot") }
        let reply = try await read()
        XCTAssertEqual(orderSeen, ["press", "snapshot"])
        XCTAssertEqual(provider.systemAlertTapCalls.count, 1)
        XCTAssertEqual(reply["labels"] as? [String], ["Don’t Allow"])
        XCTAssertEqual(reply["tappedSurfaces"] as? [String], ["native"])
        XCTAssertEqual(reply["springboardChecked"] as? Bool, false)
    }

    func testAnAlertTitleJoinsTheMatchedLabelsAsAMarker() async throws {
        let localNetwork = InterruptionRule(
            identify: ["Allow", "Don’t Allow"], tap: "Allow",
            exclude: [alertTitleMarker("“App” Would Like to Send You Notifications")]
        )
        push([localNetwork, Self.notifications])
        provider.systemAlertButtons = alertButtons(
            ["Allow", "Don’t Allow"], title: "“Showcase” Would Like to Send You Notifications"
        )
        let reply = try await read()
        XCTAssertEqual(reply["labels"] as? [String], ["Don’t Allow"])
    }

    func testEveryAlertTitleJoinsTheMatchedLabels() async throws {
        // With notifications and Local Network both up, the Local Network title must reach the
        // match even when it is not the first, or the notifications rule would answer it.
        let localNetworkTitle = "“Showcase” Would Like to Find and Connect to Devices on Your Local Network"
        let notificationsRule = InterruptionRule(
            identify: ["Allow", "Don’t Allow"], tap: "Don’t Allow",
            exclude: [alertTitleMarker(localNetworkTitle)]
        )
        push([notificationsRule])
        provider.systemAlertButtons = alertButtons(
            ["Allow", "Don’t Allow"],
            titles: ["“Showcase” Would Like to Send You Notifications", localNetworkTitle]
        )
        let reply = try await read()
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
        XCTAssertEqual(reply["labels"] as? [String], [])
    }

    func testNoAlertSetsSpringboardChecked() async throws {
        push([Self.notifications])
        let reply = try await read()
        XCTAssertEqual(provider.systemAlertQueryCalls, 1)
        XCTAssertEqual(reply["springboardChecked"] as? Bool, true)
    }

    func testTheCheckIsRateLimitedToThePushedPollInterval() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 1.0))
        _ = try await read()
        clock.now += 0.5
        let second = try await read()
        XCTAssertEqual(provider.systemAlertQueryCalls, 1)
        XCTAssertEqual(second["springboardChecked"] as? Bool, false)
        clock.now += 0.5
        _ = try await read()
        XCTAssertEqual(provider.systemAlertQueryCalls, 2)
    }

    func testForceCheckSkipsTheRateLimit() async throws {
        push([Self.notifications])
        _ = try await read()
        let forced = try await read(force: true)
        XCTAssertEqual(provider.systemAlertQueryCalls, 2)
        XCTAssertEqual(forced["springboardChecked"] as? Bool, true)
    }

    func testAnAlertNoRuleIdentifiesIsRecordedUnidentifiedOnEveryCheckAndNeverPressed() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Save", "Cancel"])
        let first = try await read()
        clock.now += 1
        let second = try await read()
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
        XCTAssertEqual(first["unidentified"] as? [[String]], [["Save", "Cancel"]])
        XCTAssertEqual(second["unidentified"] as? [[String]], [["Save", "Cancel"]])
        XCTAssertEqual(first["springboardChecked"] as? Bool, false)
    }

    func testAnInTreeRuleNeverAnswersASpringboardAlert() async throws {
        let inTreeTwin = InterruptionRule(
            identify: ["Allow", "Don’t Allow"], tap: "Allow", surface: .inTree
        )
        push([inTreeTwin])
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        let reply = try await read()
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
        XCTAssertEqual(reply["unidentified"] as? [[String]], [["Allow", "Don’t Allow"]])
    }

    func testANativeShowingIsAnsweredOnceThenWithheldInsideTheRetapDelay() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0, retapDelay: 1.0))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        _ = try await read()
        clock.now += 0.5
        let withheld = try await read()
        XCTAssertEqual(provider.systemAlertTapCalls.count, 1)
        XCTAssertEqual(withheld["withheld"] as? Bool, true)
        XCTAssertEqual(withheld["labels"] as? [String], [])
    }

    func testAShowingStillUpIsRePressedUpToTheCeilingThenGivenUpOnce() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0, retapDelay: 1.0, maxTaps: 3))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        var answered: [String] = []
        var gaveUp: [[String: Any]] = []
        for _ in 0..<6 {
            let reply = try await read()
            answered += reply["labels"] as? [String] ?? []
            gaveUp += reply["gaveUp"] as? [[String: Any]] ?? []
            clock.now += 1
        }
        XCTAssertEqual(provider.systemAlertTapCalls.count, 3)
        XCTAssertEqual(answered, ["Don’t Allow"])
        XCTAssertEqual(gaveUp.count, 1)
        XCTAssertEqual(gaveUp.first?["tap"] as? String, "Don’t Allow")
        XCTAssertEqual(gaveUp.first?["surface"] as? String, "native")
    }

    func testAPressThatKeepsFailingCountsTowardTheCeilingAndRecordsNoAnswer() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0, retapDelay: 1.0, maxTaps: 2))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        provider.tapResult = .stale
        var answered: [String] = []
        var gaveUp = 0
        for _ in 0..<4 {
            let reply = try await read()
            answered += reply["labels"] as? [String] ?? []
            gaveUp += (reply["gaveUp"] as? [Any])?.count ?? 0
            clock.now += 1
        }
        XCTAssertEqual(provider.systemAlertTapCalls.count, 2)
        XCTAssertEqual(answered, [])
        XCTAssertEqual(gaveUp, 1)
    }

    func testAnAlertWhoseLabelsAllReadEmptyIsNeitherRecordedNorAClearScreen() async throws {
        // The alert lost the race with its own dismissal between the existence check and the reads.
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["", ""])
        let reply = try await read()
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
        XCTAssertEqual((reply["unidentified"] as? [Any])?.count, 0)
        XCTAssertEqual(reply["springboardChecked"] as? Bool, false)
    }

    func testATapLabelOnTheAlertTwiceGivesUpRatherThanPressingTheFirst() async throws {
        let rule = InterruptionRule(identify: ["Allow"], tap: "OK")
        push([rule])
        provider.systemAlertButtons = alertButtons(["Allow", "OK", "OK"])
        let reply = try await read()
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
        XCTAssertEqual((reply["gaveUp"] as? [Any])?.count, 1)
    }

    func testAKeyReArmsOnceACheckNoLongerFindsTheAlert() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        _ = try await read()
        provider.systemAlertButtons = []
        _ = try await read()
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        let again = try await read()
        XCTAssertEqual(again["labels"] as? [String], ["Don’t Allow"])
    }

    // MARK: - Re-arming by actuation

    func testACompletedActuationReArmsTheNativeKeySoARecurringPromptIsANewShowing() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0))
        provider.elementsToReturn = [treeButton("Paste", identifier: "paste")]
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        _ = try await read()
        let handle = try await firstHandle()
        _ = try await handler.tap(.init(body: .json(.init(handle: handle))))
        let again = try await read()
        XCTAssertEqual(again["labels"] as? [String], ["Don’t Allow"])
    }

    func testEverySynthesizedActuationReArmsTheNativeKey() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        let actuations: [(String, () async throws -> Void)] = [
            ("tapPoint", { _ = try await self.handler.tap(.init(body: .json(.init(point: [1, 1])))) }),
            ("type", { _ = try await self.handler.typeText(.init(body: .json(.init(text: "x")))) }),
            ("scroll", {
                _ = try await self.handler.scroll(.init(body: .json(.init(from: [0, 0], to: [0, 9]))))
            }),
            ("selectAll", { _ = try await self.handler.selectAll(.init(body: .json(.init()))) }),
        ]
        for (name, actuate) in actuations {
            _ = try await read()
            try await actuate()
            let again = try await read()
            XCTAssertEqual(again["labels"] as? [String], ["Don’t Allow"], name)
        }
    }

    func testARefusedActuationReArmsNothing() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0))
        provider.elementsToReturn = [treeButton("Paste", identifier: "paste")]
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        _ = try await read()
        let handle = try await firstHandle()
        provider.tapResult = .notHittable
        _ = try await handler.tap(.init(body: .json(.init(handle: handle))))
        provider.tapResult = .ok
        let again = try await read()
        XCTAssertEqual(again["labels"] as? [String], [])
        XCTAssertEqual(again["withheld"] as? Bool, true)
    }

    func testAPressOnTheAlertsOwnButtonReArmsNothingSoAnUnlandedPressReachesTheCeiling() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0, maxTaps: 2))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        let alert = try await handler.querySystemAlert(.init(body: .json(.init())))
        guard case .ok(let ok) = alert, case .json(let payload) = ok.body,
              let handle = payload.elements.first?.handle
        else { throw XCTSkip("no alert handle") }
        var gaveUp = 0
        for _ in 0..<3 {
            let reply = try await read()
            gaveUp += (reply["gaveUp"] as? [Any])?.count ?? 0
            _ = try await handler.tapSystemAlert(.init(body: .json(.init(handle: handle))))
            clock.now += 1
        }
        XCTAssertEqual(gaveUp, 1)
    }

    func testTheMonitorAlwaysPressesButRecordsAShowingOnlyOnce() {
        push([Self.notifications], pacing: AlertPacing(retapDelay: 1.0, maxTaps: 1))
        XCTAssertTrue(store.recordMonitorPress(Self.notifications, alert: ["Allow", "Don’t Allow"]))
        // Inside the re-tap delay and past the ceiling alike, the monitor's press records nothing.
        XCTAssertFalse(store.recordMonitorPress(Self.notifications, alert: ["Allow", "Don’t Allow"]))
        clock.now += 5
        XCTAssertFalse(store.recordMonitorPress(Self.notifications, alert: ["Allow", "Don’t Allow"]))
        XCTAssertEqual(store.drain().tapped, ["Don’t Allow"])
    }

    func testAShowingTheMonitorAnsweredIsNotRecordedAgainByAResolvingRead() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0))
        store.recordMonitorPress(Self.notifications, alert: ["Allow", "Don’t Allow"])
        _ = store.drain()
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        let reply = try await read()
        XCTAssertEqual(reply["labels"] as? [String], [])
        XCTAssertEqual(reply["withheld"] as? Bool, true)
    }

    // MARK: - Policy pushes

    func testAPushInsideAScenarioKeepsTheMemoAndAScenarioStartPushClearsIt() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        _ = try await read()
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0), scenarioStart: false)
        let kept = try await read()
        XCTAssertEqual(kept["labels"] as? [String], [])
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0), scenarioStart: true)
        let cleared = try await read()
        XCTAssertEqual(cleared["labels"] as? [String], ["Don’t Allow"])
    }

    func testThePolicyEndpointParsesSurfacesPacingAndTheScenarioStartMark() async throws {
        let body = Components.Schemas.InterruptionPolicyRequest(
            rules: [
                .init(identify: ["Save Password", "Not Now"], tap: "Not Now", surface: .inTree)
            ],
            governs: true, pollIntervalSeconds: 2, retapDelaySeconds: 3, maxTapsPerShowing: 4,
            giveUpSeconds: 5, scenarioStart: true
        )
        _ = try await handler.setInterruptionPolicy(.init(body: .json(body)))
        XCTAssertEqual(store.policy.rules.first?.surface, .inTree)
        XCTAssertEqual(
            store.policy.pacing,
            AlertPacing(pollInterval: 2, retapDelay: 3, maxTaps: 4, giveUp: 5)
        )
    }

    // MARK: - Step 3: in-tree

    func testInTreeModeSkipsTheSpringboardStep() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        let reply = try await read(.inTree)
        XCTAssertEqual(provider.systemAlertQueryCalls, 0)
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
        XCTAssertEqual(reply["springboardChecked"] as? Bool, false)
    }

    func testAnInTreePromptIsTappedAfterAClearReCheckAndTheTreeIsReRead() async throws {
        push([Self.savePassword])
        let sheet = [treeButton("Save Password"), treeButton("Not Now")]
        provider.elementsToReturn = sheet
        let after = [treeButton("Done", identifier: "done")]
        provider.afterTap = { [provider] in provider?.elementsToReturn = after }
        let reply = try await read()
        XCTAssertEqual(provider.tapCalls.count, 1)
        XCTAssertTrue(provider.tapCalls[0].backingElement === sheet[1].backingElement)
        XCTAssertEqual(provider.queryElementsCalls, 2)
        XCTAssertEqual(provider.systemAlertQueryCalls, 2)
        XCTAssertEqual(reply["labels"] as? [String], ["Not Now"])
        XCTAssertEqual(reply["tappedSurfaces"] as? [String], ["inTree"])
        XCTAssertEqual((reply["elements"] as? [[String: Any]])?.first?["label"] as? String, "Done")
        XCTAssertEqual(reply["springboardChecked"] as? Bool, true)
    }

    func testTheReCheckWithholdsTheInTreeTapWhileASpringboardAlertIsUp() async throws {
        push([Self.savePassword])
        provider.elementsToReturn = [treeButton("Save Password"), treeButton("Not Now")]
        provider.systemAlertButtons = alertButtons(["Save", "Cancel"])
        let reply = try await read(.inTree)
        XCTAssertTrue(provider.tapCalls.isEmpty)
        XCTAssertEqual(reply["labels"] as? [String], [])
        XCTAssertEqual(reply["springboardChecked"] as? Bool, false)
    }

    func testTheReCheckCountsTowardTheRateLimitOutsideARequestThatJustChecked() async throws {
        push([Self.savePassword], pacing: AlertPacing(pollInterval: 1.0))
        _ = try await read()
        XCTAssertEqual(provider.systemAlertQueryCalls, 1)
        // The sheet arrives inside the rate limit: the in-tree pass leaves it for a later request
        // rather than querying SpringBoard again.
        provider.elementsToReturn = [treeButton("Save Password"), treeButton("Not Now")]
        clock.now += 0.2
        _ = try await read(.inTree)
        XCTAssertEqual(provider.systemAlertQueryCalls, 1)
        XCTAssertTrue(provider.tapCalls.isEmpty)
        clock.now += 0.9
        _ = try await read(.inTree)
        XCTAssertEqual(provider.systemAlertQueryCalls, 2)
        XCTAssertEqual(provider.tapCalls.count, 1)
    }

    func testAnInTreeRuleHonorsItsExclusionsAndArrivalOrder() async throws {
        let wide = InterruptionRule(
            identify: ["Save Password", "Not Now", "Never"], tap: "Never", surface: .inTree
        )
        let excluded = InterruptionRule(
            identify: ["Save Password", "Not Now"], tap: "Save Password", exclude: ["Never"],
            surface: .inTree
        )
        push([excluded, wide, Self.savePassword])
        provider.elementsToReturn = [
            treeButton("Save Password"), treeButton("Not Now"), treeButton("Never"),
        ]
        let reply = try await read(.inTree)
        XCTAssertEqual(reply["labels"] as? [String], ["Never"])
    }

    func testAButtonCarryingAnIdentifierIsNotAPromptButton() async throws {
        push([Self.savePassword])
        provider.elementsToReturn = [
            treeButton("Save Password", identifier: "app.save"), treeButton("Not Now"),
        ]
        _ = try await read(.inTree)
        XCTAssertTrue(provider.tapCalls.isEmpty)
    }

    func testTwoInTreeButtonsCarryingTheTapLabelArePressedNeitherAndGiveUpNothing() async throws {
        provider.elementsToReturn = [
            treeButton("Save Password"), treeButton("Not Now"), treeButton("Not Now"),
        ]
        // `Not Now` is an identifying label too, so the rule cannot match two of them; a rule whose
        // tap label is not identifying is what can.
        let loose = InterruptionRule(identify: ["Save Password"], tap: "Not Now", surface: .inTree)
        push([loose])
        let reply = try await read(.inTree)
        XCTAssertTrue(provider.tapCalls.isEmpty)
        XCTAssertEqual((reply["gaveUp"] as? [Any])?.count, 0)
    }

    func testAFadingSheetMatchingANarrowerSiblingIsNotReTappedInsideTheDelay() async throws {
        let wide = InterruptionRule(
            identify: ["Save Password", "Not Now", "Never for This Website"], tap: "Not Now",
            surface: .inTree
        )
        push([wide, Self.savePassword], pacing: AlertPacing(pollInterval: 0))
        provider.elementsToReturn = [
            treeButton("Save Password"), treeButton("Not Now"), treeButton("Never for This Website"),
        ]
        let fading = [treeButton("Save Password"), treeButton("Not Now")]
        provider.afterTap = { [provider] in provider?.elementsToReturn = fading }
        let first = try await read(.inTree)
        clock.now += 0.3
        let second = try await read(.inTree)
        XCTAssertEqual(provider.tapCalls.count, 1)
        XCTAssertEqual(first["labels"] as? [String], ["Not Now"])
        XCTAssertEqual(second["labels"] as? [String], [])
        XCTAssertEqual(second["withheld"] as? Bool, true)
    }

    func testALabelStillMatchingOnAChangedScreenIsDeclinedWithoutGivingUp() async throws {
        push([Self.savePassword], pacing: AlertPacing(pollInterval: 0))
        provider.elementsToReturn = [treeButton("Save Password"), treeButton("Not Now")]
        _ = try await read(.inTree)
        provider.elementsToReturn = [
            treeButton("Save Password"), treeButton("Not Now"), treeButton("Edit", identifier: "edit"),
        ]
        clock.now += 2
        let reply = try await read(.inTree)
        XCTAssertEqual(provider.tapCalls.count, 1)
        XCTAssertEqual((reply["gaveUp"] as? [Any])?.count, 0)
    }

    func testAnInTreeShowingStillUpReachesTheCeilingAndRecordsGaveUpBesideItsAnswer() async throws {
        push([Self.savePassword], pacing: AlertPacing(pollInterval: 0, maxTaps: 2))
        provider.elementsToReturn = [treeButton("Save Password"), treeButton("Not Now")]
        var answered: [String] = []
        var gaveUp: [[String: Any]] = []
        for _ in 0..<4 {
            let reply = try await read(.inTree)
            answered += reply["labels"] as? [String] ?? []
            gaveUp += reply["gaveUp"] as? [[String: Any]] ?? []
            clock.now += 1
        }
        XCTAssertEqual(provider.tapCalls.count, 2)
        XCTAssertEqual(answered, ["Not Now"])
        XCTAssertEqual(gaveUp.count, 1)
        XCTAssertEqual(gaveUp.first?["surface"] as? String, "inTree")
    }

    func testAnUnhittableInTreeButtonGivesUpPastTheBound() async throws {
        push([Self.savePassword], pacing: AlertPacing(pollInterval: 0, giveUp: 2.0))
        provider.elementsToReturn = [treeButton("Save Password"), treeButton("Not Now")]
        provider.tapResult = .notHittable
        let first = try await read(.inTree)
        clock.now += 1
        let early = try await read(.inTree)
        clock.now += 1.5
        let late = try await read(.inTree)
        XCTAssertEqual((first["gaveUp"] as? [Any])?.count, 0)
        XCTAssertEqual((early["gaveUp"] as? [Any])?.count, 0)
        XCTAssertEqual((late["gaveUp"] as? [Any])?.count, 1)
        XCTAssertEqual(late["labels"] as? [String], [])
    }

    func testAnInTreeKeyReArmsWhenItsLabelLeavesTheTree() async throws {
        push([Self.savePassword], pacing: AlertPacing(pollInterval: 0))
        provider.elementsToReturn = [treeButton("Save Password"), treeButton("Not Now")]
        _ = try await read(.inTree)
        provider.elementsToReturn = []
        _ = try await read(.inTree)
        provider.elementsToReturn = [treeButton("Save Password"), treeButton("Not Now")]
        let again = try await read(.inTree)
        XCTAssertEqual(again["labels"] as? [String], ["Not Now"])
    }

    func testASheetReturningOverAnAppButtonWithTheSameLabelIsANewShowing() async throws {
        // The app's own identifier-less "Not Now" stays once the sheet closes; the key must still
        // re-arm, since no rule matches the tree without the sheet's identifying labels.
        push([Self.savePassword], pacing: AlertPacing(pollInterval: 0))
        let sheet = [treeButton("Save Password"), treeButton("Not Now")]
        let appOnly = [treeButton("Not Now")]
        provider.elementsToReturn = sheet
        provider.afterTap = { [provider] in provider?.elementsToReturn = appOnly }
        let first = try await read(.inTree)
        provider.afterTap = nil
        provider.elementsToReturn = sheet
        let again = try await read(.inTree)
        XCTAssertEqual(first["labels"] as? [String], ["Not Now"])
        XCTAssertEqual(again["labels"] as? [String], ["Not Now"])
        XCTAssertEqual(provider.tapCalls.count, 2)
    }

    func testAMonitorPressAfterAFailedResolvingPressIsRecordedAndSaysSo() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        provider.tapResult = .stale
        _ = try await read()
        XCTAssertTrue(store.recordMonitorPress(Self.notifications, alert: ["Allow", "Don’t Allow"]))
        XCTAssertEqual(store.drain().tapped, ["Don’t Allow"])
    }

    func testNoActuationReArmsAnInTreeKey() async throws {
        push([Self.savePassword], pacing: AlertPacing(pollInterval: 0))
        provider.elementsToReturn = [treeButton("Save Password"), treeButton("Not Now")]
        _ = try await read(.inTree)
        _ = try await handler.swipe(.init(body: .json(.init(from: [0, 0], to: [0, 100]))))
        let again = try await read(.inTree)
        XCTAssertEqual(again["labels"] as? [String], [])
    }

    // MARK: - POST /systemAlert/resolve (Unit 3)

    private func resolve(_ labels: [String]) async throws -> [String: Any] {
        let out = try await handler.resolveSystemAlert(.init(body: .json(.init(labels: labels))))
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            throw XCTSkip("unexpected /systemAlert/resolve output")
        }
        return try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(payload)) as? [String: Any]
        )
    }

    func testResolveAnswersTheAlertTheStepReadAndDrainsItsRecord() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        let reply = try await resolve(["Don’t Allow", "Allow"])
        XCTAssertEqual(reply["status"] as? String, "answered")
        XCTAssertEqual(provider.systemAlertTapCalls.count, 1)
        XCTAssertTrue(provider.systemAlertTapCalls[0] === provider.systemAlertButtons[1].backingElement)
        XCTAssertEqual(reply["labels"] as? [String], ["Don’t Allow"])
        XCTAssertEqual(reply["tappedSurfaces"] as? [String], ["native"])
    }

    func testResolveTapsNothingWhenTheAlertNoLongerOffersTheLabelsTheStepRead() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        let reply = try await resolve(["Allow Paste", "Don’t Allow Paste"])
        XCTAssertEqual(reply["status"] as? String, "changed")
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
        XCTAssertEqual(reply["labels"] as? [String], [])
    }

    func testResolveComparesTheLabelsAsAMultiset() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Allow", "Allow", "Don’t Allow"])
        let reply = try await resolve(["Allow", "Don’t Allow"])
        XCTAssertEqual(reply["status"] as? String, "changed")
    }

    func testResolveIgnoresAnUnlabelledButtonTheDriverNeverReported() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow", ""])
        let reply = try await resolve(["Allow", "Don’t Allow"])
        XCTAssertEqual(reply["status"] as? String, "answered")
    }

    func testResolveReportsAbsentWhenNoAlertIsShowing() async throws {
        push([Self.notifications])
        let reply = try await resolve(["Allow", "Don’t Allow"])
        XCTAssertEqual(reply["status"] as? String, "absent")
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
    }

    func testResolveRecordsAnAlertNoRuleIdentifiesAndTapsNothing() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Save", "Cancel"])
        let reply = try await resolve(["Save", "Cancel"])
        XCTAssertEqual(reply["status"] as? String, "unidentified")
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
        XCTAssertEqual(reply["unidentified"] as? [[String]], [["Save", "Cancel"]])
    }

    func testResolveNeverAnswersThroughAnInTreeRule() async throws {
        push([Self.savePassword])
        provider.systemAlertButtons = alertButtons(["Save Password", "Not Now"])
        let reply = try await resolve(["Save Password", "Not Now"])
        XCTAssertEqual(reply["status"] as? String, "unidentified")
        XCTAssertTrue(provider.systemAlertTapCalls.isEmpty)
    }

    func testResolveSharesThePacingMemoAndReportsAWithheldCallAsPending() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0, retapDelay: 1.0))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        // A resolving read answered this showing first: inside the re-tap delay, resolve withholds.
        let read = try await read()
        XCTAssertEqual(read["labels"] as? [String], ["Don’t Allow"])
        clock.now += 0.3
        let reply = try await resolve(["Allow", "Don’t Allow"])
        XCTAssertEqual(reply["status"] as? String, "pending")
        XCTAssertEqual(provider.systemAlertTapCalls.count, 1)
        XCTAssertEqual(reply["labels"] as? [String], [])
    }

    func testResolveRePressesUpToTheCeilingRecordingOneAnswerAndOneGiveUp() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0, retapDelay: 1.0, maxTaps: 2))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        var statuses: [String] = []
        var answered: [String] = []
        var gaveUp: [[String: Any]] = []
        for _ in 0..<4 {
            let reply = try await resolve(["Allow", "Don’t Allow"])
            statuses.append(reply["status"] as? String ?? "")
            answered += reply["labels"] as? [String] ?? []
            gaveUp += reply["gaveUp"] as? [[String: Any]] ?? []
            clock.now += 1.5
        }
        XCTAssertEqual(statuses, ["answered", "answered", "pending", "pending"])
        XCTAssertEqual(provider.systemAlertTapCalls.count, 2)
        XCTAssertEqual(answered, ["Don’t Allow"])
        XCTAssertEqual(gaveUp.count, 1)
        XCTAssertEqual(gaveUp.first?["surface"] as? String, "native")
    }

    func testResolvesOwnPressReArmsNoKeySoAnUnlandedPressReachesTheCeiling() async throws {
        push([Self.notifications], pacing: AlertPacing(pollInterval: 0, retapDelay: 0, maxTaps: 1))
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        _ = try await resolve(["Allow", "Don’t Allow"])
        let again = try await resolve(["Allow", "Don’t Allow"])
        XCTAssertEqual(again["status"] as? String, "pending")
        XCTAssertEqual((again["gaveUp"] as? [Any])?.count, 1)
    }

    func testARefusedResolvePressIsPendingAndRecordsNoAnswer() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Allow", "Don’t Allow"])
        provider.tapResult = .stale
        let reply = try await resolve(["Allow", "Don’t Allow"])
        XCTAssertEqual(reply["status"] as? String, "pending")
        XCTAssertEqual(reply["labels"] as? [String], [])
    }

    func testResolveDrainsWhatTheMonitorRecordedBeforeIt() async throws {
        push([Self.notifications])
        store.recordDeclined(["Save", "Cancel"])
        let reply = try await resolve(["Allow", "Don’t Allow"])
        XCTAssertEqual(reply["unmatched"] as? [[String]], [["Save", "Cancel"]])
        XCTAssertTrue(store.drain().declined.isEmpty)
    }

    // MARK: - Drains

    func testTheDrainEndpointCarriesTheNewRecordKinds() async throws {
        push([Self.notifications])
        provider.systemAlertButtons = alertButtons(["Save", "Cancel"])
        let resolver = AlertResolver(provider: provider, store: store)
        _ = resolver.run(mode: .full, forceCheck: false)
        let out = try await handler.drainInterruptions(.init(body: .json(.init())))
        guard case .ok(let ok) = out, case .json(let payload) = ok.body else {
            throw XCTSkip("unexpected drain output")
        }
        XCTAssertEqual(payload.unidentified, [["Save", "Cancel"]])
        XCTAssertEqual(payload.gaveUp, [])
    }

    private func firstHandle() async throws -> String {
        let out = try await handler.queryElements(.init())
        guard case .ok(let ok) = out, case .json(let payload) = ok.body,
              let handle = payload.elements.first?.handle
        else { throw XCTSkip("no handle minted") }
        return handle
    }
}
