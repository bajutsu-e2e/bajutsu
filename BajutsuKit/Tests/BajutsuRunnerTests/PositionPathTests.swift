import XCTest
@testable import BajutsuRunner

/// A plain in-memory `SnapshotNode` so the flatten walk is tested without XCTest's snapshot types.
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
        value: String? = nil,
        traits: [String] = [],
        frame: (Double, Double, Double, Double) = (0, 0, 0, 0),
        children: [FakeNode] = []
    ) {
        self.nodeIdentifier = id
        self.nodeLabel = label
        self.nodeValue = value
        self.nodeTraits = traits
        self.nodeFrame = (frame.0, frame.1, frame.2, frame.3)
        self.kids = children
    }
}

final class PositionPathTests: XCTestCase {
    private func backing(_ snapshot: ElementSnapshot) -> PositionPathBacking {
        guard let b = snapshot.backingElement as? PositionPathBacking else {
            fatalError("expected PositionPathBacking, got \(snapshot.backingElement)")
        }
        return b
    }

    func testFlattenIsPreOrderWithRootRelativePaths() {
        // root -> [A -> [A1], B]; the root itself is excluded, matching descendants(matching:.any).
        let tree = FakeNode(children: [
            FakeNode(id: "A", children: [FakeNode(id: "A1")]),
            FakeNode(id: "B"),
        ])

        let flat = flattenSnapshot(root: tree)

        XCTAssertEqual(flat.map(\.identifier), ["A", "A1", "B"])
        XCTAssertEqual(flat.map { backing($0).path }, [[0], [0, 0], [1]])
    }

    func testFlattenCopiesNormalizedFieldsAndRecordsThem() {
        let tree = FakeNode(children: [
            FakeNode(id: "id", label: "Label", value: "v", traits: ["button"], frame: (1, 2, 3, 4)),
        ])

        let el = flattenSnapshot(root: tree)[0]

        XCTAssertEqual(el.identifier, "id")
        XCTAssertEqual(el.label, "Label")
        XCTAssertEqual(el.value, "v")
        XCTAssertEqual(el.traits, ["button"])
        XCTAssertEqual(el.frame.x, 1)
        XCTAssertEqual(el.frame.height, 4)

        let recorded = backing(el).recorded
        XCTAssertEqual(recorded.identifier, "id")
        XCTAssertEqual(recorded.label, "Label")
        XCTAssertEqual(recorded.value, "v")
        XCTAssertEqual(recorded.traits, ["button"])
        XCTAssertEqual(recorded.frame.width, 3)
    }

    func testEmptyTreeFlattensToNothing() {
        XCTAssertTrue(flattenSnapshot(root: FakeNode()).isEmpty)
    }

    private func attrs(
        id: String? = "id",
        label: String? = "Label",
        value: String? = nil,
        traits: [String] = ["button"],
        frame: (Double, Double, Double, Double) = (0, 0, 10, 10)
    ) -> RecordedAttributes {
        RecordedAttributes(
            identifier: id, label: label, value: value, traits: traits,
            frame: (frame.0, frame.1, frame.2, frame.3)
        )
    }

    func testAttributesMatchWhenIdentical() {
        XCTAssertTrue(attributesMatch(recorded: attrs(), current: attrs()))
    }

    func testAttributesMismatchOnIdentifier() {
        XCTAssertFalse(attributesMatch(recorded: attrs(id: "a"), current: attrs(id: "b")))
    }

    func testAttributesMismatchOnLabel() {
        XCTAssertFalse(attributesMatch(recorded: attrs(label: "a"), current: attrs(label: "b")))
    }

    func testAttributesMismatchOnTraits() {
        XCTAssertFalse(
            attributesMatch(recorded: attrs(traits: ["button"]), current: attrs(traits: ["cell"]))
        )
    }

    func testAttributesMatchIgnoresFrameShift() {
        // A still-settling layout moves an element between snapshot and tap without changing its
        // identity (BE-0287: a 49pt vertical shift of the same field was read as stale). Frame
        // is not part of the identity match — identifier / label / traits carry it — so a shift alone
        // must not fail the match.
        XCTAssertTrue(
            attributesMatch(
                recorded: attrs(frame: (61, 399, 280, 34)),
                current: attrs(frame: (61, 448, 280, 34))
            )
        )
    }

    func testAttributesMatchIgnoresFrameSizeChange() {
        // Even a size change keeps identity when identifier / label / traits agree — the position path
        // plus those three is what distinguishes a genuinely different element.
        XCTAssertTrue(
            attributesMatch(
                recorded: attrs(frame: (0, 0, 10, 10)),
                current: attrs(frame: (0, 0, 20, 40))
            )
        )
    }

    func testUniqueMatchingIndexReturnsSoleIdentityMatch() {
        XCTAssertEqual(
            uniqueMatchingIndex(
                recorded: attrs(label: "Not Now"),
                candidates: [
                    attrs(label: "Save"),
                    attrs(label: "Not Now"),
                    attrs(label: "Cancel"),
                ]
            ),
            1
        )
    }

