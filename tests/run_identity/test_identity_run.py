"""The provenance stamp a `run` writes: every target's build and source, gaps included (BE-0506)."""

from __future__ import annotations

import hashlib
import json
import plistlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from run_cli import invoke
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.common.config import load_config, resolve
from bajutsu.common.config.effective import Effective
from bajutsu.common.config.schema import DeviceProvider
from bajutsu.common.report.manifest import run_provenance
from bajutsu.common.run_identity.stamp import RunIdentity, read_run_identity, target_identity
from bajutsu.common.runner.launch import ReadinessResult

_MISSING = dict.fromkeys(("hash", "hashKind", "version", "buildNumber"), "path-missing")


def _config(*targets: str) -> str:
    blocks = "".join(
        f"  {name}:\n    platform: ios\n    subject:\n      id: com.example.{name}\n"
        f"      path: {name}.app\n      sourceRoot: {name}-src\n"
        for name in targets
    )
    return f"targets:\n{blocks}"


def _eff(root: Path, name: str = "one") -> Effective:
    return resolve(load_config(_config(name)), name).rebased(root)


def _bundle(root: Path, name: str, version: str) -> None:
    app_dir = root / f"{name}.app"
    app_dir.mkdir()
    (app_dir / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": version, "CFBundleVersion": "7"})
    )


def test_a_target_records_its_binary_and_version(tmp_path: Path) -> None:
    _bundle(tmp_path, "one", "1.2")
    stamp: Any = target_identity(_eff(tmp_path), trusted=True)
    assert stamp["app"]["hash"].startswith("sha256:")
    assert stamp["app"]["hashKind"] == "tree"
    assert (stamp["app"]["version"], stamp["app"]["buildNumber"]) == ("1.2", "7")
    assert "unavailable" not in stamp["app"]
    assert stamp["appSource"]["unavailable"]["commit"] == "not-a-git-repo"


def test_an_android_target_records_its_apk(tmp_path: Path) -> None:
    (tmp_path / "a.apk").write_bytes(b"not a zip")
    config = (
        "targets:\n  a:\n    platform: android\n    subject: {id: com.example.a, path: a.apk}\n"
    )
    stamp: Any = target_identity(resolve(load_config(config), "a").rebased(tmp_path), trusted=True)
    assert stamp["app"]["hash"] == f"sha256:{hashlib.sha256(b'not a zip').hexdigest()}"
    assert stamp["app"]["hashKind"] == "file"
    assert stamp["app"]["unavailable"] == {
        "version": "apk-unreadable",
        "buildNumber": "apk-unreadable",
    }
    assert "appSource" not in stamp


def test_a_target_without_a_path_or_source_records_neither(tmp_path: Path) -> None:
    for config in (
        "targets:\n  w:\n    platform: web\n    subject: {url: 'http://127.0.0.1/'}\n",
        "targets:\n  w:\n    platform: ios\n    subject: {id: com.example.w}\n",
    ):
        assert (
            target_identity(resolve(load_config(config), "w").rebased(tmp_path), trusted=True) == {}
        )


def test_a_provider_installed_app_is_not_claimed(tmp_path: Path) -> None:
    _bundle(tmp_path, "one", "1.2")
    eff = replace(_eff(tmp_path), device_provider=DeviceProvider(kind="appium"))
    assert set(target_identity(eff, trusted=True)) == {"appSource"}


def test_a_local_provider_installs_from_the_path(tmp_path: Path) -> None:
    _bundle(tmp_path, "one", "1.2")
    eff = replace(_eff(tmp_path), device_provider=DeviceProvider(kind="local"))
    assert "app" in target_identity(eff, trusted=True)


def test_a_real_ios_device_s_build_is_not_claimed(tmp_path: Path) -> None:
    _bundle(tmp_path, "one", "1.2")
    config = _config("one") + "    runsOn: {ios: {kind: device}}\n"
    eff = resolve(load_config(config), "one").rebased(tmp_path)
    assert set(target_identity(eff, trusted=True)) == {"appSource"}


def test_provenance_omits_an_empty_identity() -> None:
    prov = run_provenance("s", git_revision=None, identity=RunIdentity())
    assert "targets" not in prov and "toolSource" not in prov


