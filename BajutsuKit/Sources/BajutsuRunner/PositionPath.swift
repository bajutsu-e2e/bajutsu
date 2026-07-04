import Foundation

/// A root-relative path of child indices identifying one node in the element tree.
///
/// `[2, 0]` is the root's 3rd child's 1st child. BE-0105 stores this in an element's opaque
/// `backingElement` so a later tap re-derives *that one* element from a single query, rather than
/// re-walking the whole tree or re-resolving a predicate that could match a different element.
public typealias PositionPath = [Int]

/// The attributes recorded for an element at snapshot time, re-checked before actuation.
///
/// Re-deriving by position path alone could land on a different element after a sibling-order change,
/// so these are the guard that keeps BE-0019's "never silently act on whatever now matches" true —
/// upgrading stale detection from the generation-only handle scheme to a same-generation attribute match.
/// `value` is deliberately excluded: a slider or text field's value legitimately changes between the
/// snapshot and the tap, and matching on it would flag a still-valid element as stale.
public struct RecordedAttributes {
    public let identifier: String?
    public let label: String?
    public let traits: [String]
    public let frame: (x: Double, y: Double, width: Double, height: Double)

    public init(
        identifier: String?,
        label: String?,
        traits: [String],
        frame: (x: Double, y: Double, width: Double, height: Double)
    ) {
        self.identifier = identifier
        self.label = label
        self.traits = traits
        self.frame = frame
    }
}

/// The opaque `ElementSnapshot.backingElement` payload (BE-0105): where the element is (its position
/// path) plus what it was (the recorded attributes) so a re-derivation can be verified before acting.
public final class PositionPathBacking {
    public let path: PositionPath
    public let recorded: RecordedAttributes

    public init(path: PositionPath, recorded: RecordedAttributes) {
        self.path = path
        self.recorded = recorded
    }
}

/// One node of an element tree, normalized so the flatten walk needs no XCTest.
///
/// `XcuitestElementProvider` conforms `XCUIElementSnapshot` to this over a single `app.snapshot()`;
/// tests supply a plain in-memory tree. Fields are already normalized (traits mapped, empty strings
/// dropped) so this file stays pure Foundation and testable on the `swift test` gate.
public protocol SnapshotNode {
    var nodeIdentifier: String? { get }
    var nodeLabel: String? { get }
    var nodeValue: String? { get }
    var nodeTraits: [String] { get }
    var nodeFrame: (x: Double, y: Double, width: Double, height: Double) { get }
    var nodeChildren: [SnapshotNode] { get }
}

/// Flatten a snapshot tree into the normalized `ElementSnapshot` list in pre-order, excluding the
/// root — matching BE-0019's `descendants(matching: .any)` shape so the `/elements` contract is
/// unchanged. Each element carries a `PositionPathBacking` with its root-relative index path and the
/// attributes recorded here, so a later tap re-derives exactly this node and verifies it.
public func flattenSnapshot(root: SnapshotNode) -> [ElementSnapshot] {
    var out: [ElementSnapshot] = []
    func appendDescendants(of node: SnapshotNode, at path: PositionPath) {
        for (index, child) in node.nodeChildren.enumerated() {
            let childPath = path + [index]
            let recorded = RecordedAttributes(
                identifier: child.nodeIdentifier,
                label: child.nodeLabel,
                traits: child.nodeTraits,
                frame: child.nodeFrame
            )
            out.append(
                ElementSnapshot(
                    identifier: recorded.identifier,
                    label: recorded.label,
                    value: child.nodeValue,
                    traits: recorded.traits,
                    frame: recorded.frame,
                    backingElement: PositionPathBacking(path: childPath, recorded: recorded)
                )
            )
            appendDescendants(of: child, at: childPath)
        }
    }
    appendDescendants(of: root, at: [])
    return out
}

/// Whether a re-derived element still matches what was recorded at snapshot time.
///
/// Identifier / label / traits must be equal; the frame must agree within `frameTolerance` so
/// sub-pixel jitter is not read as a different element. A `false` means the position path now points
/// at something else, so the caller must return `stale` rather than act on it.
///
/// The 1pt default tolerance absorbs sub-point layout rounding (a settled element re-derives to the
/// same frame within well under a point) while still catching a genuinely different element, whose
/// origin or size differs by far more. The on-device latency validation is the place to retune it if
/// real jitter proves larger.
public func attributesMatch(
    recorded: RecordedAttributes,
    current: RecordedAttributes,
    frameTolerance: Double = 1.0
) -> Bool {
    guard recorded.identifier == current.identifier,
          recorded.label == current.label,
          recorded.traits == current.traits else {
        return false
    }
    let a = recorded.frame
    let b = current.frame
    return abs(a.x - b.x) <= frameTolerance
        && abs(a.y - b.y) <= frameTolerance
        && abs(a.width - b.width) <= frameTolerance
        && abs(a.height - b.height) <= frameTolerance
}
