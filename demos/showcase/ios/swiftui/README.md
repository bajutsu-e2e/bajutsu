**English** · [日本語](README.ja.md)

# Showcase — SwiftUI

This is the SwiftUI half of the Bajutsu showcase dogfood suite. [`../SPEC.md`](../SPEC.md) defines
the behavior, identifiers, launch-env hooks, deeplinks, and OS-alert placement once, and this half
implements them identifier-for-identifier (the UIKit half implements the same spec). A single
scenario set therefore drives every variant.

## Two build variants, one codebase

The variants differ by a single Swift active-compilation condition, `ACCESSIBLE`, so the source
is not forked. The `aid(_:)` helper ([`Sources/AID.swift`](Sources/AID.swift), SPEC §8) attaches
an identifier, and mirrors state to `accessibilityValue`, only when the flag is set.

| Target | `ACCESSIBLE` | Bundle id | Display name | Deeplink scheme |
|---|---|---|---|---|
| `BajutsuShowcaseSwiftUI` | defined | `com.bajutsu.showcase.ios.swiftui` | Showcase SwiftUI | `showcaseswiftui` |
| `BajutsuShowcaseSwiftUINoAx` | — | `com.bajutsu.showcase.ios.swiftui.noax` | Showcase SwiftUI (no a11y) | `showcaseswiftuinoax` |

The `-a11y` build exposes every identifier in SPEC §5 (`doctor --target` grades it **Ready**).
The no-a11y build compiles to a tree with none (graded **Blocked**), which makes the cost of
skipping accessibility concrete.

## Build

The build requires Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`).
Both targets share the same `Sources/` directory.

```bash
cd demos/showcase/ios/swiftui
xcodegen generate          # -> BajutsuShowcaseSwiftUI.xcodeproj
xcodebuild -scheme BajutsuShowcaseSwiftUI \
  -destination 'generic/platform=iOS Simulator' build        # the a11y build
xcodebuild -scheme BajutsuShowcaseSwiftUINoAx \
  -destination 'generic/platform=iOS Simulator' build        # the no-a11y twin
```

The generated `.xcodeproj` and any `build/` output are gitignored, and `project.yml` is the
source of truth.

## Launch-env hooks & deeplinks

The app reads the hooks once at launch from `ProcessInfo`, using the `SHOWCASE_` prefix. Deeplinks
use the per-variant scheme above. [`../SPEC.md`](../SPEC.md) §3–§4 specifies both.