def test_provenance_carries_the_identity() -> None:
    identity = RunIdentity(targets={"one": {}}, tool_source={"commit": "c", "source": "build-arg"})
    prov = run_provenance("s", git_revision=None, identity=identity)
    assert prov["targets"] == {"one": {}}
    assert prov["toolSource"] == {"commit": "c", "source": "build-arg"}


def test_read_run_identity_covers_every_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pinned, since the real one answers for however this suite's own package was installed.
    pinned = {"commit": "c", "source": "checkout"}
    monkeypatch.setattr("bajutsu.common.run_identity.stamp.tool_source", lambda: pinned)
    effs = {"b": _eff(tmp_path, "b"), "a": _eff(tmp_path, "a")}
    identity = read_run_identity(effs, trusted=True)
    assert list(identity.targets) == ["a", "b"]
    assert identity.tool_source == pinned


@pytest.fixture
def _ready_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake backend never shows a screen, so the launch wait would run to its timeout.
    monkeypatch.setattr(
        "bajutsu.common.runner.launch.await_ready",
        lambda *_a, **_k: ReadinessResult(True, "skipped", 0.0),
    )


@pytest.mark.usefixtures("_ready_at_once")
def test_a_run_passes_and_stamps_every_target_including_its_gaps(tmp_path: Path) -> None:
    _bundle(tmp_path, "one", "1.2")
    (tmp_path / "bajutsu.config.yaml").write_text(_config("one", "two"), encoding="utf-8")
    (tmp_path / "s.yaml").write_text(
        "scenarios:\n  - name: both\n    targets: [one, two]\n    steps: []\n", encoding="utf-8"
    )
    result = invoke(
        CliRunner(),
        app,
        [
            *("run", "--target", "one", "--scenario", str(tmp_path / "s.yaml")),
            *("--backend", "fake", "--config", str(tmp_path / "bajutsu.config.yaml")),
            *("--runs-dir", str(tmp_path / "runs"), "--udid", "d1,d2"),
        ],
    )
    assert result.exit_code == 0, result.output
    run_dir = next((tmp_path / "runs").glob("*/manifest.json")).parent
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    targets = manifest["provenance"]["targets"]
    assert set(targets) == {"one", "two"}
    assert targets["one"]["app"]["version"] == "1.2"
    assert targets["two"]["app"]["hash"] is None
    assert targets["two"]["app"]["unavailable"] == _MISSING
    assert targets["two"]["appSource"]["commit"] is None


@pytest.mark.usefixtures("_ready_at_once")
def test_a_run_reads_the_binary_its_build_and_before_run_hook_wrote(tmp_path: Path) -> None:
    # The app does not exist until the build runs, and the hook then rewrites its version.
    plist = tmp_path / "one.app" / "Info.plist"
    for name, version in (("build.sh", "1.0"), ("hook.sh", "2.0")):
        info = plistlib.dumps({"CFBundleShortVersionString": version, "CFBundleVersion": "1"})
        (tmp_path / f"{version}.plist").write_bytes(info)
        script = tmp_path / name
        script.write_text(
            f"#!/bin/sh\nmkdir -p '{plist.parent}'\ncp '{tmp_path / version}.plist' '{plist}'\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
    (tmp_path / "bajutsu.config.yaml").write_text(
        "targets:\n  one:\n    platform: ios\n    subject:\n      id: com.example.one\n"
        f"      path: one.app\n      build: '{tmp_path / 'build.sh'}'\n"
        f"    hooks:\n      beforeRun: [{{name: up, cmd: '{tmp_path / 'hook.sh'}'}}]\n",
        encoding="utf-8",
    )
    (tmp_path / "s.yaml").write_text("scenarios:\n  - name: s\n    steps: []\n", encoding="utf-8")
    result = invoke(
        CliRunner(),
        app,
        [
            *("run", "--target", "one", "--scenario", str(tmp_path / "s.yaml")),
            *("--backend", "fake", "--config", str(tmp_path / "bajutsu.config.yaml")),
            *("--runs-dir", str(tmp_path / "runs")),
        ],
    )
    assert result.exit_code == 0, result.output
    manifest = json.loads(next((tmp_path / "runs").glob("*/manifest.json")).read_text("utf-8"))
    assert manifest["provenance"]["targets"]["one"]["app"]["version"] == "2.0"
