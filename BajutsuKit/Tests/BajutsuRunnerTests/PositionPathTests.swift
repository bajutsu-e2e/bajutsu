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
        XCTAssertEqual(recorded.traits, ["button"])
        XCTAssertEqual(recorded.frame.width, 3)
    }

    func testEmptyTreeFlattensToNothing() {
        XCTAssertTrue(flattenSnapshot(root: FakeNode()).isEmpty)
    }

    private func attrs(
        id: String? = "id",
        label: String? = "Label",
        traits: [String] = ["button"],
        frame: (Double, Double, Double, Double) = (0, 0, 10, 10)
    ) -> RecordedAttributes {
        RecordedAttributes(
            identifier: id, label: label, traits: traits,
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

    func testAttributesMatchWithinFrameTolerance() {
        XCTAssertTrue(
            attributesMatch(
                recorded: attrs(frame: (0, 0, 10, 10)),
                current: attrs(frame: (0.4, 0.4, 10, 10))
            )
        )
    }

    func testAttributesMismatchBeyondFrameTolerance() {
        XCTAssertFalse(
            attributesMatch(
                recorded: attrs(frame: (0, 0, 10, 10)),
                current: attrs(frame: (5, 0, 10, 10))
            )
        )
    }
}
