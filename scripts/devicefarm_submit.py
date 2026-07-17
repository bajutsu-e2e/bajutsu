#!/usr/bin/env python3
"""AWS Device Farm batch submitter (BE-0235).

Device Farm is a *batch* device cloud: it does not lend a device to drive over the network — it runs
*your* commands on a host that already has `adb` connected to the reserved device. So this is not a
runtime provider (there is no device to acquire); it is CI-side glue that ferries Bajutsu to the
Device Farm host and back. Bajutsu runs *inside* Device Farm exactly as it does anywhere — the same
deterministic core, the same pass/fail from machine-checkable assertions — so the verdict this tool
surfaces comes from **Bajutsu's own manifest**, never from Device Farm's run classification.

The flow, all outside the deterministic `run`/CI verdict path:

1. `render_test_spec` — the custom-environment test spec that installs deps, runs
   `bajutsu run --backend adb <scenarios>`, and copies `runs/` into ``$DEVICEFARM_LOG_DIR`` so the
   artifacts come back.
2. `build_package` — bundle the Bajutsu payload (source/wheel + config + scenarios) for upload.
3. `submit_and_collect` — upload the app APK, the test package, and the spec; schedule the run; poll
   it to completion; download the artifacts; and derive the verdict via `verdict_from_manifest`.

The AWS SDK (boto3) is imported lazily and reached only through the `DeviceFarmClient` / `Transfer`
seams, so this module imports without the ``aws`` extra and its logic is unit-tested against an
in-memory fake. Raw-adb access on the Device Farm host is a by-product of its toolchain rather than
a first-class guarantee (the first-class path is Appium); this tool documents that so a future
Device Farm change does not silently break it.
"""

from __future__ import annotations

import argparse
import json
import shlex
import time
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import yaml

# Device Farm caps one custom-environment execution at 150 minutes; poll no longer than that before
# giving up rather than blocking a CI job indefinitely.
_HARD_CAP_SECONDS = 150 * 60
_POLL_INTERVAL_SECONDS = 30

# boto3 Device Farm upload types. The app is an Android APK (`.aab` is not accepted); the test
# package and spec use the Appium-Python custom-environment types.
_UPLOAD_APP = "ANDROID_APP"
_UPLOAD_TEST_PACKAGE = "APPIUM_PYTHON_TEST_PACKAGE"
_UPLOAD_TEST_SPEC = "APPIUM_PYTHON_TEST_SPEC"

# Noise directories to keep out of the upload. `--package .=bajutsu` walks the whole checked-out
# repo root, which already holds `.git/` and the `uv`-created `.venv/` plus build/test caches and
# scratch output; zipping them would bloat (and could break) every upload. Matched on any path
# component during the walk.
_PACKAGE_EXCLUDES = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".coverage",
        "node_modules",
        "runs",
        "tmp",
        ".DS_Store",
    }
)


class DeviceFarmError(RuntimeError):
    """A Device Farm submission failed loudly — a missing payload, a failed upload, or a run that
    never completed. Never swallowed: a test tool that hides its own failure is worse than none."""


@dataclass(frozen=True)
class Verdict:
    """Bajutsu's verdict for a Device Farm run, read from the downloaded ``manifest.json`` tree."""

    ok: bool
    passed: int
    total: int
    failures: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Test spec
# ---------------------------------------------------------------------------


