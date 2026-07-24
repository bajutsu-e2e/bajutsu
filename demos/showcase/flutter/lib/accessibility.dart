import 'package:flutter/widgets.dart';

/// SPEC §8: the single place identifiers (and state-mirroring values) enter the tree.
///
/// The `ACCESSIBLE` dart-define is the Flutter analog of the iOS `ACCESSIBLE` compilation
/// condition and the Android `BuildConfig.ACCESSIBLE` flavor flag: the a11y build passes
/// `--dart-define=ACCESSIBLE=true`, so [aid] / [aidValue] attach a `Semantics(identifier:)`;
/// the noax build leaves it false, so the tree carries no identifiers and no mirrored values —
/// the honest "we skipped accessibility" app that `record` must cope with and `doctor` flags
/// Blocked. Named to echo the iOS `accessibilityID` / `accessibilityStateValue` helpers and the
/// Compose `aid` / `stateValue` modifiers.
const bool kAccessible = bool.fromEnvironment('ACCESSIBLE');

/// Attach a stable `Semantics(identifier:)` in the a11y build; a no-op otherwise.
///
/// Since Flutter 3.19 the identifier maps straight into the platform accessibility tree the
/// native backends read — `resource-id` on Android (via `setViewIdResourceName`),
/// `accessibilityIdentifier` on iOS (BE-0008). The SPEC §5 dotted ids (e.g. `stable.refresh`)
/// pass through verbatim, so the shared `scenarios/` set drives this app unchanged.
Widget aid(String id, Widget child) =>
    kAccessible ? Semantics(identifier: id, child: child) : child;

/// Attach the identifier and mirror state into the semantics `value` (a11y build only), so an
/// assertion can read the state the way it reads the iOS `accessibilityValue` / Android
/// `content-desc` mirror.
///
/// `excludeSemantics: true` drops the visible child's own label from this node. It matters on
/// Android: Flutter concatenates a node's label and value into one `content-desc` (which adb reads
/// as `value`), so without it a mirror over `Text("ID: 3")` with value `"3"` would surface as
/// `value = "3, ID: 3"`. Excluding the child isolates the mirrored value, matching how the Compose
/// twin sets `content-desc` alone (SPEC §2.1). iOS keeps `accessibilityValue` separate from the
/// label regardless, so this is transparent there. `container: true` gives the node its own slot.
Widget aidValue(String id, String value, Widget child) => kAccessible
    ? Semantics(identifier: id, value: value, container: true, excludeSemantics: true, child: child)
    : child;

/// Attach the identifier and the `selected` trait (the iOS `.isSelected` trait) to the **same**
/// node, so a selector resolving the id also sees the trait rather than landing on a separate inner
/// node — the shape `_TabItem` uses. The default `container: false` merges both annotations onto the
/// child control's own semantics node (keeping its button trait, label, and tap). The `selected`
/// trait is unconditional (present in the `-noax` build too), like the iOS and Compose apps: traits
/// are ordinary accessibility semantics, not the assertion-only ids/values SPEC §8 gates.
Widget aidSelected(String id, bool isSelected, Widget child) => kAccessible
    ? Semantics(identifier: id, selected: isSelected, child: child)
    : Semantics(selected: isSelected, child: child);
