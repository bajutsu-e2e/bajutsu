**English** · [日本語](ja/showcase.md)

# The showcase suite (Bajutsu's single cross-platform fixture)

> Bajutsu's test fixture lives under [`demos/showcase/`](../demos/showcase). The same screen
> contract is built five times — SwiftUI and UIKit on iOS, Jetpack Compose and Views on Android, and
> one Flutter codebase that targets both platforms — mostly in an accessibility-on / -off pair per
> toolkit (Flutter's iOS build ships accessibility-on only, for now). Together, the five codebases
> register a dozen `targets.<name>` entries in
> [`demos/showcase/showcase.config.yaml`](../demos/showcase/showcase.config.yaml). It packs the
> interaction surface a real app has (five tabs with push navigation, four modal styles shared by
> every codebase plus a fifth, iOS-only native alert, text entry, gestures, async loading, live +
> mockable networking, an OS-alert screen) into the smallest coherent app that still tells that
> whole story.
>
> BE-0079 made it the **single** iOS fixture, retiring the older `demo` / `sample` / `sample2` apps;
> BE-0007 and BE-0008 then grew that iOS fixture into today's cross-platform one, adding the Android
> and Flutter twins. The authoritative, screen-by-screen contract — every identifier, every scenario
> mapping — is [`demos/showcase/SPEC.md`](../demos/showcase/SPEC.md); this page summarizes how to
> reach it.

Related: [scenarios](scenarios.md) · [configuration](configuration.md) · [codegen](codegen.md) · [cli](cli.md)

---

## Why two toolkits × two accessibility variants

The showcase makes visible the two axes on which Bajutsu's design rests:

- **Toolkit axis** (UIKit vs SwiftUI) — the two accessibility-on products (`showcase-swiftui` /
  `showcase-uikit`) expose an *identical* identifier contract, so the shared
  [`demos/showcase/scenarios/`](../demos/showcase/scenarios) runs unchanged against either. What
  differs is the element tree the backend sees, which is exactly what a cross-toolkit driver must absorb.
- **Accessibility axis** (no suffix ↔ `-noax`) — the `-noax` builds carry **no** identifiers
  (`idNamespaces: []`). They are the controlled experiment for selector stability (DESIGN §5): the
  same goal recorded against both shows the value of accessibility work as a concrete diff, and they
  are the `record` / `doctor` "missing accessibility" subjects.

## Build and run

The four core iOS targets keep the original bundle ids
(`com.bajutsu.showcase.ios.{swiftui,uikit}[.noax]`) and deeplink schemes
(`showcase{swiftui,uikit}[noax]`). They build with XcodeGen + xcodebuild (`project.yml` is the
source of truth; `.xcodeproj` / `build/` are gitignored). A fifth iOS target,
`showcase-swiftui-bundled`, runs the same SwiftUI app with no `xcuitest:` sub-config. A Simulator
run for it resolves to the wheel-bundled runner (BE-0292) instead of the locally built one, and
`bajutsu doctor --target showcase-swiftui-bundled` reports which one that is. The remaining seven
targets are the Android twins ([`android/`](../demos/showcase/android), Jetpack Compose and Views,
BE-0007) and the Flutter twins on both platforms ([`flutter/`](../demos/showcase/flutter),
BE-0008). They build with Gradle and the Flutter SDK respectively.

```bash
make -C demos/showcase swiftui-build       # compile the SwiftUI a11y product for the Simulator
make -C demos/showcase run-swiftui         # build → install → bajutsu run (XCUITest) against a booted Simulator
make -C demos/showcase doctor              # the accessibility A/B: a11y grades Ready, -noax Blocked
make -C demos/showcase ui-test             # the codegen path: scenario → XCUITest → xcodebuild test
make -C demos/showcase run-flutter         # the Flutter twin on iOS: build → install → bajutsu run (XCUITest)
make -C demos/showcase run-flutter-android # the Flutter twin on Android: build → install → bajutsu run (adb)
make -C demos/showcase/android e2e-codegen # the Android twin: scenario → UI Automator → connectedAndroidTest (needs a booted emulator, BE-0294)
```

`bajutsu run` / `serve` also build the app on demand via each target's `build` command, so a manual
build is rarely needed.

## Launch-environment hooks

Driven via `launchEnv` and passed as `SIMCTL_CHILD_<NAME>` ([drivers](drivers.md#environment-management-simctl)).
BE-0079 removed the launch-env shortcuts to a *data state* and to a *pushed screen*: the catalog is
fixed (no seed knob), and a deeplink no longer jumps onto a detail — a detail is reached only by
tapping its row. BE-0107 finished the job by retiring `SHOWCASE_TAB`, the last launch-env shortcut to
a screen: the app always launches on the Stable tab, and every other tab is reached by tapping the
native tab bar. The XCUITest backend taps the native tab bar's individual tabs, so the tab-crossing
scenarios run on `--backend ios`. Every screen beyond the launch tab is
reached by driving the UI, and a scenario observes the app's own data rather than relying on an injected data state.

| Variable | Effect |
|---|---|
| `SHOWCASE_UITEST=1` | disable animations (keeps condition waits tight) |
| `SHOWCASE_API_URL` / `SHOWCASE_HTTP_BASE` | base URLs for the catalog GET and the echo POST/DELETE endpoints |

The full identifier catalog, the deeplink grammar, and the primitive-to-scenario mapping are in
[`demos/showcase/SPEC.md`](../demos/showcase/SPEC.md).
