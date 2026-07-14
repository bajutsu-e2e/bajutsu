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

This item runs Bajutsu Android scenarios on **AWS Device Farm** using its **custom test
environment**. Unlike the live-device providers of the *device-cloud-provider-abstraction* seam,
Device Farm is a **batch** service: it does not lend you a device to drive over the network — it
runs *your commands on its host*, which already has `adb` connected to the reserved device. So the
deliverable here is not a runtime provider but a **CI-side submitter**: package Bajutsu plus the
scenarios, upload with a test-spec YAML that runs `bajutsu run --backend adb`, and collect the
artifacts. The submitter is intentionally decoupled from the deterministic core.

## Motivation

AWS Device Farm is a common corporate device cloud, and its custom test environment is close to
tailor-made for Bajutsu's Android backend: the spec's phases run arbitrary shell commands on the
device host, can `pip install` dependencies and select a Python runtime, and expose `adb` against
the reserved device. The Android backend "just needs a host with `adb` connected to a device" —
which is exactly what a Device Farm custom-environment run provides. So Android scenarios should be
runnable there with little or no core change; the work is the packaging and submission glue.

This is a distinct topology from the live providers, and forcing it into the runtime provider seam
would be leaky (there is no network device to acquire — Bajutsu is the uploaded payload). Keeping it
as a separate submitter, decoupled from the core, is deliberate:

- The submission machinery (the AWS SDK / CLI, credentials, upload, polling, artifact download) is
  non-deterministic and provider-coupled; it must not enter the `run`/CI verdict path.
- Bajutsu runs *inside* Device Farm exactly as it does anywhere — the same deterministic core, the
  same pass/fail from machine-checkable assertions. The submitter only ferries it there and back.

## Detailed design

The submitter is a CI-side tool (a script/workflow plus a Device Farm test-spec YAML), shipped
under an optional extra (e.g. `bajutsu[aws]`) or as CI glue in `.github/`, not wired into `run`:

1. **Package** — bundle the Bajutsu source/wheel, the target config, and the scenarios into a
   Device Farm test package.
2. **Test spec** — a spec YAML whose phases install dependencies (`devicefarm-cli use python …`,
   `pip install`), then run `bajutsu run --backend adb <scenarios>` in the `test` phase, and copy
   `runs/` into `$DEVICEFARM_LOG_DIR` in `post_test` for artifact retrieval.
3. **Serial resolution PoC** — confirm that `adb.resolve_serial()` picks up the Device Farm host's
   connected device (the reserved device should already appear in `adb devices`). This is the one
   empirical unknown and is validated **before** any polishing.
4. **Submit + collect** — upload via the AWS SDK/CLI, poll the run, and download artifacts/report;
   surface pass/fail from Bajutsu's own manifest, not from Device Farm's classification.

Batch-topology traits to document and handle: the run has a **150-minute hard cap** per execution
and a per-Appium-command timeout that does not apply to the raw-adb path; `.aab` is not accepted
(APK only); iOS custom-mode has extra constraints (covered by the *ios-device-cloud-execution*
sibling, not here). Raw-adb access on Device Farm is a by-product of the host toolchain rather than
a first-class guarantee (the first-class path is Appium); the submitter documents this so a future
Device Farm change does not silently break it.

### Work breakdown (MECE)

1. **Serial-resolution PoC** — a minimal spec that runs one showcase Android scenario and proves
   `resolve_serial` finds the reserved device on the host. Gate everything else on this.
2. **Package builder** — assemble the Bajutsu payload (source/wheel + config + scenarios) for
   upload.
3. **Test-spec template** — the install/pre_test/test/post_test spec that installs deps and runs
   `bajutsu run --backend adb`, capturing artifacts into `$DEVICEFARM_LOG_DIR`.
4. **Submit/collect tooling** — SDK/CLI wrapper to upload, poll, download, and report Bajutsu's
   verdict; decoupled from `run`, behind `bajutsu[aws]` / CI glue.
5. **Docs** — an AWS how-to in `docs/` (both languages): batch model, the raw-adb caveat, the
   150-minute cap, APK-only.

### Prime-directive compliance

- **AI out of the gate.** Bajutsu runs unchanged inside Device Farm; verdicts come from its
  assertions. The submitter adds no model anywhere.
- **Determinism first.** The submitter is orchestration outside the deterministic core; the run it
  triggers is the same deterministic run as locally.
- **App-agnostic.** Scenarios and target config are unchanged; only the delivery mechanism differs,
  and it lives outside the core.

## Alternatives considered

- **Model Device Farm as a runtime `DeviceProvider` (live seam).** There is no network device to
  acquire; Bajutsu is the payload that runs on the host. Rejected — batch belongs in a CI-side
  submitter, not the runtime seam.
- **Use Device Farm's built-in Appium instead of raw adb.** Possible, but it would require an
  Appium-speaking backend and abandon the near-zero-change reuse of the existing adb backend.
  Rejected for the Android path; the Appium route is relevant to the iOS sibling item.
- **Bake submission into `bajutsu run`.** Drags the AWS SDK/credentials/polling into the core.
  Rejected — keep it a decoupled CI-side tool behind an optional extra.

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

## References

- [AWS Device Farm — custom test environments](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments.html)
- [AWS Device Farm — custom test spec](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environment-test-spec.html)
- [AWS Device Farm — service limits](https://docs.aws.amazon.com/devicefarm/latest/developerguide/limits.html)
- [BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md)
- [BE-0208 — Android emulator E2E in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)
- Related sibling item: **device-cloud-provider-abstraction** (the live seam this item deliberately sits beside, not inside)
