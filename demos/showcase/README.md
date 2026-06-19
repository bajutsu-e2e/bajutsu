# Showcase — the dogfood app suite

**English** · [日本語](README.ja.md)

The showcase is Bajutsu's next-generation dogfood target: **the same app written twice**
(UIKit and SwiftUI) and **each in two accessibility variants** (identifiers on / off) — four
installable products from two codebases. It packs the interaction surface a real app has —
four tabs, navigation-stack pushes, all four modal styles, text entry, async loading,
networking (live + mockable), and a screen that deliberately raises OS-level alerts — into the
smallest app that still exercises `record`, `crawl`, and `run` together.

- **The contract:** [`SPEC.md`](SPEC.md) ([ja](SPEC.ja.md)) — every screen, identifier,
  launch-env hook, deeplink, and the OS-alert placement. The two `-a11y` apps expose an
  identical identifier contract, so the one [`scenarios/`](scenarios) set drives both.
- **The roadmap item:** the `dogfood-showcase-apps` BE item under
  [`roadmaps`](../../roadmaps/README.md) records the rationale.

## The four products

| `apps.<name>` | Toolkit | Accessibility | Demonstrates |
|---|---|---|---|
| `showcase-swiftui` | SwiftUI | on | `run` (id-based), `doctor` → Ready |
| `showcase-uikit` | UIKit | on | same scenarios, the other toolkit |
| `showcase-swiftui-noax` | SwiftUI | off | `record` (ladder fallback), `doctor` → Blocked |
| `showcase-uikit-noax` | UIKit | off | same, the other toolkit |

The variant difference is a single Swift compile flag, `ACCESSIBLE` (SPEC §8); there is no
forked source. The `-noax` builds compile to a tree with **no** identifiers — the app a team
that skipped accessibility ships, made testable.

## Build

Needs [XcodeGen](https://github.com/yonsm/XcodeGen) (`brew install xcodegen`) and Xcode.

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
bajutsu run --app showcase-swiftui --backend idb --config demos/showcase/showcase.config.yaml
bajutsu run --app showcase-swiftui --scenario demos/showcase/scenarios/modals.yaml \
    --backend idb --config demos/showcase/showcase.config.yaml
```

## What's here

| Path | What |
|---|---|
| [`SPEC.md`](SPEC.md) | the screen-by-screen contract (the spec) |
| [`swiftui/`](swiftui), [`uikit/`](uikit) | the two codebases (xcodegen `project.yml`, two targets each) |
| [`showcase.config.yaml`](showcase.config.yaml) | the four `apps.<name>` entries |
| [`scenarios/`](scenarios) | shared id-based `run` scenarios (drive both a11y apps) |
| [`record/goals.txt`](record/goals.txt) | natural-language goals for the `record` A/B demo |
| [`crawl/`](crawl/expected-screen-map.md) | the screen map a future `crawl` should produce (test data) |

## Deeplinks

Deeplink schemes are per-product (so two installed apps never collide): `showcaseswiftui`,
`showcaseuikit`, plus the `…noax` variants. Because `bajutsu` opens the URL literally, the
shared scenarios use `launchEnv` + taps (scheme-agnostic) rather than deeplinks. To exercise a
deeplink directly:

```bash
xcrun simctl openurl booted showcaseswiftui://horse/2
xcrun simctl openurl booted showcaseuikit://permissions
```

## Relationship to `sample`

The showcase supersedes the older single-app [`sample` fixture](../features/app/README.md);
`sample` stays until the showcase also covers its on-device CI and Web UI tours. See the BE
item for the migration note.
