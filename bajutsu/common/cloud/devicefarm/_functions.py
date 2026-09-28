"""Submit a run to Device Farm, poll it to completion, and read Bajutsu's verdict back."""

from __future__ import annotations

import io
import json
import shlex
import stat
import time
import zipfile
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import yaml

from ._platform_run import _PlatformRun
from .device_farm_client import DeviceFarmClient
from .device_farm_error import DeviceFarmError
from .transfer import Transfer
from .verdict import Verdict

# Device Farm caps one custom-environment execution at 150 minutes; poll no longer than that before
# giving up rather than blocking a CI job indefinitely.
_HARD_CAP_SECONDS = 150 * 60
# Status polls back off exponentially: a small first wait so a short upload/run isn't blocked for a
# full fixed interval, doubling up to a ceiling so a long one still polls no faster than every 30s
# (gentle on the Device Farm API). `_POLL_INTERVAL_SECONDS` is that ceiling — the old fixed interval.
_POLL_INITIAL_SECONDS = 3
_POLL_INTERVAL_SECONDS = 30


Platform = Literal["android", "ios"]

# boto3 Device Farm upload types. The test package and spec use the Appium-Python
# custom-environment types on both platforms; only the app artifact differs — an Android APK
# (`.aab` is not accepted) or an iOS `.ipa`. Keying by `Platform` lets `mypy --strict` catch a
# missing or misspelt platform key here rather than silently widening to `dict[str, str]`.
APP_UPLOAD_TYPE: dict[Platform, str] = {"android": "ANDROID_APP", "ios": "IOS_APP"}
_UPLOAD_TEST_PACKAGE = "APPIUM_PYTHON_TEST_PACKAGE"
_UPLOAD_TEST_SPEC = "APPIUM_PYTHON_TEST_SPEC"

# Device Farm's DeviceFilter PLATFORM values (uppercase), keyed by Bajutsu's platform. Used to build
# the deviceSelectionConfiguration the serve fan-out path schedules with (see `device_selection_for`).
_DF_PLATFORM_ATTR: dict[Platform, str] = {"android": "ANDROID", "ios": "IOS"}


# Android resolves the single connected device through adb's `booted` alias; iOS has no such alias,
# so the XCUITest backend takes the reserved device's UDID that Device Farm exposes as
# $DEVICEFARM_DEVICE_UDID (double-quoted so the host expands it, and validated by the backend at run
# time). The probe is the platform's "is the device visible" check (the serial-resolution PoC).
_PLATFORM_RUN: dict[Platform, _PlatformRun] = {
    "android": _PlatformRun(backend="adb", udid="booted", probe="adb devices"),
    "ios": _PlatformRun(
        backend="xcuitest", udid='"$DEVICEFARM_DEVICE_UDID"', probe="xcrun xctrace list devices"
    ),
}

# Noise directories to keep out of the upload. `--package .=.` walks the whole checked-out
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

# Path component *strings* that must never enter the package even if they are not in _PACKAGE_EXCLUDES
# (which is an exact-match set). Any component whose string starts with one of these prefixes is
# excluded — e.g. `.env`, `.env.local`, and `.aws`. A `.env` at the checkout root holds live API
# keys; `.aws` holds credentials. Neither belongs on a shared Device Farm host.
_PACKAGE_EXCLUDE_PREFIXES = (".env", ".aws")


# ---------------------------------------------------------------------------
# Test spec
# ---------------------------------------------------------------------------


# --- Temporary Python 3.13 bootstrap (delete this block once Device Farm ships Python >= 3.13) ---
# Device Farm's custom-environment host tops out at Python 3.12 (`devicefarm-cli use python` only
# offers the runtimes Amazon preinstalls), but Bajutsu requires >= 3.13, so a native install fails
# with "requires a different Python". Until Device Farm ships 3.13, the install phase fetches a
# standalone interpreter with uv and runs Bajutsu from a venv on it. Removing the workaround is a
# single localized edit: delete `_python_bootstrap_commands` and these three constants, inline the
# native install below, and let `_BAJUTSU` fall back to a bare `bajutsu` on PATH.
_UV = "$HOME/.local/bin/uv"  # where `pip install --user uv` lands on the Device Farm host
_VENV = "$HOME/bajutsu-venv"
_BAJUTSU = f"{_VENV}/bin/bajutsu"


def _next_poll_delay(previous: float) -> float:
    """The next status-poll wait: double the previous one, capped at ``_POLL_INTERVAL_SECONDS``."""
    return min(previous * 2, _POLL_INTERVAL_SECONDS)


