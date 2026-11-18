"""Tests for `run` building a target's app before the run: on every native platform, from a local
config as well as a Git one, and on request even when the app exists (`--rebuild`)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import typer

from bajutsu.common.capability.worker_capability import WorkerCapability, parse_worker_capability
from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.scenario import Scenario
from bajutsu.run.cli import _reject_bad_device_groups, _resolve_config_and_engines


def _config(tmp_path: Path, subject: str, platform: str = "android") -> Path:
    cfg = tmp_path / "proj" / "bajutsu.config.yaml"
    cfg.parent.mkdir()
    cfg.write_text(
        f"targets:\n  app:\n    platform: {platform}\n    subject: {subject}\n", encoding="utf-8"
    )
    return cfg


def _resolve(
    cfg: Path,
    *,
    rebuild: bool = False,
    built: set[str] | None = None,
    worker: WorkerCapability | None = None,
) -> Effective:
    _, eff, _, _ = _resolve_config_and_engines(
        str(cfg),
        "app",
        offline=False,
        require_pinned=False,
        headed=None,
        browser="",
        browsers="",
        rebuild=rebuild,
        built=built,
        worker=worker,
    )
    return eff


@pytest.mark.parametrize("platform", ["android", "ios"])
def test_a_local_config_builds_a_missing_app_from_its_own_directory(
    tmp_path: Path, platform: str
) -> None:
    # The relative `build/app.bin` lands beside the config, where `subject.path` already points —
    # not in the caller's working directory.
    cfg = _config(
        tmp_path,
        "{ id: com.example.app, path: build/app.bin, build: 'mkdir -p build/app.bin' }",
        platform,
    )
    _resolve(cfg)
    assert (cfg.parent / "build" / "app.bin").is_dir()


def test_an_existing_app_is_reused_without_rebuild(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "{ id: com.example.app, path: app.apk, build: 'touch rebuilt' }")
    (cfg.parent / "app.apk").touch()
    _resolve(cfg)
    assert not (cfg.parent / "rebuilt").exists()


def test_rebuild_builds_an_existing_app_once_per_run(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "{ id: com.example.app, path: app.apk, build: 'touch rebuilt' }")
    (cfg.parent / "app.apk").touch()
    built: set[str] = set()
    _resolve(cfg, rebuild=True, built=built)
    assert (cfg.parent / "rebuilt").exists()
    assert built == {"app"}
    # A reload under the same run (a re-inferred primary) does not build the same app again.
    (cfg.parent / "rebuilt").unlink()
    _resolve(cfg, rebuild=True, built=built)
    assert not (cfg.parent / "rebuilt").exists()


@pytest.mark.parametrize(
    ("platform", "subject"),
    [
        ("android", "{ id: com.example.app, path: app.apk }"),
        ("web", "{ url: 'http://localhost:1/' }"),
    ],
)
def test_rebuild_without_a_build_command_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], platform: str, subject: str
) -> None:
    cfg = _config(tmp_path, subject, platform)
    with pytest.raises(typer.Exit) as exc:
        _resolve(cfg, rebuild=True)
    assert exc.value.exit_code == 2
    assert "--rebuild needs target 'app' to declare subject.build" in capsys.readouterr().out


def test_a_failing_build_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _config(tmp_path, "{ id: com.example.app, path: app.apk, build: 'false' }")
    with pytest.raises(typer.Exit) as exc:
        _resolve(cfg)
    assert exc.value.exit_code == 2
    assert "build failed" in capsys.readouterr().out


def _group_effs(tmp_path: Path, build: str) -> dict[str, Effective]:
    cfg = load_config(
        f"""
targets:
  old: {{ platform: android, subject: {{ id: com.example.app }} }}
  new:
    platform: android
    subject: {{ id: com.example.app, path: {tmp_path / "new.apk"}, build: "{build}" }}
