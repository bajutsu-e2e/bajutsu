**English** · [日本語](BE-0294-codegen-uiautomator-real-compile-ja.md)

# BE-0294 — Real-compile verification for the UI Automator (Kotlin) codegen target

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0294](BE-0294-codegen-uiautomator-real-compile.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0294") |
| Implementing PR | [#1282](https://github.com/bajutsu-e2e/bajutsu/pull/1282) |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

`bajutsu codegen --emit uiautomator` turns a scenario into a Kotlin UI Automator test. No workflow,
Makefile target, or Gradle build ever compiles the generated file. Every assertion in
`tests/test_codegen_uiautomator.py` checks the emitted source as a string. A Kotlin syntax error, or a
call that no longer matches the real `androidx.test.uiautomator` API, would pass the whole suite.

UI Automator is the weakest of the three codegen targets. XCUITest has a gating `xcodebuild test`
job, and Playwright has a comparable real-compile check proposed separately. UI Automator has no
real-compile check of any kind, gating or not. This item adds one, reusing the emulator and Gradle
toolchain that `android-e2e.yml` already sets up for the conformance suite.

## Motivation

The emitter's unit tests confirm that the right Kotlin call comes out for a given step
(`By.res(...)`, `device.findObject(...).click()`, and so on). That is real coverage of the mapping
logic, but it does not cover the actual claim of codegen: that the emitted file is a real, buildable
Android test.

A substring match on `import androidx.test.uiautomator.By` proves the text is present. It proves
nothing on three other questions:

- whether the surrounding Kotlin parses
- whether the referenced UI Automator APIs exist at the pinned library version
- whether the resulting test passes against a running app

`android-e2e.yml` already builds the showcase app's Compose and Views variants and a resident UI
Automator server for the conformance suite
([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)).
None of that Gradle and emulator infrastructure is wired to codegen output, so a broken emitter
change would ship silently.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Emit and land a fixture.** Generate a showcase scenario's UI Automator test via
  `bajutsu codegen --emit uiautomator`. Check the generated `.kt` file into `demos/showcase`, the way
  `ComponentsUITests.swift` is checked in for XCUITest.
- **Build and run it for real.** Add the generated test to the instrumented test source set of the
  showcase Android project, and build it with Gradle. Run it against the booted emulator that
  `android-e2e.yml` already provisions, and assert that it passes.
- **Wire it into CI.** Add an `android-e2e.yml` job that mirrors `xcuitest (codegen)`. Land it
  non-gating first, per the signal-then-required precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md), and
  promote it once stable.
- **Match the XCUITest floor, not exceed it.** Scope the fixture scenario to the same DSL surface the
  XCUITest codegen gate covers (`tap` / `wait` / `type` / basic assertions). All three targets then
  share a comparable real-compile floor.

## Alternatives considered

- **Compile with the Kotlin compiler alone, without running on a device.** This is cheaper and catches
  a syntax or type error. The emitted calls are UI Automator API calls, though, and their behavior
  (`By.res` resolution, `waitForIdle` semantics) can be confirmed merely against a running app on a
  real or emulated device. A compile-only check would still miss a call that compiles but never
  matches anything.
- **Leave the string-only test suite as the sole gate.** The suite already covers the emitter's DSL
  surface broadly. The gap is the kind of coverage, not its breadth, and no number of additional
  substring assertions would catch a real UI Automator API drift.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Emit a showcase scenario's UI Automator test and check the generated `.kt` file in.
- [x] Build it with Gradle and run it against the emulator, asserting it passes.
- [x] Wire a non-gating `android-e2e.yml` job; promote to required once stable.
- [x] Scope the fixture to the DSL surface the XCUITest codegen gate already covers.

Log:

- Landed the `codegen_android.yaml` fixture, its checked-in `CodegenAndroidUITest.kt`, the compose
  module's `androidTest` wiring, the `e2e-codegen` Makefile target, and the non-gating
  `uiautomator (codegen)` job. A fast-gate test asserts the fixture emits no `// TODO` and matches
  the committed `.kt` byte-for-byte.

## References

- [BE-0209 — Android codegen emitter (Espresso / UI Automator)](../BE-0209-android-codegen-emitter/BE-0209-android-codegen-emitter.md)
- [BE-0208 — Android on-device e2e in CI (emulator via KVM)](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)
- [BE-0083 — Unify the codegen emitters behind a shared scenario walk](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/codegen/uiautomator.py`, `tests/test_codegen_uiautomator.py`, `.github/workflows/android-e2e.yml`