def _is_excluded(rel_parts: tuple[str, ...]) -> bool:
    """Return True if *rel_parts* names a path that must not enter the package.

    A component matches `_PACKAGE_EXCLUDES` exactly (e.g. `.git`) or starts with one of
    `_PACKAGE_EXCLUDE_PREFIXES` (e.g. `.env`, `.env.local`, `.aws`).
    """
    return any(
        part in _PACKAGE_EXCLUDES or part.startswith(_PACKAGE_EXCLUDE_PREFIXES)
        for part in rel_parts
    )


def _python_bootstrap_commands(python_version: str) -> list[str]:
    """Install-phase commands that provision `python_version` with uv and install Bajutsu under it.

    Temporary workaround for Device Farm having no Python >= 3.13 (see the block comment above):
    installs uv with the host's base pip, has uv fetch a standalone interpreter, and installs the
    uploaded test package into a venv on it. When Device Farm ships 3.13 this collapses back to the
    native three lines: ``devicefarm-cli use python {version}``, ``pip install --upgrade pip``,
    ``pip install "$DEVICEFARM_TEST_PACKAGE_PATH"``.
    """
    return [
        # $HOME is writable (the base pip already defaults to a --user install there).
        "python -m pip install --user --upgrade uv",
        f"{_UV} python install {shlex.quote(python_version)}",
        f"{_UV} venv --python {shlex.quote(python_version)} {_VENV}",
        # The test package unpacks into $DEVICEFARM_TEST_PACKAGE_PATH; install Bajutsu from it into
        # the 3.13 venv. The adb backend is pure subprocess, so the base install (no extras) suffices.
        f'{_UV} pip install --python {_VENV} "$DEVICEFARM_TEST_PACKAGE_PATH"',
    ]