def render_test_spec(
    scenarios: Sequence[str],
    *,
    target: str,
    config: str,
    python_version: str = "3.13",
) -> str:
    """Render a Device Farm custom-environment test spec that runs the given scenarios over adb.

    The `test` phase runs one `bajutsu run --backend adb` per scenario against the host's reserved
    device (serial ``booted``), so a scenario that fails still leaves a manifest for the others; the
    `post_test` phase copies the whole ``runs/`` tree into ``$DEVICEFARM_LOG_DIR`` so `list_artifacts`
    can return it.

    Args:
        scenarios: Scenario file paths as they appear inside the unpacked test package.
        target: The `targets.<name>` config entry the scenarios run against.
        config: The Bajutsu config path inside the unpacked test package.
        python_version: The Python the Device Farm host should select for the run.

    Raises:
        ValueError: If `scenarios` is empty — a spec that runs nothing would silently "pass".
    """
    if not scenarios:
        raise ValueError("cannot render a test spec with no scenario to run")
    # `target`, `config`, and each scenario path trace back to workflow_dispatch text inputs, so
    # quote every splice: an unescaped space or shell metacharacter would otherwise break argument
    # parsing or inject a command onto the Device Farm host running under the OIDC-minted AWS role.
    run_cmds = [
        "bajutsu run"
        f" --scenario {shlex.quote(s)} --target {shlex.quote(target)}"
        f" --config {shlex.quote(config)} --backend adb --udid booted"
        for s in scenarios
    ]
    spec: dict[str, Any] = {
        "version": 0.1,
        "phases": {
            "install": {
                "commands": [
                    f"devicefarm-cli use python {python_version}",
                    "python -m pip install --upgrade pip",
                    # The test package unpacks into $DEVICEFARM_TEST_PACKAGE_PATH; install Bajutsu
                    # from it. The adb backend is pure subprocess, so the base install suffices.
                    'python -m pip install "$DEVICEFARM_TEST_PACKAGE_PATH"',
                ]
            },
            "pre_test": {
                "commands": [
                    # Prove the reserved device is visible before running (the serial-resolution PoC).
                    "adb devices",
                ]
            },
            "test": {"commands": run_cmds},
            "post_test": {
                "commands": [
                    'cp -r runs "$DEVICEFARM_LOG_DIR"/ || true',
                ]
            },
        },
        "artifacts": ["$DEVICEFARM_LOG_DIR"],
    }
    return yaml.safe_dump(spec, sort_keys=False)


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def build_package(entries: Sequence[tuple[Path, str]], out_zip: Path) -> Path:
    """Bundle the Bajutsu payload into `out_zip` for upload, one `(source, arcname)` pair per entry.

    A directory source is added recursively under its arcname; a file source is added at its
    arcname. Paths under a `_PACKAGE_EXCLUDES` component (VCS/build/cache/scratch noise such as
    `.git` and `.venv`), symlinks, and the output archive itself are skipped. Returns `out_zip`.

    Raises:
        DeviceFarmError: If any source path does not exist — an incomplete package would fail
            opaquely on the Device Farm host, so fail here instead.
    """
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    # `--out` commonly lands inside a packaged source (its default sits in the repo root that
    # `--package .=bajutsu` walks). Skip the archive by resolved path so it never zips itself —
    # doing so reads back its own growing bytes and balloons the upload without bound.
    out_resolved = out_zip.resolve()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for source, arcname in entries:
            if not source.exists():
                raise DeviceFarmError(f"package source not found: {source}")
            if source.is_dir():
                for path in sorted(source.rglob("*")):
                    rel = path.relative_to(source)
                    # Skip noise dirs (`.git`, `.venv`, caches, scratch) so `--package .=bajutsu`
                    # doesn't zip the whole repo root, and skip symlinks so a link pointing outside
                    # the source tree can't pull in unintended files (mirrors `archive_run_dir`).
                    if any(part in _PACKAGE_EXCLUDES for part in rel.parts):
                        continue
                    if path.resolve() == out_resolved:
                        continue
                    if path.is_file() and not path.is_symlink():
                        zf.write(path, f"{arcname}/{rel.as_posix()}")
            else:
                zf.write(source, arcname)
    return out_zip


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def verdict_from_manifest(runs_root: Path) -> Verdict:
    """Derive Bajutsu's overall verdict from every ``manifest.json`` under `runs_root`.

    Aggregates the per-scenario verdicts across all run manifests (Device Farm may run several
    `bajutsu run` invocations, each writing its own run dir). The verdict is a pass only when at
    least one scenario ran and every scenario passed — an empty tree is a failure, not a silent
    pass, since a run that produced no manifest produced no verdict.
    """
    passed = 0
    total = 0
    failures: list[str] = []
    for manifest in sorted(runs_root.rglob("manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            total += 1
            failures.append(f"<unreadable manifest: {manifest}>")
            continue
        for scenario in data.get("scenarios", []):
            total += 1
            if scenario.get("ok"):
                passed += 1
            else:
                failures.append(str(scenario.get("scenario", "<unknown>")))
    return Verdict(ok=total > 0 and passed == total, passed=passed, total=total, failures=failures)


# ---------------------------------------------------------------------------
# AWS seams
# ---------------------------------------------------------------------------


class DeviceFarmClient(Protocol):
    """The slice of the boto3 ``devicefarm`` client the submitter uses (so an in-memory fake fits).

    Each method mirrors the boto3 call of the same name; the responses are the nested dicts boto3
    returns (``{"upload": {...}}``, ``{"run": {...}}``, ``{"artifacts": [...]}``).

    Each stub body is ``raise NotImplementedError``: a bare ``...`` here is a newly-added expression
    statement CodeQL flags as "no effect" (a new-file line can't inherit the dismissal the same idiom
    carries on ``main``, e.g. ``network.py``'s ``Collector``), while a docstring-only body would
    silently return ``None`` against the non-``None`` annotation if the Protocol were ever called
    directly. ``raise`` is CodeQL-clean and fails loud, so it closes both gaps at once.
    """

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        """Register a new upload; boto3 returns ``{"upload": {...}}`` with the presigned PUT URL."""
        raise NotImplementedError

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        """Fetch an upload's status (``INITIALIZED`` → ``SUCCEEDED`` / ``FAILED``)."""
        raise NotImplementedError

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        """Schedule a run from the uploaded app/test/spec; boto3 returns ``{"run": {...}}``."""
        raise NotImplementedError

    def get_run(self, *, arn: str) -> dict[str, Any]:
        """Fetch a run's current status; boto3 returns ``{"run": {...}}``."""
        raise NotImplementedError

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        """List a run's artifacts of the given type; boto3 returns ``{"artifacts": [...]}``."""
        raise NotImplementedError


class Transfer(Protocol):
    """The HTTP file transfer the submitter uses against Device Farm's presigned S3 URLs."""

    def upload(self, url: str, path: Path) -> None:
        """PUT the file at `path` to the presigned `url`."""

    def download(self, url: str, dest: Path) -> None:
        """Download and unpack the artifact at `url` into `dest`."""


def _upload_one(
    client: DeviceFarmClient,
    transfer: Transfer,
    *,
    project_arn: str,
    name: str,
    upload_type: str,
    path: Path,
    sleep: Callable[[float], None],
) -> str:
    """Create an upload, PUT the file, and poll until it succeeds; return the upload ARN.

    Raises:
        DeviceFarmError: If Device Farm reports the upload FAILED, or it does not succeed within the
            hard cap.
    """
    created = client.create_upload(projectArn=project_arn, name=name, type=upload_type)["upload"]
    transfer.upload(created["url"], path)
    deadline = time.monotonic() + _HARD_CAP_SECONDS
    while True:
        status = client.get_upload(arn=created["arn"])["upload"]["status"]
        if status == "SUCCEEDED":
            return str(created["arn"])
        if status == "FAILED":
            raise DeviceFarmError(f"upload failed on Device Farm: {name}")
        if time.monotonic() >= deadline:
            raise DeviceFarmError(f"upload did not complete within the 150-minute cap: {name}")
        sleep(_POLL_INTERVAL_SECONDS)


def _wait_run(
    client: DeviceFarmClient,
    run_arn: str,
    *,
    sleep: Callable[[float], None],
) -> None:
    """Poll the scheduled run until Device Farm reports it COMPLETED.

    Device Farm's own ``result`` (PASSED/FAILED) is deliberately ignored — the verdict is Bajutsu's,
    read from the downloaded manifest. This only waits for the batch execution to finish.

    Raises:
        DeviceFarmError: If the run does not complete within the 150-minute hard cap.
    """
    deadline = time.monotonic() + _HARD_CAP_SECONDS
    while client.get_run(arn=run_arn)["run"]["status"] != "COMPLETED":
        if time.monotonic() >= deadline:
            raise DeviceFarmError("run did not complete within the 150-minute cap")
        sleep(_POLL_INTERVAL_SECONDS)


def submit_and_collect(
    client: DeviceFarmClient,
    transfer: Transfer,
    *,
    project_arn: str,
    device_pool_arn: str,
    app_apk: Path,
    package_zip: Path,
    spec_yaml: Path,
    dest: Path,
    run_name: str = "bajutsu",
    sleep: Callable[[float], None] = time.sleep,
) -> Verdict:
    """Upload the payload, schedule the run, wait for it, download artifacts, and return the verdict.

    Uploads the app APK, the test package, and the test spec (each a create-upload + presigned PUT +
    poll), schedules a run wiring the three together, waits for completion, downloads the customer
    artifacts into `dest`, and derives the verdict from the returned ``manifest.json`` tree — always
    Bajutsu's verdict, never Device Farm's classification.

    Raises:
        DeviceFarmError: If any upload fails or the run does not complete within the hard cap.
    """
    app_arn = _upload_one(
        client,
        transfer,
        project_arn=project_arn,
        name=app_apk.name,
        upload_type=_UPLOAD_APP,
        path=app_apk,
        sleep=sleep,
    )
    package_arn = _upload_one(
        client,
        transfer,
        project_arn=project_arn,
        name=package_zip.name,
        upload_type=_UPLOAD_TEST_PACKAGE,
        path=package_zip,
        sleep=sleep,
    )
    spec_arn = _upload_one(
        client,
        transfer,
        project_arn=project_arn,
        name=spec_yaml.name,
        upload_type=_UPLOAD_TEST_SPEC,
        path=spec_yaml,
        sleep=sleep,
    )
    scheduled = client.schedule_run(
        projectArn=project_arn,
        appArn=app_arn,
        devicePoolArn=device_pool_arn,
        name=run_name,
        test={"type": "APPIUM_PYTHON", "testPackageArn": package_arn, "testSpecArn": spec_arn},
    )
    run_arn = scheduled["run"]["arn"]
    _wait_run(client, run_arn, sleep=sleep)
    dest.mkdir(parents=True, exist_ok=True)
    for artifact in client.list_artifacts(arn=run_arn, type="FILE")["artifacts"]:
        transfer.download(artifact["url"], dest)
    return verdict_from_manifest(dest)


def _devicefarm_client() -> DeviceFarmClient:
    """Build the real boto3 ``devicefarm`` client (lazy import — the ``aws`` extra is optional)."""
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise DeviceFarmError(
            "the AWS Device Farm submitter needs boto3 — install it with `uv sync --extra aws`"
        ) from exc
    # Device Farm's control plane lives only in us-west-2. boto3's dynamically built client is
    # untyped, so present it as the DeviceFarmClient slice we actually use.
    return cast("DeviceFarmClient", boto3.client("devicefarm", region_name="us-west-2"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Package the payload and, unless ``--package-only``, submit it and print Bajutsu's verdict."""
    parser = argparse.ArgumentParser(
        description="Submit Bajutsu Android scenarios to AWS Device Farm."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        required=True,
        help="scenario path inside the package (repeat for several)",
    )
    parser.add_argument("--target", required=True, help="targets.<name> config entry")
    parser.add_argument("--config", required=True, help="Bajutsu config path inside the package")
    parser.add_argument("--app-apk", type=Path, required=True, help="the Android APK to install")
    parser.add_argument(
        "--package",
        required=True,
        help="entry as source=arcname (repeat)",
        action="append",
        dest="package_entries",
        metavar="SRC=ARCNAME",
        default=[],
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("devicefarm-package.zip"),
        help="where to write the test package zip",
    )
    parser.add_argument("--python-version", default="3.13")
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="build the package and spec, but do not submit (no AWS credentials needed)",
    )
    parser.add_argument(
        "--project-arn", help="Device Farm project ARN (required unless --package-only)"
    )
    parser.add_argument(
        "--device-pool-arn", help="Device Farm device-pool ARN (required unless --package-only)"
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("devicefarm-artifacts"),
        help="where to download the run artifacts",
    )
    args = parser.parse_args(argv)

    spec = render_test_spec(
        args.scenarios, target=args.target, config=args.config, python_version=args.python_version
    )
    spec_path = args.out.parent / "testspec.yml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(spec, encoding="utf-8")

    entries: list[tuple[Path, str]] = []
    for raw in args.package_entries:
        src, _, arcname = raw.partition("=")
        entries.append((Path(src), arcname or Path(src).name))
    build_package(entries, args.out)
    print(f"wrote package {args.out} and spec {spec_path}")

    if args.package_only:
        return 0
    if not args.project_arn or not args.device_pool_arn:
        parser.error("--project-arn and --device-pool-arn are required unless --package-only")

    verdict = submit_and_collect(
        _devicefarm_client(),
        _HttpTransfer(),
        project_arn=args.project_arn,
        device_pool_arn=args.device_pool_arn,
        app_apk=args.app_apk,
        package_zip=args.out,
        spec_yaml=spec_path,
        dest=args.dest,
    )
    print(f"bajutsu verdict: {'PASS' if verdict.ok else 'FAIL'} ({verdict.passed}/{verdict.total})")
    if verdict.failures:
        print("failed scenarios: " + ", ".join(verdict.failures))
    return 0 if verdict.ok else 1


def _safe_extract(zip_file: zipfile.ZipFile, dest: Path) -> None:
    """Extract every member of *zip_file* into *dest*, confining each to *dest* (zip-slip guard).

    The artifact zip comes from Device Farm's presigned URL; a member with a ``../`` or absolute
    name would otherwise let ``extractall`` write outside *dest*. Each member is resolved and
    checked to land strictly under *dest* before extracting (mirrors `serve.uploads.extract_bundle`).

    Raises:
        DeviceFarmError: If any member resolves outside *dest* — fail loud rather than write astray.
    """
    dest_root = dest.resolve()
    for member in zip_file.infolist():
        target = (dest / member.filename).resolve()
        if target != dest_root and dest_root not in target.parents:
            raise DeviceFarmError(f"unsafe path in Device Farm artifact: {member.filename!r}")
    # Every member was validated to land under `dest` in the loop above, so extractall is safe here.
    zip_file.extractall(dest)


class _HttpTransfer:
    """The real presigned-URL transfer over urllib — used by `main`, replaced by a fake in tests."""

    def upload(self, url: str, path: Path) -> None:
        import urllib.request

        request = urllib.request.Request(url, data=path.read_bytes(), method="PUT")  # noqa: S310
        # An explicit timeout keeps a stalled S3 connection from hanging past the poll loops' cap.
        urllib.request.urlopen(request, timeout=300).close()  # noqa: S310 - Device Farm presigned https URL

    def download(self, url: str, dest: Path) -> None:
        import io
        import urllib.request

        with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310 - Device Farm presigned https URL
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            _safe_extract(zf, dest)


if __name__ == "__main__":
    raise SystemExit(main())
