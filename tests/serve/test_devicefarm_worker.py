"""A worker whose `worker.yaml` environment is `devicefarm` runs the Device Farm dispatch (BE-0448).

Units 1 to 3: the worker registers the provider and advertises `environment:devicefarm`, the server
registers none and refuses to run a fan-out itself, and a leased batch job is packaged on the worker
from the source tree plus its own bundle directory, with paths and the application reference relative
to that bundle. Everything runs against the in-memory Device Farm fake; no AWS call is made.
"""

from __future__ import annotations

import io
import json
import plistlib
import re
import tomllib
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
import typer
import yaml
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.common.backend_cli import simctl
from bajutsu.common.capability.route_tokens import is_route_token
from bajutsu.common.capability.worker_capability import WorkerCapability, parse_worker_capability
from bajutsu.common.cloud.devicefarm import DeviceFarmError, Platform, Verdict
from bajutsu.serve import batch_bootstrap
from bajutsu.serve import batch_provider as bp
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.executor import UnqueuedExecutor
from bajutsu.serve.logbus import InMemoryLogBus
from bajutsu.serve.operations.dispatch import start_run_set
from bajutsu.serve.server import one_shot
from bajutsu.serve.server.worker_job import execute_job_spec
from bajutsu.serve.state import ServeState

runner = CliRunner()

_SCENARIO = "- name: a\n  steps:\n    - tap: { id: x }\n"


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    # The provider registry is process-global; restore it so a registration never leaks.
    saved = dict(bp._PROVIDERS)
    try:
        yield
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)


