# Showcase — the dogfood app suite

**English** · [日本語](README.ja.md)

The showcase is Bajutsu's next-generation dogfood target: **the same app written twice**
(UIKit and SwiftUI) and **each in two accessibility variants** (identifiers on / off) — four
installable products from two codebases. It packs the interaction surface a real app has —
five tabs, navigation-stack pushes, all four modal styles, text entry, async loading,
networking (live + mockable), and a screen that deliberately raises OS-level alerts — into the
smallest app that still exercises `record`, `crawl`, and `run` together.

- **The screens:** a 10-screen inventory (5 tabs + pushes + 3 modals) is in
  [`SPEC.md` §5](SPEC.md#screen-inventory); the tabs are **Stable · Search · Log · Notices · Permissions**.
- **The contract:** [`SPEC.md`](SPEC.md) ([ja](SPEC.ja.md)) — every screen, identifier,
  launch-env hook, deeplink, and the OS-alert placement. The two `-a11y` apps expose an
  identical identifier contract, so the one [`scenarios/`](scenarios) set drives both.
- **The roadmap items:** [BE-0045](../../roadmaps/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md)
  records the rationale for the showcase suite; [BE-0079](../../roadmaps/BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase.md)
  completed the consolidation onto it (see [`roadmaps`](../../roadmaps/README.md) for how the roadmap works).

## The four products

| `targets.<name>` | Toolkit | Accessibility | Demonstrates |
|---|---|---|---|
| `showcase-swiftui` | SwiftUI | on | `run` (id-based), `doctor` → Ready |
| `showcase-uikit` | UIKit | on | same scenarios, the other toolkit |
| `showcase-swiftui-noax` | SwiftUI | off | `record` (ladder fallback), `doctor` → Blocked |
| `showcase-uikit-noax` | UIKit | off | same, the other toolkit |

The variant difference is a single Swift compile flag, `ACCESSIBLE` (SPEC §8); there is no
forked source. The `-noax` builds compile to a tree with **no** identifiers — the app a team
that skipped accessibility ships, made testable.

**Android twins** ([`android/`](android/), SPEC §2.1): the same fixture also exists for Android
ahead of the BE-0007 adb backend — Jetpack Compose mirroring SwiftUI, Android Views mirroring
UIKit, each in the same a11y/noax flavor pair (four more products,
`make -C demos/showcase/android build-all`). Until BE-0007 lands they build but cannot be run
(`--backend android` reports "not implemented yet").

## Build

Needs [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`) and Xcode.

```bash
make -C demos/showcase build-all          # all four products
make -C demos/showcase swiftui-build      # just the SwiftUI a11y product
make -C demos/showcase uikit-noax-build   # just the UIKit no-a11y product
```

Each build lands at `…/build/dd/Build/Products/Debug-iphonesimulator/<Scheme>.app`, exactly
where `showcase.config.yaml`'s `appPath` expects it. Generated `*.xcodeproj` and `build/` are
gitignored.

## Run (on a booted Simulator)

Prereqs: a booted Simulator, `brew install facebook/fb/idb-companion`, `uv sync --extra idb`.

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
bajutsu run --target showcase-swiftui --backend idb --config demos/showcase/showcase.config.yaml
bajutsu run --target showcase-swiftui --scenario demos/showcase/scenarios/modals.yaml \
    --backend idb --config demos/showcase/showcase.config.yaml
```

## What's here

| Path | What |
|---|---|
| [`SPEC.md`](SPEC.md) | the screen-by-screen contract (the spec) |
| [`WEBUI.md`](WEBUI.md) | the Web UI tour — drive a Simulator from the browser, collect every evidence type |
| [`ios/swiftui/`](ios/swiftui), [`ios/uikit/`](ios/uikit) | the two iOS codebases (xcodegen `project.yml`, two targets each) |
| [`ios/scenarios-noax/`](ios/scenarios-noax) | the `-noax` twin of `scenarios/` — the same flows by visible label + on-screen mirror text, run over XCUITest (`--backend ios`), which idb's a11y tree can't reach |
| [`android/`](android/) | the four Android twins (Compose × Views, BE-0007 preparation) |
| [`showcase.config.yaml`](showcase.config.yaml) | the eight iOS + Android `targets.<name>` entries |
| [`scenarios/`](scenarios) | shared id-based `run` scenarios (drive every a11y app, iOS and Android alike) |
| [`record/goals.txt`](record/goals.txt) | natural-language goals for the `record` A/B demo |
| [`crawl/`](crawl/expected-screen-map.md) | the screen map `crawl` ([BE-0038](../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md), in progress) should produce — validation test data |

### Two aligned scenario suites: `scenarios/` (a11y) ↔ `ios/scenarios-noax/` (no-a11y)

The `-noax` apps expose no accessibility identifiers *and* no mirrored a11y values, so the id-based
shared suite cannot drive them. `ios/scenarios-noax/` is the label/visible-text twin: one file per
`scenarios/` flow, same behaviour, but every step addresses elements by their **visible label** and
every assertion reads the on-screen **mirror `Text`** the app also renders (e.g. `Text("Segment:
\(segment)")`, `Text(favorite ? "Favorited" : "Not favorited")`) instead of the dropped a11y value.
It runs over XCUITest (`make -C demos/showcase run-swiftui-noax` / `run-uikit-noax`) because idb
collapses the tab bar and cannot reach a `-noax` element by label. The same suite drives both `-noax`
toolkits: the visible mirror strings are identical (`AppModel` + the `*Controller`/`*View` labels).

Not every `scenarios/` file has a twin, by design:

- **`components.yaml`** — its purpose is `codegen` → a native XCUITest keyed by *id*, which a `-noax`
  build has none of; its flows (filter sheet, gallery, search) are already covered by `modals.yaml`
  and `search.yaml` twins.
- **`visual.yaml` / `golden/`** — pixel/tree baselines are id- and image-specific, a separate concern.

Two twins are **SwiftUI-only** (`tags: [swiftui]`, excluded by `run-uikit-noax`): `gestures_multitouch`
(the `SHOWCASE_GESTURES` pinch/rotate screen exists in SwiftUI + Compose, not UIKit — the same gap the
a11y twin has) and `generated.yaml` (a `record` output captured on `showcase-swiftui-noax`).
`permission.yaml` is kept for 1:1 correspondence but, exactly like its a11y twin, is **non-deterministic
on iOS** (the SpringBoard grant is cleared by the AI alert guard) — not a deterministic gate. These
`-noax` runs are on-device only (not part of `make check`), and label/visible-text selectors are more
brittle than ids, so scroll counts may need on-device tuning per toolkit.

## Deeplinks

Deeplink schemes are per-product (so two installed apps never collide): `showcaseswiftui`,
`showcaseuikit`, plus the `…noax` variants. Because `bajutsu` opens the URL literally, the
shared scenarios use `launchEnv` + taps (scheme-agnostic) rather than deeplinks. A deeplink
selects a tab (it no longer pushes a detail screen — BE-0079); to exercise one directly:

```bash
xcrun simctl openurl booted showcaseswiftui://log
xcrun simctl openurl booted showcaseuikit://permissions
```

## The single iOS fixture

The showcase is Bajutsu's only iOS fixture. BE-0079 retired the older single-variant apps
(`demo`, `sample`, `sample2`) after bringing the showcase to parity and re-pointing every demo
and on-device CI job at it.
