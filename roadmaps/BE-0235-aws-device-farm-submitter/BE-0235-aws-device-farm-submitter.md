**English** · [日本語](BE-0235-aws-device-farm-submitter-ja.md)

# BE-0235 — AWS Device Farm batch submitter

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0235](BE-0235-aws-device-farm-submitter.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0235") |
| Implementing PR | [#1040](https://github.com/bajutsu-e2e/bajutsu/pull/1040) |
| Topic | Device-cloud execution |
<!-- /BE-METADATA -->

## Introduction

This item runs Bajutsu Android scenarios on **AWS Device Farm**, using its **custom test
environment**.

The *device-cloud-provider-abstraction* seam covers live-device providers. Device Farm is not one of
them. It is a **batch** service. It does not lend a device to drive over the network. It runs *your
commands on its host*, and that host already has `adb` connected to the reserved device.

The deliverable is therefore not a runtime provider. It is a **CI-side submitter**. The submitter
packages Bajutsu and the scenarios, and uploads them with a test-spec YAML that runs
`bajutsu run --backend adb`. It then collects the artifacts. The submitter is intentionally decoupled
from the deterministic core.

## Motivation

AWS Device Farm is a common corporate device cloud. Its custom test environment is close to
tailor-made for Bajutsu's Android backend. The spec's phases run arbitrary shell commands on the
device host. They can `pip install` dependencies and select a Python runtime. They also expose `adb`
against the reserved device.

The Android backend "just needs a host with `adb` connected to a device", and a Device Farm
custom-environment run provides exactly that. Android scenarios should therefore run there with
little or no core change. The work is the packaging and submission glue.

This topology differs from the live providers. Forcing it into the runtime provider seam would leak,
because there is no network device to acquire. Bajutsu is the uploaded payload. Keeping the submitter
separate, and decoupled from the core, is deliberate for two reasons:

- The submission machinery is non-deterministic and provider-coupled. It consists of the AWS SDK or
  CLI, credentials, upload, polling, and artifact download. It must not enter the `run`/CI verdict
  path.
- Bajutsu runs *inside* Device Farm exactly as it does anywhere else. The deterministic core is the
  same, and pass/fail comes from the same machine-checkable assertions. The submitter merely ferries
  Bajutsu there and back.

## Detailed design

The submitter is a CI-side tool: a script or workflow plus a Device Farm test-spec YAML. It ships
under an optional extra (e.g. `bajutsu[aws]`) or as CI glue in `.github/`. It is not wired into
`run`. It has four parts:

1. **Package.** Bundle the Bajutsu source or wheel, the target config, and the scenarios into a
   Device Farm test package.
2. **Test spec.** Write a spec YAML with three jobs:
   - Its phases install dependencies (`devicefarm-cli use python …`, `pip install`).
   - The `test` phase runs `bajutsu run --backend adb <scenarios>`.
   - The `post_test` phase copies `runs/` into `$DEVICEFARM_LOG_DIR` for artifact retrieval.
3. **Serial resolution PoC.** Confirm that `adb.resolve_serial()` picks up the device connected to the
   Device Farm host. The reserved device should already appear in `adb devices`. This is the one
   empirical unknown. It is validated **before** any polishing.
4. **Submit + collect.** Upload via the AWS SDK or CLI, poll the run, and download the artifacts and
   report. Pass/fail comes from Bajutsu's own manifest, not from Device Farm's classification.

The batch topology has traits to document and handle:

- Each execution has a **150-minute hard cap**.
- A per-Appium-command timeout exists, and it does not apply to the raw-adb path.
- `.aab` is not accepted (APK only).
- iOS custom mode has extra constraints. The *ios-device-cloud-execution* sibling covers them, not
  this item.

Raw-adb access on Device Farm is a by-product of the host toolchain. It is not a first-class
guarantee, since the first-class path is Appium. The submitter documents this, so that a future
Device Farm change does not silently break the path.

### Work breakdown (MECE)

1. **Serial-resolution PoC.** Write a minimal spec that runs one showcase Android scenario. It proves
   that `resolve_serial` finds the reserved device on the host. Everything else is gated on this.
2. **Package builder.** Assemble the Bajutsu payload (source or wheel, config, and scenarios) for
   upload.
3. **Test-spec template.** Write the install/pre_test/test/post_test spec. It installs the
   dependencies and runs `bajutsu run --backend adb`. It captures artifacts into
   `$DEVICEFARM_LOG_DIR`.
4. **Submit/collect tooling.** Wrap the SDK or CLI to upload, poll, download, and report Bajutsu's
   verdict. The tooling is decoupled from `run` and sits behind `bajutsu[aws]` or CI glue.
5. **Docs.** Add an AWS how-to in `docs/` (both languages). It covers the batch model, the raw-adb
   caveat, the 150-minute cap, and APK-only.

### Prime-directive compliance

- **AI out of the gate.** Bajutsu runs unchanged inside Device Farm, and verdicts come from its
  assertions. The submitter adds no model anywhere.
- **Determinism first.** The submitter is orchestration outside the deterministic core. The run it
  triggers is the same deterministic run as a local one.
- **App-agnostic.** Scenarios and target config are unchanged. Only the delivery mechanism differs,
  and the mechanism lives outside the core.

## Alternatives considered

- **Model Device Farm as a runtime `DeviceProvider` (live seam).** Rejected. There is no network
  device to acquire, because Bajutsu is the payload that runs on the host. Batch belongs in a
  CI-side submitter, not in the runtime seam.
- **Use Device Farm's built-in Appium instead of raw adb.** Rejected for the Android path. It is
  possible, but it would require an Appium-speaking backend. It would also abandon the
  near-zero-change reuse of the existing adb backend. The Appium route is relevant to the iOS
  sibling item.
- **Bake submission into `bajutsu run`.** Rejected. It drags the AWS SDK, credentials, and polling
  into the core. The submitter stays a decoupled CI-side tool behind an optional extra.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Serial-resolution PoC — the mechanism (`pre_test: adb devices` + `--udid booted`) and a
  documented manual procedure ship; the empirical run needs a real AWS account, so it is a human
  procedure deliberately kept off the deterministic gate (docs/devicefarm.md)
- [x] Package builder (`build_package`: Bajutsu payload + config + scenarios → zip)
- [x] Test-spec template (`render_test_spec`: install/pre_test/test/post_test; artifacts → `$DEVICEFARM_LOG_DIR`)
- [x] Submit/collect tooling (`submit_and_collect`: upload / poll / download / manifest verdict; decoupled, `bajutsu[aws]`)
- [x] Docs (AWS how-to: batch model, raw-adb caveat, 150-min cap, APK-only)

### Progress log

- `scripts/devicefarm_submit.py` — the CI-side submitter: `render_test_spec`, `build_package`,
  `verdict_from_manifest` (from Bajutsu's own `manifest.json`, never Device Farm's classification),
  and `submit_and_collect` behind the `DeviceFarmClient` / `Transfer` seams (boto3 lazy-imported).
  Unit-tested against in-memory fakes (`tests/test_devicefarm_submit.py`).
- `demos/showcase/devicefarm/testspec.yml` — a checked-in reference spec.
- `.github/workflows/devicefarm.yml` — manual, opt-in (`workflow_dispatch`), OIDC-gated, dormant
  until an operator wires up an account.
- `docs/devicefarm.md` (+ `docs/ja/`) and the `ci.md` table row.
- `aws = ["boto3>=1.34"]` extra in `pyproject.toml`.
- 2026-07-18 — end-to-end verified on real AWS Device Farm hardware (`SM-S911U1`, Android 13):
  `bajutsu verdict: PASS (1/1)` on `firstlook.yaml`, with `adb devices` listing the reserved device
  and `--udid booted` resolving it. This closes the serial-resolution PoC's one empirical unknown.
  The manual run surfaced seven real Device-Farm-specific issues the in-memory fakes could not, each
  fixed in the submitter (PR #1040): never zip the output into itself; surface Device Farm's reason
  on a failed upload; pack the payload at the zip root so `tests/` is present; synthesize a root
  `requirements.txt`; download non-zip artifacts without unzipping them; bootstrap Python 3.13 with
  uv (the host tops out at 3.12, isolated in `_python_bootstrap_commands` for easy removal once
  Device Farm ships 3.13); and run against the pre-installed app via an `appPath`-less
  `demos/showcase/devicefarm/showcase.devicefarm.config.yaml`.

## References

- [AWS Device Farm — custom test environments](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments.html)
- [AWS Device Farm — custom test spec](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environment-test-spec.html)
- [AWS Device Farm — service limits](https://docs.aws.amazon.com/devicefarm/latest/developerguide/limits.html)
- [BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md)
- [BE-0208 — Android emulator E2E in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)
- Related sibling item: **device-cloud-provider-abstraction** (the live seam this item deliberately sits beside, not inside)