    func testUniqueMatchingIndexReturnsNilWithoutAMatch() {
        XCTAssertNil(
            uniqueMatchingIndex(
                recorded: attrs(label: "Not Now"),
                candidates: [attrs(label: "Save"), attrs(label: "Cancel")]
            )
        )
    }

    func testUniqueMatchingIndexRejectsDuplicateIdentityMatches() {
        XCTAssertNil(
            uniqueMatchingIndex(
                recorded: attrs(label: "Not Now"),
                candidates: [attrs(label: "Not Now"), attrs(label: "Not Now")]
            )
        )
    }

    func testResolvableMatchingIndexReturnsSoleIdentityMatch() {
        XCTAssertEqual(
            resolvableMatchingIndex(
                recorded: attrs(label: "Not Now"),
                candidates: [attrs(label: "Save"), attrs(label: "Not Now")]
            ),
            1
        )
    }

    func testResolvableMatchingIndexReturnsNilWithoutAMatch() {
        XCTAssertNil(
            resolvableMatchingIndex(
                recorded: attrs(label: "Not Now"),
                candidates: [attrs(label: "Save"), attrs(label: "Cancel")]
            )
        )
    }

    func testResolvableMatchingIndexTakesTheFirstOfADuplicateRegistration() {
        // The UIAlertController pair: same identity, same frame, so one control registered twice.
        // The recorded frame plays no part — the element may have settled elsewhere since the
        // snapshot (BE-0287), which is one of the ways the position path misses in the first place.
        XCTAssertEqual(
            resolvableMatchingIndex(
                recorded: attrs(label: "OK", frame: (205, 414, 140, 48)),
                candidates: [
                    attrs(label: "Cancel", frame: (57, 463, 140, 48)),
                    attrs(label: "OK", frame: (205, 463, 140, 48)),
                    attrs(label: "OK", frame: (205, 463, 140, 48)),
                ]
            ),
            1
        )
    }

    func testResolvableMatchingIndexRejectsMatchesAtDifferentFrames() {
        // Two controls sharing an identity but standing at two places: still a genuine ambiguity.
        XCTAssertNil(
            resolvableMatchingIndex(
                recorded: attrs(label: "OK", frame: (205, 463, 140, 48)),
                candidates: [
                    attrs(label: "OK", frame: (205, 463, 140, 48)),
                    attrs(label: "OK", frame: (205, 611, 140, 48)),
                ]
            )
        )
    }

    func testResolvableMatchingIndexRejectsMatchesDifferingOnlyInSize() {
        // Every frame field decides, not the origin alone.
        XCTAssertNil(
            resolvableMatchingIndex(
                recorded: attrs(label: "OK", frame: (205, 463, 140, 48)),
                candidates: [
                    attrs(label: "OK", frame: (205, 463, 140, 48)),
                    attrs(label: "OK", frame: (205, 463, 140, 96)),
                ]
            )
        )
    }

    func testResolvableMatchingIndexToleratesASubPointFrameDifference() {
        // The candidates are not read at one instant: each one's frame is its own XCUITest attribute
        // fetch, so a still-settling screen can report one control's two registrations a fraction of
        // a point apart. That must stay one control, not fall back to the stale failure.
        XCTAssertEqual(
            resolvableMatchingIndex(
                recorded: attrs(label: "OK", frame: (205, 463, 140, 48)),
                candidates: [
                    attrs(label: "OK", frame: (205, 463, 140, 48)),
                    attrs(label: "OK", frame: (205.5, 463.25, 140, 48)),
                ]
            ),
            0
        )
    }

    func testResolvableMatchingIndexRejectsMatchesDifferingOnlyInValue() {
        // The host's `_collapse_identical_duplicates` keys on `value` too, so a value-bearing control
        // whose two registrations disagree is a genuine ambiguity on both sides — never a guess here.
        XCTAssertNil(
            resolvableMatchingIndex(
                recorded: attrs(label: "Quantity", value: "2", frame: (205, 463, 140, 48)),
                candidates: [
                    attrs(label: "Quantity", value: "2", frame: (205, 463, 140, 48)),
                    attrs(label: "Quantity", value: "3", frame: (205, 463, 140, 48)),
                ]
            )
        )
    }

    func testResolvableMatchingIndexTakesTheFirstOfAValueBearingDuplicate() {
        // Agreeing on value keeps the duplicate collapsible: the recorded value plays no part, only
        // the candidates' agreement with one another does.
        XCTAssertEqual(
            resolvableMatchingIndex(
                recorded: attrs(label: "Quantity", value: "1", frame: (205, 463, 140, 48)),
                candidates: [
                    attrs(label: "Quantity", value: "2", frame: (205, 463, 140, 48)),
                    attrs(label: "Quantity", value: "2", frame: (205, 463, 140, 48)),
                ]
            ),
            0
        )
    }
}
