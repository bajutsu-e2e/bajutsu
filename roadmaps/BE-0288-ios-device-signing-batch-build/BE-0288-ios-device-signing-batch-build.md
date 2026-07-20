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

This item builds the signed iOS device artifacts the AWS Device Farm batch route needs, closing the
one gap [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md) left
open. BE-0238 made the XCUITest backend drive a real device and taught the Device Farm submitter to
emit an iOS submission, but it stopped short of producing the build that submission uploads: an
unsigned Simulator `.app` cannot install on a physical device, so the batch route had neither an app
`.ipa` nor a test runner `.xctestrun` to send. The contribution here is the build tooling that
produces both — a development-signed device `.ipa` of the showcase app and a device-built
`.xctestrun` for the generic XCUITest runner — together with the Device Farm iOS config that binds
them to the reserved device. Signing is parameterized on an Apple Developer team passed through the
environment, so no credential enters the tree, and the Simulator recipes and `make check` stay
unsigned and unchanged.

## Motivation

The batch route reaches a physical device, and a physical device demands a signed build — the exact
step the Simulator path never takes.

- **Device Farm installs a device build, and a device build must be signed.** Device Farm is a batch
  service: it installs the uploaded app on the reserved device and runs `bajutsu run --backend
  xcuitest` against its udid. That upload has to be a device `.ipa`, and `xcodebuild archive` refuses
  to produce one without a signing identity — unlike the unsigned `iphonesimulator` `.app` the
  existing `swiftui-build` target emits.
- **Two artifacts, signed differently.** The app installs as an `.ipa`, which Device Farm re-signs
  with its own provisioning profile, so a development export suffices. The XCUITest runner instead
  ships as a device-built `.xctestrun` *inside* the Python test package, which Device Farm does not
  re-sign — so the runner must already carry a device-valid signature when it is packaged.
- **No signing credential belongs in the repository.** [`CLAUDE.md`](../../CLAUDE.md) forbids
  hardcoded credentials, so the build must read the Team ID from the environment. The Simulator lanes
  and the deterministic `make check` gate must stay unsigned, so the gate still runs on any machine
  — Linux included — with no Apple Developer account.

## Detailed design

The work divides into four build-tooling units plus one manual verification unit. Units 1–4 are
self-contained and need no cloud account; Unit 5 is an on-hardware proof of concept, deliberately
outside the gate.

1. **Device Farm iOS config.** `demos/showcase/devicefarm/showcase.devicefarm.ios.config.yaml`
   mirrors the local `showcase-swiftui` [target](../../docs/glossary.md#target-app-device) with
   `xcuitest.deviceType: device` and no `appPath` / `build` — Device Farm installs the uploaded
   `.ipa`, so the run drives the already-installed build. Its `testRunner` points at the device runner
   inside the uploaded package. This config mirrors the Android Device Farm config that already ships,
   and the live-route pattern documented in
   [`docs/ios-device-cloud.md`](../../docs/ios-device-cloud.md).
2. **Signed device app build.** Two `demos/showcase/Makefile` targets: `swiftui-archive-device`
   (`xcodebuild archive` for `generic/platform=iOS`) and `swiftui-ipa-device` (`xcodebuild
   -exportArchive`). Signing is turned on only here, through command-line build settings
   (`CODE_SIGN_STYLE=Automatic`, `DEVELOPMENT_TEAM=…`) and `-allowProvisioningUpdates`, so the
   Simulator recipes above them stay untouched.
3. **Signed device runner build.** `runner-build-device` runs `xcodebuild build-for-testing` for
   `generic/platform=iOS` with `CODE_SIGNING_ALLOWED=YES`, overriding the runner project's
   Simulator-only default of `NO` on the command line rather than in `project.yml`. A separate
   `derivedData` path keeps the device `.xctestrun` from colliding with the Simulator one under the
   copy glob.
4. **Credential hygiene.** An `ExportOptions.plist` template carries a `__DEVELOPMENT_TEAM__`
   placeholder the Makefile substitutes at build time, so no Team ID is committed; a `require_team`
   guard fails a device build fast with a clear message when `DEVELOPMENT_TEAM` is unset; and every
   device output lands under a gitignored `build/` directory.
5. **End-to-end Device Farm submission (manual proof of concept).** Package the runner's whole
   `Products` directory — the `.xctestrun` references its test bundles by `__TESTROOT__` beside them,
   so the file alone is not enough — and prove one scenario end to end on Device Farm hardware. This
   needs both an Apple Developer account and an AWS Device Farm account, so it stays a manual
   procedure outside `make check`, mirroring the serial-resolution proof of concept in
   [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md).

## Alternatives considered

- **Hand-zip the `.app` into an `.ipa` instead of exporting one.** A device `.ipa` is a
  `Payload/<App>.app` tree zipped and renamed, so hand-packaging the signed `.app` does yield a
  Device Farm–acceptable upload — the aws-samples iOS demo does exactly this. We still prefer
  `archive` + `exportArchive`: it captures the signing style and export options as reproducible build
  settings rather than a manual folder step. (What Device Farm rejects is a bare `.app` zipped
  *without* the `Payload/` wrapper.)
- **Commit a Team ID or a provisioning profile.** Rejected — `CLAUDE.md` forbids committed
  credentials, and a profile would tie the demo to one account. The environment-variable
  parameterization keeps the tree credential-free.
- **Flip `CODE_SIGNING_ALLOWED` in the runner's `project.yml`.** Rejected — that would make the
  Simulator build attempt signing as well. Overriding only on the device command line keeps `make
  check` and the Simulator lanes unsigned.
- **Build the device artifacts in CI now.** Deferred — a signed device build needs an Apple Developer
  account wired into CI, the same way the dormant Device Farm workflow awaits AWS credentials, so the
  CI job stays a follow-on until an account is provisioned.

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

- 2026-07-20 — Landed the four build-tooling units (1–4) in one change ([#1209](https://github.com/bajutsu-e2e/bajutsu/pull/1209)): the Device Farm
  iOS config (`showcase.devicefarm.ios.config.yaml`), the signed device app and runner targets in
  `demos/showcase/Makefile` (`swiftui-archive-device` / `swiftui-ipa-device` / `runner-build-device`),
  and the credential hygiene around them (the `__DEVELOPMENT_TEAM__` `ExportOptions.plist` template
  and the `require_team` guard). Signing is turned on only on the device command line, so the
  Simulator recipes and `make check` stay unsigned and unchanged. Unit 5 — the on-hardware Device
  Farm submission — stays open: it needs an Apple Developer and an AWS account, so it remains a manual
  proof of concept outside the gate.
- 2026-07-20 — Documented Unit 5's manual runbook ([#1213](https://github.com/bajutsu-e2e/bajutsu/pull/1213))
  on the [AWS Device Farm](../../docs/devicefarm.md)
  page (and its Japanese mirror): a new "iOS device-signing proof of concept (manual)" section that
  walks through creating the project and device pool, building the two device-signed artifacts, and
  submitting one scenario with `--platform ios`. Unit 5's box stays unchecked — the empirical
  on-hardware proof still awaits an Apple Developer and an AWS account — but the runbook is now in
  place for whoever runs it.

## References

- [BE-0238 — iOS device-cloud execution](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md) — the item that deferred this device-signing axis.
- [BE-0235 — AWS Device Farm submitter](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md) — the batch submitter these artifacts feed.
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the generic runner built here for a real device.
- [iOS on a real device and in a device cloud](../../docs/ios-device-cloud.md) · [AWS Device Farm](../../docs/devicefarm.md).
