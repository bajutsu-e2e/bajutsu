# Running on AWS Device Farm

[AWS Device Farm](https://docs.aws.amazon.com/devicefarm/latest/developerguide/welcome.html) is a
device cloud where you run tests against real and virtual devices Amazon hosts. Bajutsu runs there
through Device Farm's **custom test environment**, driving its Android (adb) backend against the
reserved device — with no change to the deterministic core.

## The batch model, and why this is a submitter, not a driver

Device Farm is a **batch** service, not a live-device provider. It does not lend you a device to
drive over the network; it runs *your commands on its host*, and that host already has `adb`
connected to the reserved device. So the deliverable is not a runtime driver (there is no device to
acquire) — it is a **CI-side submitter**: package Bajutsu plus your scenarios, upload them with a
test-spec that runs `bajutsu run --backend adb`, let Device Farm execute it on the host, and collect
the artifacts.

The submitter lives entirely outside the deterministic core, at
[`scripts/devicefarm_submit.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/devicefarm_submit.py).
Nothing in the upload / poll / download machinery touches the `run`/CI verdict path: Bajutsu runs
inside Device Farm exactly as it does locally — the same deterministic core, the same pass/fail from
machine-checkable assertions — so the verdict the submitter reports comes from **Bajutsu's own
`manifest.json`**, never from Device Farm's own PASSED/FAILED classification.

## Batch-topology caveats

These are properties of Device Farm itself; the submitter documents and handles them so a future
Device Farm change does not silently break the flow.

- **Raw adb is a by-product, not a guarantee.** Device Farm's first-class custom-environment path is
  Appium. `adb` against the reserved device is available because it is part of the host toolchain,
  not a contract Amazon promises — Bajutsu's Android backend needs only "a host with `adb` connected
  to a device", which the custom environment provides, but treat it as a by-product that a future
  platform change could remove.
- **150-minute hard cap.** A single custom-environment execution is capped at 150 minutes. The
  submitter polls no longer than that and then fails loudly rather than blocking a CI job forever.
- **APK only.** Device Farm's Android app upload accepts an `.apk`; it does not accept an `.aab`.
  Build a debug APK for the run.
- **Per-Appium-command timeout.** Device Farm's per-command Appium timeout does not apply to the
  raw-adb path Bajutsu uses; the 150-minute execution cap is the effective bound.

iOS on Device Farm's custom mode carries extra constraints and is tracked separately (the
*ios-device-cloud-execution* sibling item), not here.

## The test spec

Device Farm drives the run from a
[custom-environment test spec](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environment-test-spec.html)
— a YAML file with `install` / `pre_test` / `test` / `post_test` phases of shell commands. The
submitter renders one that:

1. **install** — bootstraps Python 3.13 with uv and installs Bajutsu from the uploaded test package
   into a venv on it. Device Farm's host tops out at Python 3.12 (`devicefarm-cli use python` only
   offers the runtimes Amazon preinstalls) while Bajutsu requires 3.13, so the phase installs uv with
   the host's base pip, has uv fetch a standalone 3.13, and installs into that venv. This is a
   temporary workaround, removed once Device Farm ships 3.13 (see `_python_bootstrap_commands` in the
   submitter). The adb backend is pure subprocess, so the base install (no extras) is enough.
2. **pre_test** — runs `adb devices` to prove the reserved device is visible (the serial-resolution
   check).
3. **test** — one `bajutsu run --backend adb --udid booted` per scenario, so a scenario that fails
   still leaves a manifest for the others.
4. **post_test** — copies the whole `runs/` tree into `$DEVICEFARM_LOG_DIR` so the artifacts come
   back.

A checked-in reference spec for two showcase scenarios is at
[`demos/showcase/devicefarm/testspec.yml`](https://github.com/bajutsu-e2e/bajutsu/blob/main/demos/showcase/devicefarm/testspec.yml).

## Using the submitter

Install the optional extra:

```bash
uv sync --extra aws        # pulls in boto3
```

Build the package and spec locally without any AWS credentials (useful to inspect what will be
uploaded):

```bash
uv run python scripts/devicefarm_submit.py \
  --scenario scenarios/firstlook.yaml --scenario scenarios/controls.yaml \
  --target showcase-compose --config showcase.config.yaml \
  --app-apk app-debug.apk \
  --package .=. \
  --package demos/showcase/showcase.config.yaml=showcase.config.yaml \
  --package demos/showcase/scenarios=scenarios \
  --package-only
```

Each `--package SRC=ARCNAME` adds a file or directory to the test package under `ARCNAME` (the
arcname `.` packs a directory at the package root); the scenario / config paths you pass are the
paths *inside* the package. Packaging Bajutsu with `--package .=.` puts its `pyproject.toml` and
`tests/` directory at the root, and the submitter synthesizes an empty root `requirements.txt`, so
the upload satisfies Device Farm's APPIUM_PYTHON_TEST_PACKAGE validation while the test spec still
does the real install. Drop `--package-only` and add
`--project-arn` / `--device-pool-arn` (with AWS credentials configured in the environment) to submit
the run, poll it to completion, download the artifacts, and print Bajutsu's verdict. The process
exit code is `0` only when every scenario passed.

## The GitHub Actions workflow

[`.github/workflows/devicefarm.yml`](https://github.com/bajutsu-e2e/bajutsu/blob/main/.github/workflows/devicefarm.yml)
wraps the submitter as a **manual, opt-in** workflow. It is `workflow_dispatch` only — never on push
or pull request, so it is not on the merge path and is not a required check. It mints a short-lived
AWS credential by exchanging the workflow's GitHub OIDC token for the `AWS_DEVICEFARM_ROLE_ARN` role
(no static key), scoped to a `devicefarm` Environment, and reads the project and device-pool ARNs
from the `DEVICEFARM_PROJECT_ARN` / `DEVICEFARM_DEVICE_POOL_ARN` repository variables. With any of
the three unset, the job is a green no-op (a `::notice::`, never red), so it stays dormant until an
operator wires up an account.

## The serial-resolution proof of concept (manual)

The one empirical unknown is whether Bajutsu's Android backend picks up the Device Farm host's
reserved device — the `pre_test` `adb devices` should list it, and `bajutsu run --udid booted`
should resolve it. Proving this end-to-end needs a real AWS account, credentials, a Device Farm
project, and billing, so it is **not** part of the deterministic `make check` gate (which must run
anywhere, with no cloud account) — running it is a manual, human procedure:

1. Create a Device Farm project and a device pool in `us-west-2`; note their ARNs.
2. Build the showcase Compose debug APK: `make -C demos/showcase/android compose-build`.
3. Run the submitter (or the workflow) against one scenario, e.g. `scenarios/firstlook.yaml`.
4. Confirm from the downloaded artifacts that `adb devices` listed the reserved device and the
   scenario produced a `manifest.json` with the expected verdict. The submitter extracts the
   CUSTOMER_ARTIFACT zip (the `runs/` tree with the manifests) into the destination and writes
   Device Farm's plain-file artifacts — the device and test-spec logs that carry the `adb devices`
   output — under a `logs/` subdirectory alongside it.

Once confirmed for your account, the workflow can run the fuller scenario set on demand.
