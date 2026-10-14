"""`bajutsu run` under a worker capability file (BE-0450).

The backend filter in the shared actuator selection, the `--worker-config` gate on `run`, the
per-scenario check that runs before any target is resolved, and the guarantee that a target the
worker cannot run is refused before any device is acquired. No device is touched: the fake backend
stands in, and the host is pinned so the gate behaves the same on macOS and Linux.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer
from run_cli import invoke
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli._shared import LoadedConfig, _load_device_worker_or_exit, _worker_backends
from bajutsu.common.capability.worker_capability import WorkerCapability
from bajutsu.common.config import load_config, resolve
from bajutsu.common.scenario import Scenario
from bajutsu.run.cli import (
    _acquire_targets,
    _worker_capability_failures,
)

runner = CliRunner()

_CONFIG = (
    "targets:\n"
    "  app:\n"
    "    platform: ios\n"
    "    app: {id: com.example.app}\n"
    "  site:\n"
    "    platform: web\n"
    "    app: {url: 'http://localhost:1/'}\n"
)


def _worker(**fields: object) -> WorkerCapability:
    return WorkerCapability.model_validate(
        {"version": 1, "drivers": ["xcuitest", "adb", "playwright", "fake"], **fields}
    )


def _sc(name: str, **body: object) -> Scenario:
    return Scenario.model_validate({"name": name, "steps": [], **body})


@pytest.fixture
def on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bajutsu.cli._shared.current_host_os", lambda: "linux")
    monkeypatch.setattr("bajutsu.run.cli.current_host_os", lambda: "linux")


@pytest.fixture
def on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bajutsu.cli._shared.current_host_os", lambda: "darwin")
    monkeypatch.setattr("bajutsu.run.cli.current_host_os", lambda: "darwin")


# --- the shared backend filter ------------------------------------------------------------------


@pytest.mark.usefixtures("on_linux")
def test_a_fully_supported_list_comes_back_unchanged() -> None:
    assert _worker_backends(["web", "fake"], _worker()) == ["web", "fake"]


@pytest.mark.usefixtures("on_linux")
def test_a_fallback_list_drops_what_the_host_cannot_run() -> None:
    assert _worker_backends(["ios", "web"], _worker()) == ["playwright"]


@pytest.mark.usefixtures("on_linux")
def test_no_runnable_actuator_exits_2_on_the_named_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit) as exc:
        _worker_backends(["ios"], _worker())
    assert exc.value.exit_code == 2
    assert capsys.readouterr().out.strip() == (
        "worker-capability-unsupported: xcuitest runs only on macOS; this worker runs on linux"
    )


@pytest.mark.usefixtures("on_linux")
def test_without_a_worker_the_shipped_default_applies() -> None:
    # The default offers every driver, so only the host rule bites.
    assert _worker_backends(["ios", "fake"], None) == ["fake"]


# --- `run --worker-config` ----------------------------------------------------------------------


def test_run_refuses_a_device_cloud_worker_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "df.worker.yaml"
    path.write_text("version: 1\nenvironment: devicefarm\ndrivers: [adb]\n", encoding="utf-8")
    with pytest.raises(typer.Exit) as exc:
        _load_device_worker_or_exit(path)
    assert exc.value.exit_code == 2
    assert "environment 'devicefarm' is a device cloud" in capsys.readouterr().out


def test_run_accepts_an_appium_worker_file(tmp_path: Path) -> None:
    # An `appium` grid drives one platform's targets through its endpoint while the run itself stays
    # on this host (BE-0452), so a device-driving command takes the file.
    path = tmp_path / "grid.worker.yaml"
    path.write_text(
        "version: 1\nenvironment: appium\n"
        "appium: { endpoint: 'https://grid.example/wd/hub' }\ndrivers: [xcuitest]\n",
        encoding="utf-8",
    )
    worker = _load_device_worker_or_exit(path)
    assert worker.environment == "appium"
    provider = worker.device_provider_for("ios")
    assert provider is not None and provider.endpoint == "https://grid.example/wd/hub"
    assert worker.device_provider_for("web") is None


def test_run_refuses_an_invalid_worker_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.worker.yaml"
    path.write_text("version: 1\ndrivers: [selenium]\n", encoding="utf-8")
    result = invoke(
        runner,
        app,
        ["run", "--target", "app", "--worker-config", str(path), "--config", "nope.yaml"],
    )
    assert result.exit_code == 2
    assert "unknown driver(s) ['selenium']" in result.output


# --- the per-scenario check ---------------------------------------------------------------------


def _loaded() -> LoadedConfig:
    return LoadedConfig(
        config=load_config(_CONFIG), path=Path("bajutsu.config.yaml"), source=None, root=None
    )


@pytest.mark.usefixtures("on_macos")
def test_only_the_scenario_over_the_target_limit_fails() -> None:
    scenarios = [_sc("single"), _sc("cross", targets=["app", "site"]), _sc("other")]
    eff = resolve(load_config(_CONFIG), "app")
    failures = _worker_capability_failures(
        _loaded(), scenarios, eff, "", _worker(maxTargetsPerJob=1)
    )
    assert failures == {
        1: "worker-capability-unsupported: the scenario declares 2 targets; this worker allows "
        "at most 1 per job (maxTargetsPerJob)"
    }


@pytest.mark.usefixtures("on_linux")
def test_each_scenario_is_judged_on_its_own_targets() -> None:
    config = _CONFIG
    loaded = LoadedConfig(
        config=load_config(config), path=Path("bajutsu.config.yaml"), source=None, root=None
    )
    eff = resolve(load_config(config), "site")
    scenarios = [_sc("web-only", targets=["site"]), _sc("needs-ios", targets=["app"])]
    failures = _worker_capability_failures(loaded, scenarios, eff, "", _worker())
    assert failures == {
        1: "worker-capability-unsupported: target 'app': xcuitest runs only on macOS; this "
        "worker runs on linux"
    }


def test_an_unrunnable_target_is_refused_before_any_device_is_acquired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquired: list[object] = []
    monkeypatch.setattr("bajutsu.run.cli.acquire_device", lambda eff, udid: acquired.append(eff))
    cfg = load_config(_CONFIG)
    effs = {"app": resolve(cfg, "app"), "site": resolve(cfg, "site")}
    # `fake` is the only backend either target asks for, and this worker does not offer it.
    with pytest.raises(typer.Exit) as exc:
        _acquire_targets(effs, "", [], "booted", 1, _worker(drivers=["adb"]))
    assert exc.value.exit_code == 2
    assert acquired == []


# --- end to end through the CLI -----------------------------------------------------------------


def _write(tmp_path: Path, worker: str) -> list[str]:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(_CONFIG, encoding="utf-8")
    one = tmp_path / "one.yaml"
    one.write_text("- name: one\n  targets: [app]\n  steps: []\n", encoding="utf-8")
    two = tmp_path / "two.yaml"
    two.write_text("- name: two\n  targets: [app, site]\n  steps: []\n", encoding="utf-8")
    wfile = tmp_path / "w.yaml"
    wfile.write_text(worker, encoding="utf-8")
    return [
        "run",
        "--scenario",
        str(one),
        "--scenario",
        str(two),
        "--worker-config",
        str(wfile),
        # Both targets run on the fake driver, whatever their own platform (BE-0452).
        "--backend",
        "fake",
        "--config",
        str(cfg),
        "--runs-dir",
        str(tmp_path / "runs"),
    ]


def test_run_hands_the_failed_scenario_to_the_report_and_acquires_only_its_peers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquired: list[str] = []
    captured: dict[str, Any] = {}

    def fake_acquire(eff: Any, udid: str) -> Any:
        from bajutsu.common.platform_lifecycle import ProvisionProfile
        from bajutsu.common.runner.device_provider import DeviceLease

        acquired.append(eff.target)
        return DeviceLease(udid_spec=udid, provision=ProvisionProfile(), release=lambda: None)

    def reached_execution(
        _eff: object, scenarios: list[Scenario], *_a: object, **kwargs: Any
    ) -> object:
        captured.update(kwargs, scenarios=scenarios)
        raise typer.Exit(7)

    monkeypatch.setattr("bajutsu.run.cli.acquire_device", fake_acquire)
    monkeypatch.setattr("bajutsu.run.cli.device_pool", lambda *a, **k: (object(), lambda: None))
    monkeypatch.setattr("bajutsu.run.cli.run_and_report", reached_execution)
    result = invoke(
        runner,
        app,
        _write(tmp_path, "version: 1\nmaxTargetsPerJob: 1\ndrivers: [fake, playwright]\n"),
    )
    assert result.exit_code == 7, result.output
    # `site` is declared only by the failed scenario, so no device is reserved for it.
    assert acquired == ["app"]
    assert [s.name for s in captured["scenarios"]] == ["one", "two"]
    assert list(captured["worker_failures"]) == [1]
    assert captured["worker_failures"][1].startswith("worker-capability-unsupported:")


def test_run_exits_2_when_no_scenario_is_runnable(tmp_path: Path) -> None:
    args = _write(tmp_path, "version: 1\nmaxTargetsPerJob: 1\ndrivers: [fake]\n")
    # Keep only the two-target file, which this worker cannot run.
    del args[1:3]
    result = invoke(runner, app, args)
    assert result.exit_code == 2
    assert "two: worker-capability-unsupported: the scenario declares 2 targets" in result.output


def test_a_primary_only_failed_scenarios_use_is_not_acquired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first file infers the primary (`site`), but it is the one that fails: the run moves its
    # primary to the runnable file's target rather than reserving a device for `site`.
    acquired: list[str] = []
    reached: list[Scenario] = []

    def fake_acquire(eff: Any, udid: str) -> Any:
        from bajutsu.common.platform_lifecycle import ProvisionProfile
        from bajutsu.common.runner.device_provider import DeviceLease

        acquired.append(eff.target)
        return DeviceLease(udid_spec=udid, provision=ProvisionProfile(), release=lambda: None)

    def reached_execution(_eff: Any, scenarios: list[Scenario], *_a: object, **kw: Any) -> object:
        reached.extend(scenarios)
        raise typer.Exit(7)

    monkeypatch.setattr("bajutsu.run.cli.acquire_device", fake_acquire)
    monkeypatch.setattr("bajutsu.run.cli.device_pool", lambda *a, **k: (object(), lambda: None))
    monkeypatch.setattr("bajutsu.run.cli.run_and_report", reached_execution)
    args = _write(tmp_path, "version: 1\nmaxTargetsPerJob: 1\ndrivers: [fake, playwright]\n")
    # Only the new primary erases by default, so a scenario still carrying the failed primary's
    # run defaults would reach execution with erase off.
    (tmp_path / "bajutsu.config.yaml").write_text(
        _CONFIG.replace(
            "    app: {id: com.example.app}\n",
            "    app: {id: com.example.app}\n    run: {erase: true}\n",
        ),
        encoding="utf-8",
    )
    two = tmp_path / "two.yaml"
    two.write_text("- name: two\n  targets: [site, app]\n  steps: []\n", encoding="utf-8")
    # The failing file first, so it is the one the primary is inferred from.
    args[2], args[4] = args[4], args[2]
    result = invoke(runner, app, args)
    assert result.exit_code == 7, result.output
    assert acquired == ["app"]
    assert [sc.preconditions.run.erase for sc in reached if sc.name == "one"] == [True]


def test_the_browser_matrix_still_refuses_a_failed_multi_target_scenario(tmp_path: Path) -> None:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        (
            "targets:\n"
            "  app:\n"
            "    platform: fake\n"
            "    app: {id: com.example.app}\n"
            "  site:\n"
            "    platform: web\n"
            "    app: {url: 'http://localhost:1/'}\n"
        ),
        encoding="utf-8",
    )
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("- name: legacy\n  steps: []\n", encoding="utf-8")
    two = tmp_path / "two.yaml"
    two.write_text("- name: two\n  targets: [site, app]\n  steps: []\n", encoding="utf-8")
    wfile = tmp_path / "w.yaml"
    wfile.write_text(
        "version: 1\nmaxTargetsPerJob: 1\ndrivers: [playwright, fake]\n", encoding="utf-8"
    )
    result = invoke(
        runner,
        app,
        [
            "run",
            "--target",
            "site",
            "--scenario",
            str(legacy),
            "--scenario",
            str(two),
            "--browsers",
            "chromium,firefox",
            "--worker-config",
            str(wfile),
            "--config",
            str(cfg),
        ],
    )
    assert result.exit_code == 2
    assert "--browsers cannot fan out" in result.output