def _zip_bytes(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buffer.getvalue()


class _FakeClient:
    """The happy path of the Device Farm client, counting the runs it schedules."""

    def __init__(self) -> None:
        self.schedule_calls = 0
        self.upload_types: list[str] = []
        self.test_type: str | None = None

    def create_upload(self, *, projectArn: str, name: str, type: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        self.upload_types.append(type)
        return {"upload": {"arn": f"arn:upload/{name}", "url": f"https://s3/{name}"}}

    def get_upload(self, *, arn: str) -> dict[str, Any]:
        return {"upload": {"arn": arn, "status": "SUCCEEDED"}}

    def schedule_run(self, **kwargs: Any) -> dict[str, Any]:
        self.schedule_calls += 1
        self.test_type = kwargs["test"]["type"]
        return {"run": {"arn": f"arn:run/{self.schedule_calls}"}}

    def get_run(self, *, arn: str) -> dict[str, Any]:
        return {"run": {"arn": arn, "status": "COMPLETED"}}

    def list_artifacts(self, *, arn: str, type: str) -> dict[str, Any]:
        return {"artifacts": [{"name": "runs", "extension": "zip", "url": "https://s3/runs.zip"}]}

    def stop_run(self, *, arn: str) -> dict[str, Any]:
        return {"run": {"arn": arn, "status": "STOPPING"}}


class _CapturingTransfer:
    """Keeps every uploaded file's bytes (the staging directory is gone once submit returns)."""

    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}

    def upload(self, url: str, path: Path) -> None:
        self.uploads[url] = path.read_bytes()

    def download(self, url: str) -> bytes:
        manifest = json.dumps({"ok": True, "scenarios": [{"scenario": "a", "ok": True}]})
        return _zip_bytes({"runs/20260101-1/manifest.json": manifest})

    def package(self) -> zipfile.ZipFile:
        (data,) = (v for k, v in self.uploads.items() if k.endswith("devicefarm-package.zip"))
        return zipfile.ZipFile(io.BytesIO(data))


def _spec_phases(transfer: _CapturingTransfer) -> dict[str, Any]:
    """The phases of the one test spec a submit uploaded."""
    (spec,) = (v for k, v in transfer.uploads.items() if k.endswith("testspec.yml"))
    phases: dict[str, Any] = yaml.safe_load(spec)["phases"]
    return phases


def _source_tree(root: Path) -> Path:
    """A stand-in for Bajutsu's checkout: the parts a package takes from it, plus noise."""
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text("", encoding="utf-8")
    (root / "bajutsu").mkdir()
    (root / "bajutsu" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='bajutsu'\n", encoding="utf-8")
    for name in ("DESIGN.md", "LICENSE", "NOTICE"):
        (root / name).write_text("", encoding="utf-8")
    (root / "README.md").write_text("not packaged", encoding="utf-8")
    return root


def _provider(
    source_root: Path | None,
) -> tuple[bp.DeviceFarmBatchProvider, _FakeClient, _CapturingTransfer]:
    client, transfer = _FakeClient(), _CapturingTransfer()
    provider = bp.DeviceFarmBatchProvider(
        client=client,
        transfer=transfer,
        project_arn="arn:project/1",
        sleep=lambda _: None,
        source_root=source_root,
    )
    return provider, client, transfer


def _bundle(root: Path) -> Path:
    """A job's bundle directory: config, one scenario, the binary, and a stray earlier run."""
    (root / "scenarios").mkdir(parents=True)
    (root / "scenarios" / "one.yaml").write_text(_SCENARIO, encoding="utf-8")
    (root / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    (root / "app.apk").write_bytes(b"APK")
    (root / "runs" / "old").mkdir(parents=True)
    (root / "runs" / "old" / "manifest.json").write_text("{}", encoding="utf-8")
    return root


def _ios_bundle(root: Path, *, runner: str | None = "products/Runner.xctestrun") -> Path:
    """An iOS job's bundle: a device target, its `.ipa`, and the runner build `testRunner` names."""
    (root / "scenarios").mkdir(parents=True)
    (root / "scenarios" / "one.yaml").write_text(_SCENARIO, encoding="utf-8")
    driver = f"    driver: {{ runner: {{ testRunner: {runner} }} }}\n" if runner else ""
    (root / "bajutsu.config.yaml").write_text(
        "targets:\n  demo:\n    platform: ios\n    subject: { id: com.example, path: app.ipa }\n"
        f"    runsOn: {{ ios: {{ kind: device }} }}\n{driver}",
        encoding="utf-8",
    )
    (root / "app.ipa").write_bytes(b"IPA")
    products = root / "products"
    runner_app = products / "Debug-iphoneos" / "Runner.app"
    (runner_app / "PlugIns" / "UITests.xctest").mkdir(parents=True)
    (runner_app / "Runner").write_bytes(b"MACHO")
    (runner_app / "PlugIns" / "UITests.xctest" / "UITests").write_bytes(b"MACHO")
    (products / "Debug-iphoneos" / "Host.app").mkdir()
    (products / "Debug-iphoneos" / "Host.app" / "Host").write_bytes(b"MACHO")
    (products / "Runner.xctestrun").write_bytes(
        plistlib.dumps(
            {
                "UITests": {
                    "TestHostPath": "__TESTROOT__/Debug-iphoneos/Runner.app",
                    "UITargetAppPath": "__TESTROOT__/Debug-iphoneos/Host.app",
                },
                "__xctestrun_metadata__": {"FormatVersion": 1},
            }
        )
    )
    return root


def _ios_request() -> bp.BatchRequest:
    return replace(_request("app.ipa"), platform="ios")


def _request(app_path: str = "app.apk") -> bp.BatchRequest:
    return bp.BatchRequest(
        provider="devicefarm",
        scenario="scenarios/one.yaml",
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path=app_path,
    )


# --- unit 1: the worker registers the provider; the server does not -----------------------------


def test_a_device_farm_worker_registers_its_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVICEFARM_PROJECT_ARN", "arn:aws:devicefarm:us-west-2:1:project:p")
    monkeypatch.setattr(batch_bootstrap, "_make_devicefarm_client", lambda _region: object())
    worker_mod._register_environment_or_exit("devicefarm")
    provider = bp.resolve("devicefarm")
    assert isinstance(provider, bp.DeviceFarmBatchProvider)
    # The package is composed from the checkout this worker runs from.
    assert provider._source_root == batch_bootstrap.bajutsu_source_root()


def test_a_local_worker_registers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVICEFARM_PROJECT_ARN", "arn:aws:devicefarm:us-west-2:1:project:p")
    bp._PROVIDERS.clear()
    worker_mod._register_environment_or_exit("local")
    assert bp._PROVIDERS == {}


def test_a_provider_that_cannot_be_built_stops_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVICEFARM_PROJECT_ARN", "arn:aws:devicefarm:us-west-2:1:project:p")
    monkeypatch.setenv("BAJUTSU_BATCH_HOOKS", "not-a-hook-path")
    with pytest.raises(typer.BadParameter, match="cannot register its provider"):
        worker_mod._register_environment_or_exit("devicefarm")


def test_registration_needs_a_source_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    # An installed Bajutsu carries no pyproject.toml or tests/, so no job's package could validate.
    monkeypatch.setattr(batch_bootstrap, "bajutsu_source_root", lambda: None)
    monkeypatch.setattr(batch_bootstrap, "_make_devicefarm_client", lambda _region: object())
    with pytest.raises(DeviceFarmError, match="source checkout"):
        batch_bootstrap.register_batch_providers(
            {"DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:us-west-2:1:project:p"}
        )


def _worker(environment: str) -> WorkerCapability:
    return WorkerCapability(version=1, environment=environment, drivers=("xcuitest", "adb"))


def test_a_device_farm_worker_advertises_its_environment_and_host_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BAJUTSU_WORKER_CAPABILITIES", raising=False)
    # No platform axis: the phone is the provider's. The host token is the machine's (BE-0450).
    assert worker_mod._advertised_capabilities("", "", _worker("devicefarm"), "linux") == [
        "environment:devicefarm",
        "host:linux",
    ]


def test_a_device_farm_worker_refuses_an_explicit_platform() -> None:
    with pytest.raises(typer.BadParameter, match="environment is 'devicefarm'"):
        worker_mod._advertised_capabilities("android", "", _worker("devicefarm"), "linux")


def test_a_local_worker_defaults_to_ios(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAJUTSU_WORKER_CAPABILITIES", raising=False)
    # No Simulator inventory is probed, so the platform axis and the host alone are advertised.
    monkeypatch.setattr(simctl, "real_run", None)
    caps = worker_mod._advertised_capabilities("", "", _worker("local"), "darwin")
    assert [c for c in caps if not is_route_token(c)] == ["host:darwin", "platform:ios"]
    # The Simulator route's tokens ride along (BE-0467).
    assert "cap:ios-simulator:query" in caps


@pytest.mark.parametrize("environment", ["local", "devicefarm"])
def test_the_environment_prefix_is_reserved_in_the_override(
    environment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(typer.BadParameter, match="reserved"):
        worker_mod._advertised_capabilities(
            "", "environment:devicefarm", _worker(environment), "darwin"
        )
    monkeypatch.setenv("BAJUTSU_WORKER_CAPABILITIES", "ios18,environment:x")
    with pytest.raises(typer.BadParameter, match="reserved"):
        worker_mod._advertised_capabilities("", "", _worker(environment), "darwin")


def test_a_server_without_a_queue_refuses_a_fan_out(tmp_path: Path) -> None:
    # No provider is registered in the server and it runs no job itself, so a fan-out needs a queue.
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text("targets: {}\n", encoding="utf-8")
    state = ServeState(config=cfg, runs_dir=tmp_path / "runs", cwd=tmp_path)
    assert isinstance(state.executor, UnqueuedExecutor)
    payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})
    assert status == 400
    assert "needs a queue and a worker" in payload["error"]


# --- unit 3: the package is composed per job, from the source tree and the bundle ---------------


def test_the_package_holds_the_source_parts_and_the_bundle(tmp_path: Path) -> None:
    source = _source_tree(tmp_path / "src")
    before = sorted(p.relative_to(source) for p in source.rglob("*"))
    bundle = _bundle(tmp_path / "bundle")
    provider, client, transfer = _provider(source)

    verdict = provider.submit(_request(), work_dir=bundle, dest=tmp_path / "dest")

    assert verdict.ok and client.schedule_calls == 1
    names = set(transfer.package().namelist())
    assert {"pyproject.toml", "tests/test_x.py", "bajutsu/__init__.py"} <= names
    assert {"bundle/bajutsu.config.yaml", "bundle/scenarios/one.yaml"} <= names
    # Nothing outside the three source parts, no earlier run, and no second copy of the binary.
    assert "README.md" not in names
    assert not any(n.startswith("bundle/runs/") for n in names)
    assert "bundle/app.apk" not in names
    # The binary went up as the app upload instead.
    assert any(url.endswith("app.apk") for url in transfer.uploads)
    # The test spec runs the scenario from inside the bundle.
    (run,) = _spec_phases(transfer)["test"]["commands"]
    assert "--scenario bundle/scenarios/one.yaml" in run
    assert "--config bundle/bajutsu.config.yaml" in run
    # Neither tree is written into.
    assert sorted(p.relative_to(source) for p in source.rglob("*")) == before
    assert not (bundle / "testspec.yml").exists()


@pytest.mark.parametrize(
    ("platform", "driver", "worker_file"),
    [
        ("android", "adb", "devicefarm-host.worker.yaml"),
        ("ios", "xcuitest", "bajutsu-package/devicefarm-host.worker.yaml"),
    ],
)
def test_every_run_takes_the_device_farm_installed_app(
    tmp_path: Path, platform: Platform, driver: str, worker_file: str
) -> None:
    # Device Farm installs the app upload before the test spec runs, so the run launches that build
    # through a `devicefarm-host` worker file rather than installing `subject.path` (BE-0465).
    source = _source_tree(tmp_path / "src")
    if platform == "ios":
        bundle, request = _ios_bundle(tmp_path / "bundle"), _ios_request()
    else:
        bundle, request = _bundle(tmp_path / "bundle"), _request()
    provider, _client, transfer = _provider(source)

    provider.submit(request, work_dir=bundle, dest=tmp_path / "dest")

    package = transfer.package()
    worker = parse_worker_capability(package.read(worker_file).decode())
    assert worker.environment == "devicefarm-host" and worker.drivers == (driver,)
    runs = _spec_phases(transfer)["test"]["commands"]
    assert runs and all(c.endswith("--worker-config devicefarm-host.worker.yaml") for c in runs)


def test_an_android_run_stays_an_appium_python_test(tmp_path: Path) -> None:
    provider, client, _transfer = _provider(_source_tree(tmp_path / "src"))
    provider.submit(_request(), work_dir=_bundle(tmp_path / "bundle"), dest=tmp_path / "dest")
    assert client.upload_types == [
        "ANDROID_APP",
        "APPIUM_PYTHON_TEST_PACKAGE",
        "APPIUM_PYTHON_TEST_SPEC",
    ]
    assert client.test_type == "APPIUM_PYTHON"


# macOS hands the worker a /var/folders work dir, a symlink to /private/var/folders.
@pytest.mark.parametrize("through_symlink", [False, True])
def test_an_ios_run_ships_its_runner_for_device_farm_to_re_sign(
    tmp_path: Path, through_symlink: bool
) -> None:
    # An APPIUM_PYTHON package keeps the runner as signed, which a Device Farm iPhone refuses unless
    # its profile lists that device; an XCTEST_UI package has Device Farm re-sign it (BE-0465).
    work_dir = _ios_bundle(tmp_path / "bundle")
    if through_symlink:
        (tmp_path / "link").symlink_to(work_dir)
        work_dir = tmp_path / "link"
    provider, client, transfer = _provider(_source_tree(tmp_path / "src"))

    verdict = provider.submit(_ios_request(), work_dir=work_dir, dest=tmp_path / "dest")

    assert verdict.ok
    assert client.upload_types == ["IOS_APP", "XCTEST_UI_TEST_PACKAGE", "XCTEST_UI_TEST_SPEC"]
    assert client.test_type == "XCTEST_UI"
    names = set(transfer.package().namelist())
    # The runner app sits at the package root, where Device Farm looks for it.
    assert {"Runner.app/Runner", "Runner.app/PlugIns/UITests.xctest/UITests"} <= names
    # Bajutsu and the bundle sit beside it, with neither the binary nor the build Device Farm replaces.
    assert {
        "bajutsu-package/pyproject.toml",
        "bajutsu-package/bajutsu/__init__.py",
        "bajutsu-package/bundle/bajutsu.config.yaml",
        "bajutsu-package/bundle/scenarios/one.yaml",
    } <= names
    assert not any(n.startswith("bajutsu-package/bundle/products/") for n in names)
    assert "bajutsu-package/bundle/app.ipa" not in names
    pre_test = " ".join(_spec_phases(transfer)["pre_test"]["commands"])
    assert '"$HOME/bajutsu-work"/bundle/products/Runner.xctestrun' in pre_test


def test_an_ios_target_without_a_runner_build_fails_before_any_upload(tmp_path: Path) -> None:
    provider, client, transfer = _provider(_source_tree(tmp_path / "src"))
    with pytest.raises(DeviceFarmError, match=r"driver\.runner\.testRunner"):
        provider.submit(
            _ios_request(),
            work_dir=_ios_bundle(tmp_path / "bundle", runner=None),
            dest=tmp_path / "dest",
        )
    assert client.schedule_calls == 0 and transfer.uploads == {}


def test_a_runner_build_missing_its_runner_app_fails_before_any_upload(tmp_path: Path) -> None:
    bundle = _ios_bundle(tmp_path / "bundle")
    for path in sorted((bundle / "products" / "Debug-iphoneos" / "Runner.app").rglob("*"))[::-1]:
        path.unlink() if path.is_file() else path.rmdir()
    (bundle / "products" / "Debug-iphoneos" / "Runner.app").rmdir()
    provider, client, transfer = _provider(_source_tree(tmp_path / "src"))
    with pytest.raises(DeviceFarmError, match=r"Runner\.app"):
        provider.submit(_ios_request(), work_dir=bundle, dest=tmp_path / "dest")
    assert client.schedule_calls == 0 and transfer.uploads == {}


def test_an_injected_launch_env_overlays_the_bundled_config(tmp_path: Path) -> None:
    class _Inject(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            ctx.launch_env["TOKEN"] = "t"

    source = _source_tree(tmp_path / "src")
    bundle = _bundle(tmp_path / "bundle")
    client, transfer = _FakeClient(), _CapturingTransfer()
    provider = bp.DeviceFarmBatchProvider(
        client=client,
        transfer=transfer,
        project_arn="arn:project/1",
        sleep=lambda _: None,
        hooks=[_Inject()],
        source_root=source,
    )
    provider.submit(_request(), work_dir=bundle, dest=tmp_path / "dest")
    package = transfer.package()
    assert package.namelist().count("bundle/bajutsu.config.yaml") == 1
    assert "TOKEN: t" in package.read("bundle/bajutsu.config.yaml").decode()
    assert (bundle / "bajutsu.config.yaml").read_text(encoding="utf-8") == "targets: {}\n"


def test_an_application_missing_from_the_bundle_fails_before_any_upload(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    provider, client, transfer = _provider(None)
    with pytest.raises(DeviceFarmError, match="not in this job's bundle"):
        provider.submit(_request("missing.apk"), work_dir=bundle, dest=tmp_path / "dest")
    assert client.schedule_calls == 0 and transfer.uploads == {}


def _storage_state(
    tmp_path: Path, executor: Any, *, app_path: str = "app.apk", store: bool = True
) -> ServeState:
    """A server whose scenario store ships scenario text as materials, as the hosted store does.

    The config's directory holds the binary its `appPath` names, and the server has an object store
    to deliver it through unless *store* is False.
    """
    from unittest import mock

    from _shared import queue_repository
    from conftest import FakeObjectStore

    from bajutsu.serve.scenarios import Runnable

    cfg = tmp_path / "server" / "bajutsu.config.yaml"
    cfg.parent.mkdir()
    cfg.write_text(
        (
            "targets:\n"
            "  demo:\n"
            "    platform: android\n"
            f"    subject: {{id: com.example.demo, path: {app_path}}}\n"
            f"    paths: {{scenarios: scenarios}}\n"
        ),
        encoding="utf-8",
    )
    (cfg.parent / "app.apk").write_bytes(b"APK")

    class _Scope:
        def list(self) -> list[dict[str, str]]:
            return [{"file": "one.yaml"}]

        def runnable(self, name: str) -> Runnable:
            return Runnable(arg=f"scenarios/{name}", materials={f"scenarios/{name}": _SCENARIO})

    class _Store:
        def scope(self, *_a: Any, **_k: Any) -> _Scope:
            return _Scope()

    state = ServeState(
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=cfg.parent,
        executor=executor,
        repository=queue_repository(),
    )
    if store:
        state.object_store = FakeObjectStore()
    bundle = mock.MagicMock()
    bundle.scenarios = _Store()
    state.for_org = lambda *_a, **_k: bundle  # type: ignore[method-assign]
    return state


class _Recording:
    def __init__(self) -> None:
        self.jobs: list[Any] = []

    def dispatch(self, _state: ServeState, job: Any) -> None:
        self.jobs.append(job)


def test_a_leased_fan_out_job_runs_on_the_worker_from_its_own_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole unit-3 path: the server queues a job whose scenario travels as materials and stores
    # the binary, the worker gives the job a workspace of its own and places the binary the lease's
    # signed URL delivers, and the provider packages that workspace and lands the run there.
    import hashlib

    from bajutsu.serve.server.worker_job import job_spec

    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    executor = _Recording()
    state = _storage_state(tmp_path, executor)
    _payload, status = start_run_set(state, {"target": "demo", "environment": "devicefarm"})
    assert status == 200
    (job,) = executor.jobs
    spec = job_spec(job)
    assert spec["batch"]["app_path"] == "app.apk"
    assert spec["cwd"] is None  # it ships its files, so it names nothing on the server's disk
    assert job.capabilities == ["environment:devicefarm"]
    assert spec["batch_app"] == hashlib.sha256(b"APK").hexdigest()
    assert state.object_store is not None
    (key,) = state.object_store.objects  # type: ignore[attr-defined]
    assert key.endswith(f"binary/{spec['batch_app']}")

    fetched: list[str] = []

    def fake_get(url: str, dest: Path, *, timeout: float | None = None) -> None:
        fetched.append(url)
        dest.write_bytes(b"APK")

    monkeypatch.setattr(worker_mod, "_get_file", fake_get)
    work = tmp_path / "worker"
    work.mkdir()
    (work / "stray.txt").write_text("another job's file", encoding="utf-8")
    job_work, failure = worker_mod._workspace_or_failure(
        work, spec, None, app_url="https://signed/app"
    )
    assert failure is None
    assert job_work == work / ".batch" / job.id
    assert fetched == ["https://signed/app"]
    assert (job_work / "app.apk").read_bytes() == b"APK"
    source = _source_tree(tmp_path / "src")
    provider, client, transfer = _provider(source)
    bp.register("devicefarm", provider)

    ran = execute_job_spec(spec, cwd=job_work, bus=InMemoryLogBus())

    assert ran.view()["ok"] is True and client.schedule_calls == 1
    names = set(transfer.package().namelist())
    assert {"bundle/bajutsu.config.yaml", "bundle/scenarios/one.yaml"} <= names
    assert not any("stray" in n for n in names)
    assert (job_work / "runs" / "20260101-1" / "manifest.json").is_file()
    worker_mod._discard_batch_workspace(work, job_work)
    assert not job_work.exists() and (work / "stray.txt").exists()


def test_a_job_naming_a_host_directory_packages_that_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A local start's job ships no files and names the config's directory (BE-0449); a worker on the
    # same machine packages it and resolves the relative appPath there.
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    project = _bundle(tmp_path / "project")
    spec = {
        "job_id": "j-host",
        "cmd": [],
        "cwd": str(project),
        "batch": {**asdict(_request())},
    }
    provider, client, transfer = _provider(_source_tree(tmp_path / "src"))
    bp.register("devicefarm", provider)
    job_work = worker_mod._bundle_workspace(tmp_path / "worker", spec, None)

    ran = execute_job_spec(spec, cwd=job_work, bus=InMemoryLogBus())

    assert ran.view()["ok"] is True and client.schedule_calls == 1
    assert "bundle/scenarios/one.yaml" in transfer.package().namelist()
    assert (job_work / "runs" / "20260101-1").is_dir()
    assert not (project / "runs" / "20260101-1").exists()


def test_a_batch_job_with_an_unsafe_id_gets_no_workspace(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="cannot name a directory"):
        worker_mod._bundle_workspace(
            tmp_path, {"job_id": "../x", "batch": {"provider": "devicefarm"}}, None
        )


# --- the worker runs only what belongs to its environment ---------------------------------------


@pytest.mark.parametrize(
    ("spec", "environment", "words"),
    [
        ({"batch": {"provider": "devicefarm"}}, "local", "worker.yaml environment"),
        ({"cmd": ["x"]}, "devicefarm", "runs only that provider's batch jobs"),
        ({"batch": {"provider": "other"}}, "devicefarm", "names provider 'other'"),
    ],
)
def test_a_job_outside_the_workers_environment_is_refused(
    spec: dict[str, Any], environment: str, words: str
) -> None:
    reason = one_shot.environment_refusal(spec, environment)
    assert reason is not None and words in reason


def test_a_leased_job_outside_the_environment_fails_before_it_is_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_mod, "_post_json", lambda *_a, **_k: (200, {}))
    result, abandoned, _work = worker_mod._run_with_heartbeat(
        {"cmd": ["x"], "bundle": {"id": "a" * 64}},
        job_id="j1",
        work=tmp_path,
        bundle_urls=None,
        bus=InMemoryLogBus(),
        url="http://cp",
        wid="w1",
        auth_token=None,
        heartbeat_interval=0.02,
        environment="devicefarm",
    )
    assert not abandoned
    assert result is not None and result["ok"] is False
    assert "runs only that provider's batch jobs" in result["error"]


@pytest.mark.parametrize(
    ("batch", "words"),
    [
        ({**asdict(_request()), "extra": "x"}, "must name exactly"),
        ({**asdict(_request()), "platform": "web"}, "android or ios"),
        ({**asdict(_request()), "scenario": "../one.yaml"}, "batch.scenario"),
        ({**asdict(_request()), "config": ""}, "non-empty strings"),
    ],
)
def test_a_malformed_one_shot_batch_is_refused(batch: dict[str, Any], words: str) -> None:
    reason = one_shot.refusal({"batch": batch}, environment="devicefarm")
    assert reason is not None and words in reason


# --- `bajutsu worker --once` submits one batch spec with no server --------------------------------


class _RecordingProvider:
    """Wraps the real provider over the fake, recording the checkpoint each submit received."""

    def __init__(self, inner: bp.DeviceFarmBatchProvider) -> None:
        self.inner = inner
        self.checkpoints: list[Any] = []

    def submit(
        self,
        request: bp.BatchRequest,
        *,
        work_dir: Path,
        dest: Path,
        checkpoint: Any = None,
        job_id: str = "",
        interrupt: Any = None,
    ) -> Verdict:
        self.checkpoints.append(checkpoint)
        return self.inner.submit(
            request,
            work_dir=work_dir,
            dest=dest,
            checkpoint=checkpoint,
            job_id=job_id,
            interrupt=interrupt,
        )


def test_worker_once_submits_a_batch_spec_from_a_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _bundle(tmp_path / "project")
    inner, client, _transfer = _provider(_source_tree(tmp_path / "src"))
    recording = _RecordingProvider(inner)

    def register(*_a: Any, **_k: Any) -> list[str]:
        bp.register("devicefarm", recording)
        return ["devicefarm"]

    monkeypatch.setattr(batch_bootstrap, "register_batch_providers", register)
    worker_file = tmp_path / "w.yaml"
    worker_file.write_text("version: 1\nenvironment: devicefarm\ndrivers: [adb]\n", "utf-8")
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({"batch": asdict(_request())}), encoding="utf-8")
    monkeypatch.chdir(project)

    result = runner.invoke(
        app, ["worker", "--once", str(spec_file), "--worker-config", str(worker_file)]
    )

    assert result.exit_code == 0, result.output
    assert "bajutsu verdict: PASS" in result.output
    assert client.schedule_calls == 1
    # The application resolved from this directory, and no ARN was stored anywhere.
    assert recording.checkpoints == [None]
    assert (project / "runs" / "20260101-1" / "manifest.json").is_file()


def test_worker_once_refuses_a_plain_spec_under_a_device_farm_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        batch_bootstrap, "register_batch_providers", lambda *_a, **_k: ["devicefarm"]
    )
    worker_file = tmp_path / "w.yaml"
    worker_file.write_text("version: 1\nenvironment: devicefarm\ndrivers: [adb]\n", "utf-8")
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({"cmd": ["never-run"]}), encoding="utf-8")
    result = runner.invoke(
        app, ["worker", "--once", str(spec_file), "--worker-config", str(worker_file)]
    )
    assert result.exit_code == 2
    assert "runs only that provider's batch jobs" in re.sub(
        r"[\s│]+", " ", re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    )


# --- review follow-ups: state directories and the application's reach ---------------------------


def test_process_state_never_reaches_the_package(tmp_path: Path) -> None:
    # A bundle directory may be a project holding the local start's keys, or a worker workspace.
    bundle = _bundle(tmp_path / "bundle")
    for rel in (
        ".bajutsu/secrets.key",
        ".bajutsu/queue.db",
        ".bundles/o/x/app.apk",
        ".overrides/x",
        ".batch/j/x",
        ".owner-pid",
        ".job-pgid",
    ):
        (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
        (bundle / rel).write_text("state", encoding="utf-8")
    provider, _client, transfer = _provider(_source_tree(tmp_path / "src"))

    provider.submit(_request(), work_dir=bundle, dest=tmp_path / "dest")

    names = transfer.package().namelist()
    assert "bundle/bajutsu.config.yaml" in names
    assert not [n for n in names if "/." in n or n.startswith(".")]


def _shipped_spec(job_id: str, app_path: str) -> dict[str, Any]:
    """A leased spec that ships its config and scenario as materials, naming *app_path*."""
    return {
        "job_id": job_id,
        "cmd": [],
        "materials": {"bajutsu.config.yaml": "targets: {}\n", "scenarios/one.yaml": _SCENARIO},
        "batch": asdict(_request(app_path)),
    }


@pytest.mark.parametrize("how", ["absolute", "dotdot", "symlink"])
def test_a_shipped_job_may_not_upload_a_binary_outside_its_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    outside = tmp_path / "host.apk"
    outside.write_bytes(b"host file")
    spec = _shipped_spec(f"j-{how}", str(outside))
    job_work = worker_mod._bundle_workspace(tmp_path / "worker", spec, None)
    if how == "dotdot":
        spec["batch"]["app_path"] = "../../../host.apk"
    elif how == "symlink":
        (job_work / "app.apk").symlink_to(outside)
        spec["batch"]["app_path"] = "app.apk"
    provider, client, transfer = _provider(_source_tree(tmp_path / "src"))
    bp.register("devicefarm", provider)

    ran = execute_job_spec(spec, cwd=job_work, bus=InMemoryLogBus())

    assert ran.view()["ok"] is False
    assert any("outside this job's bundle" in line for line in ran.lines)
    assert client.schedule_calls == 0 and transfer.uploads == {}


@pytest.mark.parametrize(("inherit_stdio", "one_shot"), [(True, False), (False, True)])
def test_only_the_one_shot_flag_lifts_a_batch_jobs_path_confinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherit_stdio: bool, one_shot: bool
) -> None:
    # Console passthrough says where output goes; it must never decide where a binary may come from.
    from bajutsu.serve import jobs
    from bajutsu.serve.jobs import run_job
    from bajutsu.serve.state import Job

    seen: dict[str, bool] = {}

    def fake_batch(_state: Any, _job: Any, *, one_shot: bool = False) -> None:
        seen["one_shot"] = one_shot

    monkeypatch.setattr(jobs, "_run_batch_job", fake_batch)
    state = ServeState(runs_dir=tmp_path / "runs")
    job = state.register(Job(cmd=[], batch=_request()))
    run_job(state, job, inherit_stdio=inherit_stdio, one_shot=one_shot)
    assert seen == {"one_shot": one_shot}


def test_a_host_directory_job_may_name_a_binary_anywhere_on_its_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The job names files on the server's own machine, which the worker shares, so no workspace
    # bounds it; the same holds for `--once` (covered by the one-shot test above, from a local path).
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    project = _bundle(tmp_path / "project")
    elsewhere = tmp_path / "builds" / "app.apk"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"APK")
    spec = {
        "job_id": "j-abs",
        "cmd": [],
        "cwd": str(project),
        "batch": asdict(_request(str(elsewhere))),
    }
    provider, client, _transfer = _provider(_source_tree(tmp_path / "src"))
    bp.register("devicefarm", provider)
    job_work = worker_mod._bundle_workspace(tmp_path / "worker", spec, None)

    ran = execute_job_spec(spec, cwd=job_work, bus=InMemoryLogBus())

    assert ran.view()["ok"] is True and client.schedule_calls == 1


