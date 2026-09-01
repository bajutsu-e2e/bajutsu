import Foundation

/// The identifiers `SFSafariViewController`'s own chrome carries, and the one repair that makes a
/// scenario written against it run unchanged on every iOS version (BE-0396).
///
/// Measured on iOS 18.6 and iOS 26.5, the browser reports the same identifier for every control it
/// shares between the two — `URL`, `BackButton`, `ShareButton`, `ReloadButton`, `OpenInSafariButton`,
/// `PageFormatMenuButton` — so a selector on any of those already travels. Two differences are left,
/// and only one of them is repairable:
///
/// - **The dismiss control.** iOS 26 identifies it `Close`; iOS 18 gives it no identifier at all,
///   leaving only the label `Done`. `normalizeBrowserChrome` gives the iOS 18 control the identifier
///   iOS 26 already uses, so `id: Close` addresses it on both. Adopting the platform's own name
///   rather than inventing one follows `OS_BACK_BUTTON` (`bajutsu/common/drivers/base.py`), where the iOS
///   convention `BackButton` is likewise the cross-platform vocabulary.
/// - **The forward button.** iOS 18 has a (disabled) `ForwardButton`; iOS 26 has no such control.
///   Nothing can be normalized here — a control that does not exist cannot be reported — so a
///   scenario must not depend on it.
///
/// The *label* is deliberately left alone. It is the localized string a screen reader announces, so
/// rewriting `Done` to `Close` would make the tree, and the evidence captured from it, disagree with
/// what is on screen — and would still not travel, since a Japanese-locale device reports neither.
public enum BrowserChrome {
    /// The browser's root view. Prefix-matched: the identifier encodes live state, as in
    /// `BrowserView?IsPageLoaded=true&WebViewProcessID=8840`.
    public static let browserViewIDPrefix = "BrowserView"
    /// The bar holding the dismiss control and the address field.
    public static let topBarIdentifier = "TopBrowserBar"
    /// The dismiss control's identifier — iOS 26's own, adopted as the cross-version one.
    public static let dismissIdentifier = "Close"
}

/// Report the browser's dismiss control under `BrowserChrome.dismissIdentifier` on the iOS versions
/// that leave it unidentified, so one selector addresses it everywhere.
///
/// Only the reported identity changes. The element's `backingElement` is passed through untouched,
/// so the runner still re-derives and actuates the control by what the platform actually says about
/// it — the recorded attributes and the position path both stay native.
///
/// - Parameters:
///   - elements: the flattened browser tree, as `flattenSnapshot` produced it from `root`.
///   - root: the same tree, unflattened — the dismiss control is recognized by where it sits, not by
///     its label, because the label is localized and the identifier is what is missing.
public func normalizeBrowserChrome(
    _ elements: [ElementSnapshot], root: SnapshotNode
) -> [ElementSnapshot] {
    guard let dismissPath = unidentifiedDismissPath(in: root) else { return elements }
    return elements.map { element in
        guard let backing = element.backingElement as? PositionPathBacking,
              backing.path == dismissPath else { return element }
        return ElementSnapshot(
            identifier: BrowserChrome.dismissIdentifier,
            label: element.label,
            value: element.value,
            traits: element.traits,
            frame: element.frame,
            backingElement: element.backingElement
        )
    }
}

/// The path of the top bar's sole unidentified button, or nil when there is nothing to repair.
///
/// Structural, not textual: the dismiss control is the one button directly under the top bar that
/// carries no identifier of its own (the address field beside it carries `URL`). Nil when the
/// platform already identifies it, and nil when more than one candidate sits there — a later iOS
/// adding a second unidentified button to that bar must not have one of them silently renamed into
/// the control a scenario taps to leave the browser.
private func unidentifiedDismissPath(in root: SnapshotNode) -> PositionPath? {
    guard let (bar, barPath) = firstNode(in: root, at: [], where: {
        $0.nodeIdentifier == BrowserChrome.topBarIdentifier
    }) else { return nil }
    let children = Array(bar.nodeChildren.enumerated())
    guard !children.contains(where: { $0.element.nodeIdentifier == BrowserChrome.dismissIdentifier })
    else { return nil }
    let unidentifiedButtons = children.filter {
        $0.element.nodeIdentifier == nil && $0.element.nodeTraits.contains("button")
    }
    guard unidentifiedButtons.count == 1, let dismiss = unidentifiedButtons.first else { return nil }
    return barPath + [dismiss.offset]
}

/// Pre-order search returning the first matching node with its root-relative position path — the
/// same path `flattenSnapshot` records, so the two can be matched up.
private func firstNode(
    in node: SnapshotNode, at path: PositionPath, where matches: (SnapshotNode) -> Bool
) -> (node: SnapshotNode, path: PositionPath)? {
    for (index, child) in node.nodeChildren.enumerated() {
        let childPath = path + [index]
        if matches(child) { return (child, childPath) }
        if let found = firstNode(in: child, at: childPath, where: matches) { return found }
    }
    return nil
}
