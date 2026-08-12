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
/// tap, so matching on either would flag a still-valid element as stale (frame: BE-0287). Both are still
/// carried here because the flat-query fallback needs them: `resolvableMatchingIndex` compares candidates
/// *against one another* on value and frame to tell one control registered twice from two controls
/// sharing an identity. It is only the recorded-against-live match `attributesMatch` performs that leaves
/// them out.
public struct RecordedAttributes {
    public let identifier: String?
    public let label: String?
    public let value: String?
    public let traits: [String]
    public let frame: (x: Double, y: Double, width: Double, height: Double)

    public init(
        identifier: String?,
        label: String?,
        value: String?,
        traits: [String],
        frame: (x: Double, y: Double, width: Double, height: Double)
    ) {
        self.identifier = identifier
        self.label = label
        self.value = value
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
                value: child.nodeValue,
                traits: child.nodeTraits,
                frame: child.nodeFrame
            )
            out.append(
                ElementSnapshot(
                    identifier: recorded.identifier,
                    label: recorded.label,
                    value: recorded.value,
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

/// Return the index of the sole candidate with the recorded identity.
///
/// Zero matches and multiple matches both return nil: neither case identifies one element safely.
/// Callers can then use a stronger discriminator, such as the recorded position path, or report the
/// element as stale rather than choosing an arbitrary match.
public func uniqueMatchingIndex(
    recorded: RecordedAttributes,
    candidates: [RecordedAttributes]
) -> Int? {
    var match: Int?
    for (index, candidate) in candidates.enumerated()
    where attributesMatch(recorded: recorded, current: candidate) {
        guard match == nil else { return nil }
        match = index
    }
    return match
}

/// Return the index to act on, treating several matches that agree on value and frame as one control.
///
/// `uniqueMatchingIndex` above refuses several matches because identity alone cannot tell two
/// different controls apart. A duplicate *registration* is not two controls: XCUITest reports a
/// standard `UIAlertController` button twice, at one frame, and refusing that pair leaves a button
/// plainly on screen unreachable — the position path cannot replay through an alert's live wrapper
/// nodes either, so the flat query is its only route. Matches that also agree on value and frame
/// therefore resolve to the first of them; any disagreement still returns nil, since two controls
/// reporting different content are the ambiguity a selector must fail on rather than guess at.
///
/// This is the runner-side twin of the host's `_collapse_identical_duplicates`
/// (`bajutsu/drivers/base.py`), which collapses the same artifact in an `/elements` reply, and the two
/// deliberately key on the same fields — identifier, label, traits, value, frame. Keep them in step:
/// this path is reached by `liveElement(for:)` re-resolving a *recorded* handle at actuation time, a
/// candidate set no `/elements` reply gated, so a field the host treats as distinguishing has to
/// distinguish here too or the runner guesses where the host fails loudly.
///
/// Value and frame decide here even though `attributesMatch` ignores both, and the two are consistent:
/// that function compares what was *recorded* against what is *live now*, across a gap in which a
/// settling screen legitimately moves an element (BE-0287) and a slider's value legitimately changes,
/// while these candidates are all read from one live query and compared only against one another.
public func resolvableMatchingIndex(
    recorded: RecordedAttributes,
    candidates: [RecordedAttributes]
) -> Int? {
    let matched = candidates.enumerated().filter {
        attributesMatch(recorded: recorded, current: $0.element)
    }
    guard let first = matched.first else { return nil }
    guard
        matched.allSatisfy({
            $0.element.value == first.element.value
                && framesEqual($0.element.frame, first.element.frame)
        })
    else { return nil }
    return first.offset
}

/// Whether two recorded frames describe one place on screen, within a point.
///
/// Not exact equality, because the two sides are not read at one instant: `uniquelyIdentifiedElement`
/// builds its candidates with `candidates.map(recordedAttributes)`, and each candidate's `frame` is its
/// own XCUITest attribute fetch, so a still-settling screen (BE-0287 measured a 49pt shift) can report
/// one control's two registrations a fraction of a point apart. A point of slack keeps the group rule
/// from collapsing back into the stale failure it exists to remove, while two controls standing at two
/// places on screen stay a decisive point apart.
private func framesEqual(
    _ a: (x: Double, y: Double, width: Double, height: Double),
    _ b: (x: Double, y: Double, width: Double, height: Double)
) -> Bool {
    abs(a.x - b.x) <= 1 && abs(a.y - b.y) <= 1
        && abs(a.width - b.width) <= 1 && abs(a.height - b.height) <= 1
}
