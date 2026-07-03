**English** · [日本語](README.ja.md)

# Showcase — UIKit

The UIKit half of the Bajutsu showcase dogfood suite. The behavior, identifiers,
launch-env hooks, deeplinks, and OS-alert placement are defined once in
[`../SPEC.md`](../SPEC.md) and implemented here identifier-for-identifier (the SwiftUI half
implements the same spec), so a single scenario set drives every variant. The views are
built programmatically (no storyboards) but expose the exact same accessibility identifiers,
launch-env hooks, and deeplinks as the SwiftUI variant.

## Two build variants, one codebase

The variant difference is a single Swift active-compilation condition, `ACCESSIBLE`;
there is no forked source. The `aid(_:)` helper ([`Sources/Accessibility.swift`](Sources/Accessibility.swift),
SPEC §8) — an extension on `UIAccessibilityIdentification` — attaches an identifier, and
the `mirror(value:)` helper mirrors state to `accessibilityValue`, only when the flag is set.

| Target | `ACCESSIBLE` | Bundle id | Display name | Deeplink scheme |
|---|---|---|---|---|
| `BajutsuShowcaseUIKit` | defined | `com.bajutsu.showcase.ios.uikit` | Showcase UIKit | `showcaseuikit` |
| `BajutsuShowcaseUIKitNoAx` | — | `com.bajutsu.showcase.ios.uikit.noax` | Showcase UIKit (no a11y) | `showcaseuikitnoax` |

The `-a11y` build exposes every identifier in SPEC §5 (`doctor --target` grades it **Ready**);
the no-a11y build compiles to a tree with none (graded **Blocked**) — the cost of skipping
accessibility, made concrete.

## Build

Requires Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`).
Both targets share the same `Sources/` directory.

```bash
cd demos/showcase/ios/uikit
xcodegen generate          # -> BajutsuShowcaseUIKit.xcodeproj
xcodebuild -scheme BajutsuShowcaseUIKit \
  -destination 'generic/platform=iOS Simulator' build        # the a11y build
xcodebuild -scheme BajutsuShowcaseUIKitNoAx \
  -destination 'generic/platform=iOS Simulator' build        # the no-a11y twin
```

The generated `.xcodeproj` and any `build/` output are gitignored; `project.yml` is the
source of truth.

## Launch-env hooks & deeplinks

Read once at launch from `ProcessInfo` with the `SHOWCASE_` prefix; deeplinks use the
per-variant scheme above. Both are specified in [`../SPEC.md`](../SPEC.md) §3–§4.
