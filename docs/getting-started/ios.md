**English** · [日本語](../ja/getting-started/ios.md)

# Getting started — run a scenario on the iOS Simulator

> This page finishes the [Getting started](index.md) loop on the **iOS Simulator**. You build the
> showcase app, run one scenario against it, and confirm that the run passed. Bajutsu drives the
> Simulator through the XCUITest [backend](../glossary.md#driver-backend-actuator-platform), the sole
> iOS backend since [BE-0290](../../roadmaps/BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)
> retired idb. On a machine without a Mac, follow the [web track](web.md) instead. It runs the same
> loop against a browser.

Related: [Getting started](index.md) · [web track](web.md) · [showcase](../showcase.md) · [drivers](../drivers.md)

---

## Prerequisites

This page covers Steps 4 and 5 of the shared walkthrough. Before you start, check every row below.

| Requirement | Detail |
|---|---|
| Steps 1–3 of the [shared walkthrough](index.md) | Install, run the unit suite, and read a scenario. Those steps need nothing specific to macOS. |
| macOS with **Xcode** | Xcode provides the iOS Simulator and `xcodebuild`, which the XCUITest backend drives. |
| [XcodeGen](https://github.com/yonaskolb/XcodeGen) | The showcase build generates its Xcode project with XcodeGen. |

The XCUITest backend needs no extra `brew` package and no pip extra. Xcode alone is enough.

## Step 4 — Build the showcase app and its runner

The showcase app is the fixture this walkthrough runs against. The repository ships one app written
twice, in SwiftUI and in UIKit. Each version comes with and without accessibility identifiers. The
app exposes every action and assertion Bajutsu supports. Build the SwiftUI version with identifiers
for the Simulator:

```bash
make -C demos/showcase swiftui-build         # xcodegen generate -> xcodebuild for the iOS Simulator
```

The build succeeded when the app bundle exists at this path:

```bash
ls -d demos/showcase/ios/swiftui/build/dd/Build/Products/Debug-iphonesimulator/BajutsuShowcaseSwiftUI.app
```

Git ignores the generated `.xcodeproj` and `build/`. The checked-in `project.yml` defines the
project. The [showcase](../showcase.md) page lists the launch environment variables and the
identifier catalog.

The XCUITest backend drives the app through a **prebuilt on-device runner**. The target's
`driver.runner.testRunner` names that runner. Build the runner once:

```bash
make -C demos/showcase runner-build
```

The build succeeded when the runner's test-run file exists:

```bash
ls BajutsuKit/Runner/build/dd/Build/Products/BajutsuRunner.xctestrun
```

## Step 5 — Run a scenario on the Simulator

Step 5 boots a Simulator, runs the smoke scenario on it, and reads the verdict.

### Boot a Simulator

Boot one Simulator from the command line:

```bash
xcrun simctl boot "iPhone 15"                 # or boot one from Xcode > Open Developer Tool > Simulator
```

The Simulator is ready when this command lists it with `(Booted)`:

```bash
xcrun simctl list devices booted
```

### Run the smoke scenario

Run the smoke scenario on the booted Simulator:

```bash
uv run bajutsu run --scenario demos/showcase/scenarios/smoke.yaml --target showcase-swiftui --backend ios --udid booted --no-erase --config demos/showcase/showcase.config.yaml
```

The flags mean the following:

- `--config demos/showcase/showcase.config.yaml` reads the showcase config. Without the flag, `run`
  reads `bajutsu.config.yaml` in the current directory. At the repository root, that file defines
  the docs-site target alone.
- `--target showcase-swiftui` selects `targets.showcase-swiftui` from
  [`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml). That entry
  holds the app's bundle id, launch environment, and allowed id namespaces. The tool stays the same
  for every app, and the per-app differences live in config ([configuration](../configuration.md)).
- `--backend ios` checks the backend. The target's `platform: ios` already selects the XCUITest
  actuator. `--backend xcuitest` names that actuator explicitly. A value that disagrees with
  the target makes the run exit with code 2.
- `--udid booted` runs on the Simulator you booted above.
- `--no-erase` keeps the installed app instead of running `simctl erase` first.

### Check the result

The run passed when its last line starts with `PASS`:

```
PASS  runs/20260610-120000/manifest.json
```

The path after `PASS` is the run's manifest. The command exits with code 0 when every scenario
passes and with code 1 when any scenario fails. The CI (continuous integration) gate reads that exit
code ([run-loop](../run-loop.md)).

A line that starts with `FAIL` names the same manifest. The cause may be the environment, such as
no booted Simulator or missing Xcode command-line tools. Run `doctor` to check the environment:

```bash
uv run bajutsu doctor --target showcase-swiftui --config demos/showcase/showcase.config.yaml
```

`doctor` prints a ✓/✗ checklist of the required command-line tools and the booted device. It then
scores how well the current screen follows the identifier convention
([configuration](../configuration.md#doctor-the-convention-score)).

Continue to [Step 6 — Read the report](index.md#step-6--read-the-report) in the shared walkthrough.

## More on iOS

The sections below build on the run above. None of them is part of the shared walkthrough.

### Run the whole showcase suite

One `make` target runs the whole showcase suite for the SwiftUI app. It builds the app and the
runner and installs the fresh build. It then runs the suite on the first booted Simulator:

```bash
make -C demos/showcase run-swiftui
```

The target skips the scenarios that need another platform or a dedicated build. Its recipe passes
their tags to `run`:

```bash
--exclude xcuitest,systemalert,visual,android,browser,app,multi-target
```

The `run-swiftui` recipe in [`demos/showcase/Makefile`](../../demos/showcase/Makefile) holds that
list.

### Author a scenario with AI

Claude can explore the showcase app toward a goal and write the scenario for you. Authoring with AI belongs to
[Tier 1](../glossary.md#the-two-tiers), which never decides pass or fail. Put
`ANTHROPIC_API_KEY=sk-ant-…` in a `.env` file, then run:

```bash
uv run bajutsu record --target showcase-swiftui --goal "log in and increment the counter to 3" --config demos/showcase/showcase.config.yaml   # writes into the app's scenarios dir
```

### Emit a native XCUITest

`codegen` turns a scenario into a native XCUITest source file:

```bash
uv run bajutsu codegen demos/showcase/scenarios/smoke.yaml --target showcase-swiftui -o UITests/Smoke.swift --config demos/showcase/showcase.config.yaml
```

To see codegen compile for real, run `make -C demos/showcase ui-test`. That target uses a different
scenario, `components.yaml`. It regenerates the repository's checked-in test file from that
scenario. It then builds and runs the test file with `xcodebuild test`. It does not run the `Smoke.swift` file
generated above. The [codegen](../codegen.md) page describes how each step maps to XCUITest code.
