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
/// `value` and `frame` are deliberately excluded from that match: a slider or text field's value, and an
/// element's frame while the UI is still settling, both legitimately change between the snapshot and the
/// tap, so matching on either would flag a still-valid element as stale (frame: BE-0287). `frame` is
/// still recorded here because the flattened `ElementSnapshot` reports it; it is just not part of the
/// identity match `attributesMatch` performs.
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
/// Identity is identifier / label / traits: all three must be equal, or the position path now points
/// at a different element and the caller must return `stale` rather than act on it. Frame is
/// deliberately excluded. It once guarded against a sibling reordered into the same slot, but a
/// snapshot is taken while the UI may still be settling (a screen transition animating, a keyboard
/// pushing content up), so the same element legitimately moves between the snapshot and the tap — a
/// 49pt vertical shift of an unchanged field was read as stale on-device (BE-0287). The position
/// path plus identifier / label / traits already distinguishes a genuinely different element, so
/// matching on frame only turned a still-valid element into a false stale.
public func attributesMatch(
    recorded: RecordedAttributes,
    current: RecordedAttributes
) -> Bool {
    recorded.identifier == current.identifier
        && recorded.label == current.label
        && recorded.traits == current.traits
}
