**English** · [日本語](../ja/getting-started/ios.md)

# Getting started — iOS track

> Finishes the [Getting started](index.md) loop on an **iOS Simulator** via the XCUITest [backend](../glossary.md#driver-backend-actuator-platform)
> (the sole iOS backend since [BE-0290](../../roadmaps/BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) retired idb). Needs
> macOS + Xcode. On a machine without a Mac, follow the [web track](web.md) instead — the same
> loop against a browser, no Xcode or Simulator required.

Related: [Getting started](index.md) · [web track](web.md) · [showcase](../showcase.md) · [drivers](../drivers.md)

Complete [Steps 1–3 of the shared walkthrough](index.md) first (install, unit tests, read a
scenario) — they need nothing Mac-specific. This page picks up at Step 4.

---

## What you'll need

| For… | You need |
|---|---|
| Steps 1–3 (shared) | macOS or Linux, Python 3.13 (managed via [uv](https://github.com/astral-sh/uv)) |
| Steps 4–5 below | macOS with **Xcode** (the iOS Simulator + `xcodebuild`, which the XCUITest backend drives) and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (to build the showcase). No extra `brew` install or pip extra — the XCUITest backend needs only Xcode |

## Step 4 — Build the showcase app

The repo ships the showcase fixture — the same app in SwiftUI and UIKit, each in an
accessibility-on / -off variant — instrumented for every Bajutsu primitive. Build the SwiftUI
accessibility product for the Simulator:

```bash
make -C demos/showcase swiftui-build         # xcodegen generate -> xcodebuild for the iOS Simulator
```

This produces `BajutsuShowcaseSwiftUI.app` under `demos/showcase/ios/swiftui/build/…`. (The `.xcodeproj`
and `build/` are gitignored — `project.yml` is the source of truth.) See [showcase](../showcase.md) for
the launch-env hooks and the identifier catalog.

## Step 5 — Run a scenario on a Simulator

Boot a Simulator:

```bash
xcrun simctl boot "iPhone 15"                 # or boot one from Xcode > Open Developer Tool > Simulator
```

The XCUITest backend drives the app through a **prebuilt on-device runner** (the target's
`xcuitest.testRunner`). The showcase config wires that runner and builds it for you as part of the
one-shot `make` target below (`make runner-build`), so there is nothing extra to install — Xcode
alone is enough.

The one-shot path is the `make` target, which builds the runner, installs the freshly built app,
and runs the smoke scenario plus a `doctor` check on the booted device:

```bash
make -C demos/showcase run-swiftui
```

Or drive the CLI directly (the same steps, written out):

```bash
uv run bajutsu run --scenario demos/showcase/scenarios/smoke.yaml --target showcase-swiftui --backend ios --udid booted --no-erase
```

What the flags mean:

- `--target showcase-swiftui` selects `targets.showcase-swiftui` from [`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml)
  (bundle id, launch env, allowed id namespaces). The tool itself is app-agnostic; all per-target
  differences live in config ([configuration](../configuration.md)).
- `--backend ios` picks the iOS actuator (XCUITest; `--backend xcuitest` names it explicitly);
  `--udid booted` targets the currently booted Simulator.
- `--no-erase` keeps the already-installed app instead of `simctl erase`-ing first.

On success you'll see a line like:

```
PASS  runs/20260610-120000/manifest.json
```

`run` **exits 0 when every scenario passes, 1 on any failure**, and that exit code is the CI (continuous integration) gate
([run-loop](../run-loop.md)).

> Hit an environment problem (no booted Simulator, Xcode command-line tools missing)? Run
> `uv run bajutsu doctor --target showcase-swiftui` first — it prints a ✓/✗ checklist of the required CLIs and
> a booted device, then scores how well the current screen follows the identifier convention
> ([configuration](../configuration.md#doctor-the-convention-score)).

Continue to [Step 6 — Read the report](index.md#step-6--read-the-report) in the shared walkthrough.

## Author with AI (iOS)

Let Claude explore the showcase app toward a goal and write the scenario for you (Tier 1). Put
`ANTHROPIC_API_KEY=sk-ant-…` in a `.env` file, then:

```bash
uv run bajutsu record --target showcase-swiftui --goal "log in and increment the counter to 3"   # writes into the app's scenarios dir
```

## Emit a native XCUITest

```bash
uv run bajutsu codegen demos/showcase/scenarios/smoke.yaml --target showcase-swiftui -o UITests/Smoke.swift
```

Run it end-to-end with `make -C demos/showcase ui-test`. The structural mapping: [codegen](../codegen.md).
