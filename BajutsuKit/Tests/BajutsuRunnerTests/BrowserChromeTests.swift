import XCTest
@testable import BajutsuRunner

/// A plain in-memory `SnapshotNode`, so the browser-chrome repair is tested without XCTest's
/// snapshot types. The trees below are the shapes measured off `SFSafariViewController` on
/// iOS 18.6 and iOS 26.5.
private struct FakeNode: SnapshotNode {
    var nodeIdentifier: String?
    var nodeLabel: String?
    var nodeValue: String?
    var nodeTraits: [String]
    var nodeFrame: (x: Double, y: Double, width: Double, height: Double)
    private let kids: [FakeNode]
    var nodeChildren: [SnapshotNode] { kids }

    init(
        id: String? = nil,
        label: String? = nil,
        traits: [String] = ["other"],
        children: [FakeNode] = []
    ) {
        self.nodeIdentifier = id
        self.nodeLabel = label
        self.nodeValue = nil
        self.nodeTraits = traits
        self.nodeFrame = (0, 0, 0, 0)
        self.kids = children
    }
}

private func button(id: String? = nil, label: String) -> FakeNode {
    FakeNode(id: id, label: label, traits: ["button"])
}

/// The iOS 18 top bar: the dismiss control carries no identifier, only the localized label.
private func iOS18Tree() -> FakeNode {
    FakeNode(children: [
        FakeNode(id: "BrowserView?IsPageLoaded=true", children: [
            FakeNode(id: "TopBrowserBar", children: [
                button(label: "Done"),
                button(id: "URL", label: "Address"),
            ]),
        ]),
    ])
}

/// The iOS 26 top bar: the platform identifies the dismiss control itself.
private func iOS26Tree() -> FakeNode {
    FakeNode(children: [
        FakeNode(id: "BrowserView?IsPageLoaded=true", children: [
            FakeNode(id: "TopBrowserBar", children: [
                button(id: "Close", label: "Close"),
                button(id: "URL", label: "Address"),
            ]),
        ]),
    ])
}

final class BrowserChromeTests: XCTestCase {
    private func normalized(_ tree: FakeNode) -> [ElementSnapshot] {
        normalizeBrowserChrome(
            flattenSnapshot(root: tree, in: .safariViewService), root: tree
        )
    }

    private func dismissControls(_ elements: [ElementSnapshot]) -> [ElementSnapshot] {
        elements.filter { $0.identifier == BrowserChrome.dismissIdentifier }
    }

    func testUnidentifiedDismissControlIsReportedUnderThePlatformIdentifier() {
        let elements = normalized(iOS18Tree())

        XCTAssertEqual(dismissControls(elements).count, 1)
        // The localized label is left as the screen actually announces it.
        XCTAssertEqual(dismissControls(elements).first?.label, "Done")
    }

    func testTheSameSelectorAddressesTheDismissControlOnBothVersions() {
        // The point of the repair: one `id: Close` selector, two iOS versions.
        for tree in [iOS18Tree(), iOS26Tree()] {
            XCTAssertEqual(dismissControls(normalized(tree)).count, 1)
        }
    }

    func testAnAlreadyIdentifiedTreeIsPassedThroughUnchanged() {
        let flat = flattenSnapshot(root: iOS26Tree(), in: .safariViewService)

        let out = normalizeBrowserChrome(flat, root: iOS26Tree())

        XCTAssertEqual(out.map(\.identifier), flat.map(\.identifier))
        XCTAssertEqual(out.map(\.label), flat.map(\.label))
    }

    func testTheRepairKeepsTheElementsOwnBackingAndEveryOtherField() {
        // Only the reported identifier moves: actuation still re-derives the control by what the
        // platform says about it, through the untouched backing.
        let tree = iOS18Tree()
        let flat = flattenSnapshot(root: tree, in: .safariViewService)
        let before = flat.first { $0.label == "Done" && $0.traits.contains("button") }

        let after = dismissControls(normalizeBrowserChrome(flat, root: tree)).first

        XCTAssertNotNil(before)
        XCTAssertNotNil(after)
        XCTAssertTrue(after?.backingElement === before?.backingElement)
        XCTAssertEqual(after?.traits, before?.traits)
        XCTAssertEqual(after?.value, before?.value)
        XCTAssertEqual((before?.backingElement as? PositionPathBacking)?.recorded.identifier, nil)
    }

    func testASecondUnidentifiedButtonInTheBarLeavesEverythingAlone() {
        // A later iOS adding another unidentified button there must not get one of them renamed
        // into the control a scenario taps to leave the browser.
        let tree = FakeNode(children: [
            FakeNode(id: "TopBrowserBar", children: [
                button(label: "Done"),
                button(label: "Something New"),
                button(id: "URL", label: "Address"),
            ]),
        ])

        XCTAssertTrue(dismissControls(normalized(tree)).isEmpty)
    }

    func testAnUnidentifiedNonButtonIsNotMistakenForTheDismissControl() {
        let tree = FakeNode(children: [
            FakeNode(id: "TopBrowserBar", children: [
                FakeNode(label: "decoration"),
                button(id: "URL", label: "Address"),
            ]),
        ])

        XCTAssertTrue(dismissControls(normalized(tree)).isEmpty)
    }

    func testATreeWithoutATopBarIsPassedThroughUnchanged() {
        let tree = FakeNode(children: [button(label: "Done")])

        XCTAssertTrue(dismissControls(normalized(tree)).isEmpty)
    }

    // MARK: - containsBrowserViewBoundary (BE-0407 Unit 9)

    func testFindsTheBoundaryNodeOnBothTreeShapes() {
        for tree in [iOS18Tree(), iOS26Tree()] {
            XCTAssertTrue(containsBrowserViewBoundary(in: tree))
        }
    }

    func testReportsAbsentWhenTheAppNeverOpenedABrowser() {
        let tree = FakeNode(children: [
            FakeNode(id: "SomeScreen", children: [button(id: "ok", label: "OK")]),
        ])

        XCTAssertFalse(containsBrowserViewBoundary(in: tree))
    }

    func testMatchesTheBoundaryByPrefixEvenWithQueryStateAppended() {
        // The identifier encodes live state (`?IsPageLoaded=…&WebViewProcessID=…`), so an exact
        // match would never fire — only the prefix is stable across a page load.
        let tree = FakeNode(children: [
            FakeNode(id: "BrowserView?IsPageLoaded=false&WebViewProcessID=1234"),
        ])

        XCTAssertTrue(containsBrowserViewBoundary(in: tree))
    }

    func testAnEmptyTreeReportsNoBoundary() {
        XCTAssertFalse(containsBrowserViewBoundary(in: FakeNode()))
    }
}