"""
    )
    return {name: replace(resolve(cfg, name), backend=["fake"]) for name in cfg.targets}


def _update_journey() -> Scenario:
    return Scenario.model_validate(
        {
            "name": "s",
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [{"installApp": {"from": "new"}}, {"setPrimaryTarget": {"target": "new"}}],
        }
    )


def test_a_device_group_member_builds_its_missing_android_app_locally(tmp_path: Path) -> None:
    effs = _group_effs(tmp_path, f"touch {tmp_path / 'new.apk'}")
    _reject_bad_device_groups([_update_journey()], effs, checkout_root=None)
    assert (tmp_path / "new.apk").exists()


def test_rebuild_forces_a_group_member_not_already_built(tmp_path: Path) -> None:
    (tmp_path / "new.apk").touch()
    effs = _group_effs(tmp_path, f"touch {tmp_path / 'rebuilt'}")
    built: set[str] = set()
    _reject_bad_device_groups(
        [_update_journey()], effs, checkout_root=None, rebuild=True, built=built
    )
    assert (tmp_path / "rebuilt").exists()
    assert built == {"new"}


def test_rebuild_skips_a_group_member_this_run_already_built(tmp_path: Path) -> None:
    (tmp_path / "new.apk").touch()
    effs = _group_effs(tmp_path, f"touch {tmp_path / 'rebuilt'}")
    _reject_bad_device_groups(
        [_update_journey()], effs, checkout_root=None, rebuild=True, built={"new"}
    )
    assert not (tmp_path / "rebuilt").exists()


_DEVICEFARM_HOST = "version: 1\nenvironment: devicefarm-host\ndrivers: [adb, xcuitest]\n"


def test_a_device_farm_host_never_builds_the_app_it_preinstalled(tmp_path: Path) -> None:
    # Device Farm installs the uploaded app and its package leaves `subject.path` out on purpose, so
    # the run must not try to build the missing binary on the host.
    cfg = _config(tmp_path, "{ id: com.example.app, path: app.apk, build: 'touch built' }")
    _resolve(cfg, worker=parse_worker_capability(_DEVICEFARM_HOST))
    assert not (cfg.parent / "built").exists()


def test_rebuild_on_a_device_farm_host_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _config(tmp_path, "{ id: com.example.app, path: app.apk, build: 'touch built' }")
    with pytest.raises(typer.Exit) as exc:
        _resolve(cfg, rebuild=True, worker=parse_worker_capability(_DEVICEFARM_HOST))
    assert exc.value.exit_code == 2
    assert "'devicefarm-host' provider, which installs the app itself" in capsys.readouterr().out
    assert not (cfg.parent / "built").exists()


_NO_BUILD = "version: 1\ndrivers: [adb, xcuitest]\nbuild: []\n"


def test_a_worker_that_cannot_build_stops_on_a_missing_app(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _config(tmp_path, "{ id: com.example.app, path: app.apk, build: 'touch built' }")
    with pytest.raises(typer.Exit) as exc:
        _resolve(cfg, worker=parse_worker_capability(_NO_BUILD))
    assert exc.value.exit_code == 2
    assert "this worker cannot build android apps" in capsys.readouterr().out
    assert not (cfg.parent / "built").exists()


def test_a_worker_that_cannot_build_still_runs_an_existing_app(tmp_path: Path) -> None:
    # The declaration only matters when a build is needed: an existing app runs as before.
    cfg = _config(tmp_path, "{ id: com.example.app, path: app.apk, build: 'touch built' }")
    (cfg.parent / "app.apk").touch()
    _resolve(cfg, worker=parse_worker_capability(_NO_BUILD))


def test_rebuild_needs_the_worker_to_build_the_targets_platform(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _config(tmp_path, "{ id: com.example.app, path: app.apk, build: 'touch built' }")
    (cfg.parent / "app.apk").touch()
    ios_only = parse_worker_capability("version: 1\ndrivers: [adb, xcuitest]\nbuild: [ios]\n")
    with pytest.raises(typer.Exit) as exc:
        _resolve(cfg, rebuild=True, worker=ios_only)
    assert exc.value.exit_code == 2
    assert "this worker cannot build android apps" in capsys.readouterr().out
    assert not (cfg.parent / "built").exists()


def test_a_group_member_this_worker_cannot_build_is_a_preflight_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    effs = _group_effs(tmp_path, f"touch {tmp_path / 'new.apk'}")
    with pytest.raises(typer.Exit) as exc:
        _reject_bad_device_groups(
            [_update_journey()],
            effs,
            checkout_root=None,
            worker=parse_worker_capability(_NO_BUILD),
        )
    assert exc.value.exit_code == 2
    assert "installApp from 'new': target 'new' needs its android app built" in (
        capsys.readouterr().out
    )
