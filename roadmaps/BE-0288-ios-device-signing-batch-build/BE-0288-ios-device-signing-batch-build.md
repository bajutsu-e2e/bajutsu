**English** · [日本語](BE-0288-ios-device-signing-batch-build-ja.md)

# BE-0288 — iOS device-signing build for the batch route

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0288](BE-0288-ios-device-signing-batch-build.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0288") |
| Implementing PR | [#1209](https://github.com/bajutsu-e2e/bajutsu/pull/1209), [#1213](https://github.com/bajutsu-e2e/bajutsu/pull/1213) |
| Topic | Device-cloud execution |
<!-- /BE-METADATA -->

## Introduction

This item builds the signed iOS device artifacts that the AWS Device Farm batch route needs. It closes the one gap that [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md) left open.

BE-0238 made the XCUITest backend drive a real device. It also taught the Device Farm submitter to emit an iOS submission. It stopped short of producing the build that the submission uploads. An unsigned Simulator `.app` cannot install on a physical device, so the batch route had neither an app `.ipa` nor a test runner `.xctestrun` to send.

The contribution is the build tooling that produces both artifacts:

- A development-signed device `.ipa` of the showcase app.
- A device-built `.xctestrun` for the generic XCUITest runner.

The item also adds the Device Farm iOS config, which binds both artifacts to the reserved device.

The build takes the Apple Developer team from the environment, so no credential enters the tree. The Simulator recipes and `make check` stay unsigned and unchanged.

## Motivation

The batch route reaches a physical device, and a physical device demands a signed build. The Simulator path never takes that step.

- **Device Farm installs a device build, and a device build must be signed.** Device Farm is a batch service. It installs the uploaded app on the reserved device and runs `bajutsu run --backend xcuitest` against the udid of the device. The upload must be a device `.ipa`. The existing `swiftui-build` target emits an unsigned `iphonesimulator` `.app`. `xcodebuild archive` refuses to produce a device `.ipa` without a signing identity.
- **Two artifacts, signed differently.** The app installs as an `.ipa`, which Device Farm re-signs with its own provisioning profile, so a development export suffices. The XCUITest runner instead ships as a device-built `.xctestrun` *inside* the Python test package, and Device Farm does not re-sign it. The runner must already carry a device-valid signature when someone packages it.
- **No signing credential belongs in the repository.** [`CLAUDE.md`](../../CLAUDE.md) forbids hardcoded credentials, so the build reads the Team ID from the environment. The Simulator lanes and the deterministic `make check` gate must stay unsigned. The gate then still runs on any machine, Linux included, with no Apple Developer account.

## Detailed design

The work divides into four build-tooling units plus one manual verification unit. Units 1–4 are self-contained and need no cloud account. Unit 5 is an on-hardware proof of concept, deliberately outside the gate.

1. **Device Farm iOS config.** `demos/showcase/devicefarm/showcase.devicefarm.ios.config.yaml` mirrors the local `showcase-swiftui` [target](../../docs/glossary.md#target-app-device). It sets `xcuitest.deviceType: device` and has no `appPath` or `build`. Device Farm installs the uploaded `.ipa`, so the run drives the already-installed build. The `testRunner` of the config points at the device runner inside the uploaded package. The config mirrors the Android Device Farm config that already ships. It also follows the live-route pattern documented in [`docs/ios-device-cloud.md`](../../docs/ios-device-cloud.md).
2. **Signed device app build.** Two `demos/showcase/Makefile` targets build the app: `swiftui-archive-device` (`xcodebuild archive` for `generic/platform=iOS`) and `swiftui-ipa-device` (`xcodebuild -exportArchive`). Only these targets turn signing on, through command-line build settings (`CODE_SIGN_STYLE=Automatic`, `DEVELOPMENT_TEAM=…`) and `-allowProvisioningUpdates`. The Simulator recipes above these targets stay untouched.
3. **Signed device runner build.** `runner-build-device` runs `xcodebuild build-for-testing` for `generic/platform=iOS` with `CODE_SIGNING_ALLOWED=YES`. The command line overrides the Simulator-only default of `NO` in the runner project, and `project.yml` stays unchanged. A separate `derivedData` path keeps the device `.xctestrun` from colliding with the Simulator one under the copy glob.
4. **Credential hygiene.** Three mechanisms keep credentials out of the tree:
   - An `ExportOptions.plist` template carries a `__DEVELOPMENT_TEAM__` placeholder, which the Makefile substitutes at build time. The repository never holds a Team ID.
   - A `require_team` guard fails a device build fast, with a clear message, when `DEVELOPMENT_TEAM` is unset.
   - Every device output lands under a gitignored `build/` directory.
5. **End-to-end Device Farm submission (manual proof of concept).** Package the whole `Products` directory of the runner. The `.xctestrun` references its test bundles by `__TESTROOT__` beside them, so the file alone is not enough. Then prove one scenario end to end on Device Farm hardware. The proof needs both an Apple Developer account and an AWS Device Farm account. It therefore stays a manual procedure outside `make check`, like the serial-resolution proof of concept in [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md).

## Alternatives considered

- **Hand-zip the `.app` into an `.ipa` instead of exporting one.** A device `.ipa` is a `Payload/<App>.app` tree, zipped and renamed. Hand-packaging the signed `.app` does yield an upload that Device Farm accepts, and the aws-samples iOS demo does just that. We still prefer `archive` plus `exportArchive`. This route captures the signing style and the export options as reproducible build settings, not as a manual folder step. Device Farm rejects a bare `.app` zipped *without* the `Payload/` wrapper.
- **Commit a Team ID or a provisioning profile.** Rejected. `CLAUDE.md` forbids committed credentials, and a profile would tie the demo to one account. The environment-variable parameterization keeps the tree credential-free.
- **Flip `CODE_SIGNING_ALLOWED` in the `project.yml` of the runner.** Rejected, because the flip would make the Simulator build attempt signing as well. Overriding the setting on the device command line alone keeps `make check` and the Simulator lanes unsigned.
- **Build the device artifacts in CI now.** Deferred. A signed device build needs an Apple Developer account wired into CI. The dormant Device Farm workflow likewise awaits AWS credentials. The CI job stays a follow-on until someone provisions an account.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — Device Farm iOS config (`showcase.devicefarm.ios.config.yaml`)
- [x] Unit 2 — signed device app build (`swiftui-archive-device` / `swiftui-ipa-device`, `ExportOptions.plist`)
- [x] Unit 3 — signed device runner build (`runner-build-device`)
- [x] Unit 4 — credential hygiene (`require_team` guard, placeholder template, gitignored output)
- [ ] Unit 5 — end-to-end Device Farm submission proof of concept (needs Apple Developer + AWS accounts; manual, outside `make check`)

Log:

- 2026-07-20 — Landed the four build-tooling units (1–4) in one change ([#1209](https://github.com/bajutsu-e2e/bajutsu/pull/1209)).
  - The Device Farm iOS config (`showcase.devicefarm.ios.config.yaml`).
  - The signed device app and runner targets in `demos/showcase/Makefile` (`swiftui-archive-device`, `swiftui-ipa-device`, and `runner-build-device`).
  - The credential hygiene around the targets: the `__DEVELOPMENT_TEAM__` `ExportOptions.plist` template and the `require_team` guard.
  - The device command line alone turns signing on, so the Simulator recipes and `make check` stay unsigned and unchanged.
  - Unit 5, the on-hardware Device Farm submission, stays open. It needs an Apple Developer and an AWS account, so it remains a manual proof of concept outside the gate.
- 2026-07-20 — Documented the manual runbook of Unit 5 ([#1213](https://github.com/bajutsu-e2e/bajutsu/pull/1213)) on the [AWS Device Farm](../../docs/devicefarm.md) page and its Japanese mirror.
  - The new section "iOS device-signing proof of concept (manual)" walks through creating the project and the device pool, building the two device-signed artifacts, and submitting one scenario with `--platform ios`.
  - The box of Unit 5 stays unchecked, because the empirical on-hardware proof still awaits an Apple Developer and an AWS account. The runbook is now in place for whoever runs the proof.

## References

- [BE-0238 — iOS device-cloud execution](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md) — the item that deferred this device-signing axis.
- [BE-0235 — AWS Device Farm submitter](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md) — the batch submitter these artifacts feed.
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the generic runner built here for a real device.
- [iOS on a real device and in a device cloud](../../docs/ios-device-cloud.md) · [AWS Device Farm](../../docs/devicefarm.md).
