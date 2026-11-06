**English** · [日本語](README.ja.md)

# Showcase — UIKit

This is the UIKit half of the Bajutsu showcase dogfood suite. [`../SPEC.md`](../SPEC.md) defines
the behavior, identifiers, launch-env hooks, deeplinks, and OS-alert placement once, and this half
implements them identifier-for-identifier (the SwiftUI half implements the same spec). A single
scenario set therefore drives every variant. The views are built programmatically (no
storyboards) but expose the exact same accessibility identifiers, launch-env hooks, and deeplinks
as the SwiftUI variant.

## Two build variants, one codebase

The variants differ by a single Swift active-compilation condition, `ACCESSIBLE`, so the source
is not forked. The `aid(_:)` helper ([`Sources/Accessibility.swift`](Sources/Accessibility.swift),
SPEC §8), an extension on `UIAccessibilityIdentification`, attaches an identifier. The
`mirror(value:)` helper mirrors state to `accessibilityValue`. Both act only when the flag is set.

| Target | `ACCESSIBLE` | Bundle id | Display name | Deeplink scheme |
|---|---|---|---|---|
| `BajutsuShowcaseUIKit` | defined | `com.bajutsu.showcase.ios.uikit` | Showcase UIKit | `showcaseuikit` |
| `BajutsuShowcaseUIKitNoAx` | — | `com.bajutsu.showcase.ios.uikit.noax` | Showcase UIKit (no a11y) | `showcaseuikitnoax` |

The `-a11y` build exposes every identifier in SPEC §5 (`doctor --target` grades it **Ready**).
The no-a11y build compiles to a tree with none (graded **Blocked**), which makes the cost of
skipping accessibility concrete.

## Build

The build requires Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`).
Both targets share the same `Sources/` directory.

```bash
cd demos/showcase/ios/uikit
xcodegen generate          # -> BajutsuShowcaseUIKit.xcodeproj
xcodebuild -scheme BajutsuShowcaseUIKit \
  -destination 'generic/platform=iOS Simulator' build        # the a11y build
xcodebuild -scheme BajutsuShowcaseUIKitNoAx \
  -destination 'generic/platform=iOS Simulator' build        # the no-a11y twin
```

The generated `.xcodeproj` and any `build/` output are gitignored, and `project.yml` is the
source of truth.

## Launch-env hooks & deeplinks

The app reads the hooks once at launch from `ProcessInfo`, using the `SHOWCASE_` prefix. Deeplinks
use the per-variant scheme above. [`../SPEC.md`](../SPEC.md) §3–§4 specifies both.