def test_worker_once_may_name_a_binary_outside_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _bundle(tmp_path / "project")
    elsewhere = tmp_path / "app.apk"
    elsewhere.write_bytes(b"APK")
    provider, client, _transfer = _provider(_source_tree(tmp_path / "src"))

    def register(*_a: Any, **_k: Any) -> list[str]:
        bp.register("devicefarm", provider)
        return ["devicefarm"]

    monkeypatch.setattr(batch_bootstrap, "register_batch_providers", register)
    worker_file = tmp_path / "w.yaml"
    worker_file.write_text("version: 1\nenvironment: devicefarm\ndrivers: [adb]\n", "utf-8")
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({"batch": asdict(_request(str(elsewhere)))}), "utf-8")
    monkeypatch.chdir(project)

    result = runner.invoke(
        app, ["worker", "--once", str(spec_file), "--worker-config", str(worker_file)]
    )

    assert result.exit_code == 0, result.output
    assert client.schedule_calls == 1


def test_only_a_cloud_batch_spec_may_leave_its_cmd_out() -> None:
    from bajutsu.serve.server.worker_job import _spec_argv

    assert _spec_argv({"batch": asdict(_request())}) == []
    with pytest.raises(KeyError):
        _spec_argv({"job_id": "j"})


def test_a_package_composed_from_the_source_tree_carries_every_file_its_build_needs() -> None:
    # The test spec `pip install`s the package root, and hatchling refuses to build when a readme or
    # license file pyproject.toml names is missing, so a package without one never installs Bajutsu.
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    needed = {"pyproject.toml", project["readme"], *project["license-files"]}
    provider = bp.DeviceFarmBatchProvider(
        client=_FakeClient(),
        transfer=_CapturingTransfer(),
        project_arn="arn:project/1",
        sleep=lambda _: None,
        source_root=root,
    )
    arcnames = {arc for _, arc in provider._package_entries(root / "bundle")}
    assert needed <= arcnames
    assert all((root / name).is_file() for name in needed)
