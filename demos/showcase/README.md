# Showcase — the dogfood app suite

**English** · [日本語](README.ja.md)

The showcase is Bajutsu's next-generation dogfood target. It is **the same app written twice**
(UIKit and SwiftUI), **each in two accessibility variants** (identifiers on or off), which yields
four installable products from two codebases. The app packs the interaction surface a real app has:
five tabs, navigation-stack pushes, all five modal styles, text entry, async loading, networking
(live and mockable), and a screen that deliberately raises OS-level alerts. It stays the smallest
app that still exercises `record`, `crawl`, and `run` together.

- **The screens:** a 10-screen inventory (5 tabs, pushes, and 3 modals) is in
  [`SPEC.md` §5](SPEC.md#screen-inventory). The tabs are **Stable · Search · Log · Notices · Permissions**.
- **The contract:** [`SPEC.md`](SPEC.md) ([ja](SPEC.ja.md)) covers every screen, identifier,
  launch-env hook, deeplink, and the OS-alert placement. The two `-a11y` apps expose an
  identical identifier contract, so the one [`scenarios/`](scenarios) set drives both.
- **The roadmap items:** [BE-0045](../../roadmaps/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md)
  records the rationale for the showcase suite, and [BE-0079](../../roadmaps/BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase.md)
  completed the consolidation onto it (see [`roadmaps`](../../roadmaps/README.md) for how the roadmap works).

## The four products

| `targets.<name>` | Toolkit | Accessibility | Demonstrates |
|---|---|---|---|
| `showcase-swiftui` | SwiftUI | on | `run` (id-based), `doctor` → Ready |
| `showcase-uikit` | UIKit | on | same scenarios, the other toolkit |
| `showcase-swiftui-noax` | SwiftUI | off | `record` (ladder fallback), `doctor` → Blocked |
| `showcase-uikit-noax` | UIKit | off | same, the other toolkit |

The variants differ by a single Swift compile flag, `ACCESSIBLE` (SPEC §8), so the source is not
forked. The `-noax` builds compile to a tree with **no** identifiers. They are the app a team that
skipped accessibility ships, made testable.

**Android twins** ([`android/`](android/), SPEC §2.1): the same fixture also exists for Android,
ahead of the BE-0007 adb backend. Jetpack Compose mirrors SwiftUI and Android Views mirror UIKit,
each in the same a11y/noax flavor pair (four more products, built with
`make -C demos/showcase/android build-all`). Until BE-0007 lands, they build but cannot run
(`--backend android` reports "not implemented yet").

**Flutter twin** ([`flutter/`](flutter), [BE-0008](../../roadmaps/BE-0008-flutter-support/BE-0008-flutter-support.md)):
one Dart codebase reproduces the same contract for **both** platforms and adds no backend. A
widget's `Semantics(identifier: …)` surfaces into the OS accessibility tree, so the same
`scenarios/` set drives the twin through XCUITest / adb unchanged (see
[`docs/drivers.md`](../../docs/drivers.md) "Flutter").

One scenario is the exception: the Log tab's native alert (SPEC §2.1, §5.3), which no Flutter
screen implements yet. Its scenario (`scenarios/alert.yaml`) is tagged `alert` and excluded from
`run-flutter` accordingly. The targets are `showcase-flutter` (iOS), `showcase-flutter-android`,
and `showcase-flutter-android-noax`. Run them with
`make -C demos/showcase run-flutter` / `run-flutter-android` (Flutter must be on PATH).

## Build

Building needs [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`) and Xcode.

```bash
make -C demos/showcase build-all          # all four products
make -C demos/showcase swiftui-build      # just the SwiftUI a11y product
make -C demos/showcase uikit-noax-build   # just the UIKit no-a11y product
```

Each build lands at `…/build/dd/Build/Products/Debug-iphonesimulator/<Scheme>.app`, exactly
where `showcase.config.yaml`'s `subject.path` expects it. Generated `*.xcodeproj` and `build/` are
gitignored.

## Run (on a booted Simulator)

Prerequisites: Xcode, a booted Simulator, and the XCUITest runner (`make -C demos/showcase runner-build`).

```bash
# run — the shared id-based scenarios, against either a11y toolkit (same scenarios):
make -C demos/showcase run-swiftui
make -C demos/showcase run-uikit

# doctor — the accessibility A/B: Ready (a11y) vs Blocked (no-a11y):
make -C demos/showcase doctor

# record — AI authoring against the no-a11y app (needs ANTHROPIC_API_KEY):
make -C demos/showcase record
```

Or drive `bajutsu` directly, always passing this suite's config:

```bash
bajutsu run --target showcase-swiftui --backend ios --config demos/showcase/showcase.config.yaml
bajutsu run --target showcase-swiftui --scenario demos/showcase/scenarios/modals.yaml \
    --backend ios --config demos/showcase/showcase.config.yaml
```

## What's here

| Path | What |
|---|---|
| [`SPEC.md`](SPEC.md) | the screen-by-screen contract (the spec) |
| [`WEBUI.md`](WEBUI.md) | the Web UI tour — drive a Simulator from the browser, collect every evidence type |
| [`ios/swiftui/`](ios/swiftui), [`ios/uikit/`](ios/uikit) | the two iOS codebases (xcodegen `project.yml`, two targets each) |
| [`ios/scenarios-noax/`](ios/scenarios-noax) | the `-noax` twin of `scenarios/` — the same flows by visible label + on-screen mirror text, run over XCUITest (`--backend ios`), which reaches them by label (a coordinate backend could not) |
| [`android/`](android/) | the four Android twins (Compose × Views, BE-0007 preparation) |
| [`showcase.config.yaml`](showcase.config.yaml) | the eight iOS + Android `targets.<name>` entries |
| [`scenarios/`](scenarios) | shared id-based `run` scenarios (drive every a11y app, iOS and Android alike) |
| [`browser/`](browser/index.html) | the page `scenarios/browser.yaml` loads in the in-app browser, served by `make -C demos/showcase e2e-browser` |
| [`record/goals.txt`](record/goals.txt) | natural-language goals for the `record` A/B demo |
| [`crawl/`](crawl/expected-screen-map.md) | the screen map `crawl` ([BE-0038](../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md), in progress) should produce — validation test data |

### Two aligned scenario suites: `scenarios/` (a11y) ↔ `ios/scenarios-noax/` (no-a11y)

The `-noax` apps expose no accessibility identifiers *and* no mirrored a11y values, so the id-based
shared suite cannot drive them. `ios/scenarios-noax/` is the label/visible-text twin, with one file
per `scenarios/` flow and the same behavior. Every step addresses elements by their **visible
label**. Every assertion reads the on-screen **mirror `Text`** the app also renders (for example
`Text("Segment: \(segment)")` or `Text(favorite ? "Favorited" : "Not favorited")`) instead of the
dropped a11y value.

The suite runs over XCUITest (`make -C demos/showcase run-swiftui-noax` / `run-uikit-noax`), which
reaches each `-noax` element by its visible label (a coordinate backend could not). The same suite
drives both `-noax` toolkits, because the visible mirror strings are identical (`AppModel` and the
`*Controller`/`*View` labels).

Not every `scenarios/` file has a twin, by design:

- **`components.yaml`** — its purpose is `codegen` → a native XCUITest keyed by *id*, which a `-noax`
  build has none of. Its flows (filter sheet, gallery, search) are already covered by the
  `modals.yaml` and `search.yaml` twins.
- **`visual.yaml` / `golden/`** — pixel/tree baselines are id- and image-specific, a separate concern.
- **`browser.yaml`** — the `SFSafariViewController` flow. The browser renders the page this file
  asserts on. A `-noax` build presents the identical tree, so a twin would test nothing the a11y
  file does not. Its two app-side ids sit behind a lane that serves the page.
- **`text_editing.yaml`** — once text is typed, the Search field drops its placeholder, so a `-noax`
  run cannot re-address it by the visible "Search horses" label for the follow-up `select` / `clear`
  / `delete`. The file is also iOS-only (like `push.yaml`): the Compose / Views trees do not expose an
  editable field's text as its `value`, which `clear` reads and the edits are checked against. All
  four editing actuators stay covered on the SwiftUI and UIKit a11y apps under XCUITest.

Two twins are **SwiftUI-only** (`tags: [swiftui]`, excluded by `run-uikit-noax`):

- `gestures_multitouch`: the `SHOWCASE_GESTURES` pinch/rotate screen exists in SwiftUI and Compose
  but not UIKit, the same gap the a11y twin has.
- `generated.yaml`: a `record` output captured on `showcase-swiftui-noax`.

`permission.yaml` is kept for 1:1 correspondence. Exactly like its a11y twin, it is
**non-deterministic on iOS** (the AI alert guard clears the SpringBoard grant), so the scenario is not a
deterministic gate. The `-noax` runs are on-device only (not part of `make check`). Label and
visible-text selectors are more brittle than ids, so scroll counts may need on-device tuning per toolkit.

## Deeplinks

Deeplink schemes are per-product, so two installed apps never collide: `showcaseswiftui`,
`showcaseuikit`, plus the `…noax` variants. Because `bajutsu` opens the URL literally, the
shared scenarios use `subject.launch.env` + taps (scheme-agnostic) rather than deeplinks. A deeplink
selects a tab and no longer pushes a detail screen (BE-0079). To exercise one directly:

```bash
xcrun simctl openurl booted showcaseswiftui://log
xcrun simctl openurl booted showcaseuikit://permissions
```

## The single iOS fixture

The showcase is Bajutsu's only iOS fixture. BE-0079 retired the older single-variant apps
(`demo`, `sample`, `sample2`) after bringing the showcase to parity and re-pointing every demo
and on-device CI job at it.