def render_test_spec(
    scenarios: Sequence[str],
    *,
    target: str,
    config: str,
    platform: Platform = "android",
    python_version: str = "3.13",
    pre_test_commands: Sequence[str] = (),
) -> str:
    """Render a Device Farm custom-environment test spec that runs the given scenarios.

    The `test` phase runs one `bajutsu run` per scenario against the host's reserved device — over
    the adb backend on Android (serial ``booted``) or the XCUITest backend on iOS (the
    ``$DEVICEFARM_DEVICE_UDID`` the host exposes) — so a scenario that fails still leaves a manifest
    for the others; the `post_test` phase copies the whole ``runs/`` tree into ``$DEVICEFARM_LOG_DIR``
    so `list_artifacts` can return it.

    Args:
        scenarios: Scenario file paths as they appear inside the unpacked test package. A bare `str`
            is rejected (see Raises): `Sequence[str]` matches one, but a non-empty string is truthy
            (so the empty-`scenarios` guard below never catches it) and would splice one `--scenario`
            command per character.
        target: The `targets.<name>` config entry the scenarios run against.
        config: The Bajutsu config path inside the unpacked test package.
        platform: Which reserved-device platform to target (`_PLATFORM_RUN` picks the backend, the
            ``--udid`` argument, and the visibility probe).
        python_version: The Python uv provisions for the run (see `_python_bootstrap_commands`).
        pre_test_commands: Extra shell commands appended verbatim to the `pre_test` phase, in order,
            after the visibility probe — a caller's hook for device-side setup its own backend needs
            before the run (a network relay, a VPN client), keeping that per-deployment setup outside
            `bajutsu/` (BE-0432). Each entry is treated as an opaque, already-shell-safe string and is
            spliced through unquoted, like `build_package`'s `extra_texts`: the whole test spec is a
            shell trust boundary, and the entry is the caller's own construction, not the
            request-sourced text `render_test_spec` quotes into the `bajutsu run` command. It is a
            Python-API-only hook for that reason — no `serve`, config, or `BatchRequest` field wires
            to it, so no client-supplied value reaches a shell on the host holding the run's AWS role.
            A bare `str` is rejected (see Raises): `Sequence[str]` matches one, but a single command
            passed as a string would splice one `pre_test` command per character.

    Raises:
        ValueError: If `scenarios` is empty — a spec that runs nothing would silently "pass".
        TypeError: If `scenarios` or `pre_test_commands` is a bare `str`, which `Sequence[str]`
            matches but would splice one command per character.
    """
    # `Sequence[str]` also matches a bare `str`, which `mypy --strict` accepts; iterating one emits a
    # command per character, so both sequence parameters reject it loudly rather than render a spec
    # whose every `bajutsu run` (or setup command) is a single-character fragment. Checked before the
    # emptiness guard below: a non-empty string is truthy, so that guard alone would not catch it.
    if isinstance(scenarios, str):
        raise TypeError("scenarios must be a sequence of scenario paths, not a single string")
    if isinstance(pre_test_commands, str):
        raise TypeError("pre_test_commands must be a sequence of commands, not a single string")
    if not scenarios:
        raise ValueError("cannot render a test spec with no scenario to run")
    run = _PLATFORM_RUN[platform]
    # `target`, `config`, and each scenario path trace back to workflow_dispatch text inputs, so
    # quote every splice: an unescaped space or shell metacharacter would otherwise break argument
    # parsing or inject a command onto the Device Farm host running under the OIDC-minted AWS role.
    # `run.backend` / `run.udid` are fixed, code-controlled tokens (not user input), so they are not
    # quoted — the iOS `udid` intentionally carries the shell reference the host must expand.
    run_cmds = [
        f"{_BAJUTSU} run"
        f" --scenario {shlex.quote(s)} --target {shlex.quote(target)}"
        f" --config {shlex.quote(config)} --backend {run.backend} --udid {run.udid}"
        for s in scenarios
    ]
    spec: dict[str, Any] = {
        "version": 0.1,
        "phases": {
            "install": {"commands": _python_bootstrap_commands(python_version)},
            "pre_test": {
                "commands": [
                    # Prove the reserved device is visible before running (the serial-resolution PoC).
                    run.probe,
                    # Caller-supplied device-side setup runs after the probe (BE-0432); empty by default.
                    *pre_test_commands,
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


def build_package(
    entries: Sequence[tuple[Path, str]],
    out_zip: Path,
    *,
    extra_texts: Mapping[str, str] | None = None,
    exclude_arcnames: Collection[str] = (),
) -> Path:
    """Bundle the Bajutsu payload into `out_zip` for upload, one `(source, arcname)` pair per entry.

    A directory source is added recursively under its arcname; the arcname `.` packs the directory
    *at the zip root* (no prefix). A file source is added at its arcname. Paths under a
    `_PACKAGE_EXCLUDES` component (VCS/build/cache/scratch noise such as `.git` and `.venv`),
    symlinks, and the output archive itself are skipped. `extra_texts` maps an arcname to text
    content written verbatim into the archive — used to synthesize files Device Farm's validation
    requires but the repo does not carry (a root `requirements.txt`). `exclude_arcnames` lists
    arcnames to skip during the walk so `extra_texts` can overlay them without creating duplicate
    zip entries. Returns `out_zip`.

    Raises:
        DeviceFarmError: If any source path does not exist — an incomplete package would fail
            opaquely on the Device Farm host, so fail here instead.
    """
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    # `--out` commonly lands inside a packaged source (its default sits in the repo root that
    # `--package .=.` walks). Skip the archive by resolved path so it never zips itself —
    # doing so reads back its own growing bytes and balloons the upload without bound.
    out_resolved = out_zip.resolve()
    excluded = set(exclude_arcnames)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for source, arcname in entries:
            if not source.exists():
                raise DeviceFarmError(f"package source not found: {source}")
            if source.is_dir():
                # `.` packs the directory at the zip root (no prefix); any other arcname nests
                # under it. Device Farm's APPIUM_PYTHON_TEST_PACKAGE validation needs the repo's
                # tests/ (and pyproject.toml for `pip install`) at the root, hence `--package .=.`.
                prefix = "" if arcname == "." else f"{arcname}/"
                for path in sorted(source.rglob("*")):
                    rel = path.relative_to(source)
                    # Skip noise dirs, credential files (`.env`, `.aws`), and symlinks — so
                    # `--package .=.` on the checkout root neither bloats the upload nor leaks a
                    # secret to the shared Device Farm host (mirrors `archive_run_dir`).
                    if _is_excluded(rel.parts):
                        continue
                    if path.resolve() == out_resolved:
                        continue
                    if path.is_file() and not path.is_symlink():
                        arc = f"{prefix}{rel.as_posix()}"
                        if arc not in excluded:
                            zf.write(path, arc)
            else:
                if arcname not in excluded:
                    zf.write(source, arcname)
        for arc, text in (extra_texts or {}).items():
            zf.writestr(arc, text)
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
    delay: float = _POLL_INITIAL_SECONDS
    while True:
        upload = client.get_upload(arn=created["arn"])["upload"]
        status = upload["status"]
        if status == "SUCCEEDED":
            return str(created["arn"])
        if status == "FAILED":
            # Surface Device Farm's own reason (message/metadata). A bare "upload failed" leaves the
            # operator guessing at an opaque, remote validation failure they cannot reproduce locally.
            reason = upload.get("message") or upload.get("metadata") or "(no reason reported)"
            raise DeviceFarmError(f"upload failed on Device Farm: {name}: {reason}")
        if time.monotonic() >= deadline:
            raise DeviceFarmError(f"upload did not complete within the 150-minute cap: {name}")
        sleep(delay)
        delay = _next_poll_delay(delay)


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
    delay: float = _POLL_INITIAL_SECONDS
    while client.get_run(arn=run_arn)["run"]["status"] != "COMPLETED":
        if time.monotonic() >= deadline:
            raise DeviceFarmError("run did not complete within the 150-minute cap")
        sleep(delay)
        delay = _next_poll_delay(delay)


def device_selection_for(platform: Platform, *, max_devices: int = 1) -> dict[str, Any]:
    """Build a ``deviceSelectionConfiguration`` that reserves `max_devices` of `platform` per run.

    ScheduleRun takes *either* a static ``devicePoolArn`` (which runs on every device in the pool) *or*
    a ``deviceSelectionConfiguration`` whose ``maxDevices`` bounds how many of the matching devices one
    run reserves — the two are mutually exclusive. BE-0336's serve fan-out needs one device per run so
    the Bajutsu-side budget `K` alone governs the device count, so it schedules with this selection
    (``maxDevices`` defaulting to one) rather than a pool. The only filter in this version is the
    platform; device-type filters (OS version, model) are deliberately out of scope.
    """
    return {
        "filters": [
            {"attribute": "PLATFORM", "operator": "EQUALS", "values": [_DF_PLATFORM_ATTR[platform]]}
        ],
        "maxDevices": max_devices,
    }


def submit_and_collect(
    client: DeviceFarmClient,
    transfer: Transfer,
    *,
    project_arn: str,
    device_pool_arn: str | None = None,
    device_selection: Mapping[str, Any] | None = None,
    app_path: Path,
    package_zip: Path,
    spec_yaml: Path,
    dest: Path,
    app_upload_type: str = APP_UPLOAD_TYPE["android"],
    run_name: str = "bajutsu",
    sleep: Callable[[float], None] = time.sleep,
    on_scheduled: Callable[[str], None] | None = None,
) -> Verdict:
    """Upload the payload, schedule the run, wait for it, download artifacts, and return the verdict.

    Uploads the app artifact, the test package, and the test spec (each a create-upload + presigned
    PUT + poll), schedules a run wiring the three together, waits for completion, downloads the
    customer artifacts into `dest`, and derives the verdict from the returned ``manifest.json`` tree —
    always Bajutsu's verdict, never Device Farm's classification.

    Provide exactly one of `device_pool_arn` (the batch submitter's static pool — every device in it
    runs the suite) or `device_selection` (a ``deviceSelectionConfiguration`` from
    `device_selection_for`, which the serve fan-out uses to reserve a single device per run). The two
    are mutually exclusive in ScheduleRun.

    Args:
        app_upload_type: The Device Farm upload type for the app artifact (``ANDROID_APP`` for an
            `.apk`, ``IOS_APP`` for an `.ipa`) — Device Farm rejects a mismatched artifact.
        on_scheduled: Called with the run ARN the moment the run is scheduled, before the long poll —
            serve persists it so a worker that re-leases the job after a restart resumes that run
            through `collect_run` instead of resubmitting (BE-0336 Unit 5).

    Raises:
        DeviceFarmError: If neither or both of `device_pool_arn` / `device_selection` are given, if any
            upload fails, or if the run does not complete within the hard cap.
    """
    if (device_pool_arn is None) == (device_selection is None):
        raise DeviceFarmError(
            "schedule a run with exactly one of device_pool_arn or device_selection"
        )
    app_arn = _upload_one(
        client,
        transfer,
        project_arn=project_arn,
        name=app_path.name,
        upload_type=app_upload_type,
        path=app_path,
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
    # ScheduleRun accepts the static pool or the selection, never both (validated above).
    where = (
        {"devicePoolArn": device_pool_arn}
        if device_pool_arn is not None
        else {"deviceSelectionConfiguration": device_selection}
    )
    scheduled = client.schedule_run(
        projectArn=project_arn,
        appArn=app_arn,
        name=run_name,
        test={"type": "APPIUM_PYTHON", "testPackageArn": package_arn, "testSpecArn": spec_arn},
        **where,
    )
    run_arn = scheduled["run"]["arn"]
    # Persist the run ARN before the long poll, so a restart mid-poll resumes this run rather than
    # orphaning it and scheduling a new one (BE-0336 Unit 5).
    if on_scheduled is not None:
        on_scheduled(run_arn)
    return collect_run(client, transfer, run_arn=run_arn, dest=dest, sleep=sleep)


def collect_run(
    client: DeviceFarmClient,
    transfer: Transfer,
    *,
    run_arn: str,
    dest: Path,
    sleep: Callable[[float], None] = time.sleep,
) -> Verdict:
    """Wait for an already-scheduled run, download its artifacts under `dest`, and return the verdict.

    Split from `submit_and_collect` so a resumed cloud-batch job (BE-0336 Unit 5) polls and collects a
    run scheduled before a restart — reached from the persisted run ARN — without re-uploading or
    rescheduling. The verdict is Bajutsu's own, read from the downloaded ``manifest.json`` tree.

    Raises:
        DeviceFarmError: If the run does not complete within the 150-minute hard cap.
    """
    _wait_run(client, run_arn, sleep=sleep)
    dest.mkdir(parents=True, exist_ok=True)
    for index, artifact in enumerate(client.list_artifacts(arn=run_arn, type="FILE")["artifacts"]):
        _store_artifact(artifact, transfer.download(artifact["url"]), dest, index=index)
    return verdict_from_manifest(dest)


def _store_artifact(artifact: Mapping[str, Any], payload: bytes, dest: Path, *, index: int) -> None:
    """Store one downloaded Device Farm artifact under `dest`, extracting a zip and writing the rest.

    ``list_artifacts(type="FILE")`` mixes the CUSTOMER_ARTIFACT zip (the ``runs/`` tree holding the
    manifests the verdict reads) with plain-file artifacts — device and test-spec logs, screenshots.
    Only the zip is an archive; feeding the rest to `zipfile` raises ``BadZipFile`` and aborts the
    whole collection. A ``"zip"`` extension is extracted with the zip-slip guard; anything else is
    written verbatim under ``dest/logs/`` so it is on hand for diagnostics (e.g. the pre_test
    ``adb devices`` output) without touching the manifest tree the verdict globs. `index` prefixes
    the log filename because Device Farm artifact names are not unique across a run's jobs.
    """
    if artifact.get("extension") == "zip":
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            _safe_extract(zf, dest)
        return
    logs = dest / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    # Both `name` and `extension` come from the (untrusted) Device Farm artifact and are spliced into
    # one filename, so strip path separators from each — a `/` or `\` in either would otherwise let the
    # write escape `dest/logs/`.
    name = str(artifact.get("name") or "artifact").replace("/", "_").replace("\\", "_")
    extension = str(artifact.get("extension") or "txt").replace("/", "_").replace("\\", "_")
    (logs / f"{index:03d}-{name}.{extension}").write_bytes(payload)


def _safe_extract(zip_file: zipfile.ZipFile, dest: Path) -> None:
    """Extract every member of *zip_file* into *dest*, confining each to *dest* (zip-slip guard).

    The artifact zip comes from Device Farm's presigned URL; a member with a ``../`` or absolute
    name would otherwise let ``extractall`` write outside *dest*, and a symlink member could point
    outside *dest* too. Each member is resolved and checked to land strictly under *dest*, and any
    symlink member is rejected outright, before extracting (mirrors `serve.uploads.extract_bundle`).

    Raises:
        DeviceFarmError: If any member resolves outside *dest* or is a symlink — fail loud rather
            than write astray.
    """
    dest_root = dest.resolve()
    for member in zip_file.infolist():
        # A symlink member could point outside `dest`; reject it outright (mirrors how
        # `serve.uploads.extract_bundle` reads the Unix mode bits in `external_attr >> 16`).
        if stat.S_ISLNK(member.external_attr >> 16):
            raise DeviceFarmError(f"symlink in Device Farm artifact: {member.filename!r}")
        target = (dest / member.filename).resolve()
        if target != dest_root and dest_root not in target.parents:
            raise DeviceFarmError(f"unsafe path in Device Farm artifact: {member.filename!r}")
    # Every member was validated to land under `dest` in the loop above, so extractall is safe here.
    zip_file.extractall(dest)
