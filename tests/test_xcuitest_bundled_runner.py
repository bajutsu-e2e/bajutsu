"""Tests for the wheel-bundled XCUITest runner resolution (BE-XXXX).

BE-0019 required `xcuitest.testRunner` (or a `build` command) in config; this adds a third,
lowest-priority tier — a runner shipped in the wheel — so a Simulator run with neither configured
resolves to it. The resolution and the version-keyed materialize-to-cache are pure file-path logic,
so both are exercised here without a Simulator (the sanctioned gate boundary). A real device stays
explicit, since its runner must be signed and is not bundled (BE-0288).
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from bajutsu import simctl
from bajutsu.config import XcuitestConfig
from bajutsu.platform_lifecycle.environments import _bundled_runner, xcuitest


def _products(dir_: Path) -> Path:
    """Write a minimal products directory (a `.xctestrun` beside a stub bundle) and return it."""
    dir_.mkdir(parents=True, exist_ok=True)
    with (dir_ / "BajutsuRunner.xctestrun").open("wb") as f:
        plistlib.dump({"Target": {"TestingEnvironmentVariables": {}}}, f)
    (dir_ / "BajutsuRunner-Runner.app").mkdir(exist_ok=True)
    return dir_


# --- precedence: an explicit testRunner wins, and its build fallback still runs --- #


def test_explicit_test_runner_is_used(tmp_path: Path) -> None:
    runner = tmp_path / "Explicit.xctestrun"
    runner.write_bytes(b"")
    cfg = XcuitestConfig.model_validate({"testRunner": str(runner)})
    assert xcuitest._resolve_runner(cfg, "simulator") == runner


def test_explicit_test_runner_wins_over_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even with a bundle present, an explicit path takes precedence — the bundle is the fallback.
    bundle = _products(tmp_path / "bundle")
    monkeypatch.setattr(xcuitest, "bundled_products_dir", lambda: bundle)
    runner = tmp_path / "Explicit.xctestrun"
    runner.write_bytes(b"")
    cfg = XcuitestConfig.model_validate({"testRunner": str(runner)})
    assert xcuitest._resolve_runner(cfg, "simulator") == runner


def test_missing_test_runner_runs_the_build_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = tmp_path / "Built.xctestrun"
    cfg = XcuitestConfig.model_validate({"testRunner": str(runner), "build": "make runner"})

    def _fake_run(argv: list[str], check: bool = False) -> None:
        runner.write_bytes(b"")  # the build produces the configured path

    monkeypatch.setattr(xcuitest.subprocess, "run", _fake_run)
    assert xcuitest._resolve_runner(cfg, "simulator") == runner


def test_missing_test_runner_without_build_fails(tmp_path: Path) -> None:
    cfg = XcuitestConfig.model_validate({"testRunner": str(tmp_path / "nope.xctestrun")})
    with pytest.raises(simctl.DeviceError, match="testRunner not found"):
        xcuitest._resolve_runner(cfg, "simulator")


# --- the bundled default tier (no testRunner, no build) --- #


def test_simulator_falls_back_to_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _products(tmp_path / "bundle")
    materialized = tmp_path / "cache" / "BajutsuRunner.xctestrun"
    seen: dict[str, Path] = {}

    def _fake_materialize(source: Path) -> Path:
        seen["source"] = source
        return materialized

    monkeypatch.setattr(xcuitest, "bundled_products_dir", lambda: bundle)
    monkeypatch.setattr(xcuitest, "materialize", _fake_materialize)

    # Both "no xcuitest block" and an empty one resolve to the bundle on the Simulator.
    assert xcuitest._resolve_runner(None, "simulator") == materialized
    assert xcuitest._resolve_runner(XcuitestConfig.model_validate({}), "simulator") == materialized
    assert seen["source"] == bundle


def test_simulator_without_a_bundle_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xcuitest, "bundled_products_dir", lambda: None)
    with pytest.raises(simctl.DeviceError, match="no bundled runner"):
        xcuitest._resolve_runner(None, "simulator")


def test_device_without_a_test_runner_never_uses_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real device must not silently take a Simulator runner it cannot install (BE-0288).
    bundle = _products(tmp_path / "bundle")
    monkeypatch.setattr(xcuitest, "bundled_products_dir", lambda: bundle)
    cfg = XcuitestConfig.model_validate({"deviceType": "device"})
    with pytest.raises(simctl.DeviceError, match="deviceType: device requires"):
        xcuitest._resolve_runner(cfg, "device")


# --- materialize-to-cache: copy once, reuse a warm, version-keyed cache --- #


def test_materialize_copies_once_and_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _products(tmp_path / "bundle")
    cache = tmp_path / "cache"

    # Count actual copies by the single os.replace each materialize does (copytree recurses into
    # subdirectories, so counting it would over-count a multi-file bundle).
    copies = {"n": 0}
    real_replace = _bundled_runner.os.replace

    def _counting_replace(src: object, dst: object) -> object:
        copies["n"] += 1
        return real_replace(src, dst)

    monkeypatch.setattr(_bundled_runner.os, "replace", _counting_replace)

    first = _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)
    assert first.is_file()
    assert first == cache / "1.2.3" / "BajutsuRunner.xctestrun"
    assert copies["n"] == 1

    # A warm cache is reused without recopying.
    second = _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)
    assert second == first
    assert copies["n"] == 1

    # A different version lands in its own directory (an upgrade refreshes it).
    other = _bundled_runner.materialize(source, version="9.9.9", cache_root=cache)
    assert other == cache / "9.9.9" / "BajutsuRunner.xctestrun"
    assert copies["n"] == 2


def test_materialize_survives_a_concurrent_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two lanes on the same host + version race: the loser's os.replace lands on a dest another
    # process already materialized. It must keep the winner's copy, return the runner, and not leak
    # its own temp dir.
    source = _products(tmp_path / "bundle")
    cache = tmp_path / "cache"
    dest = cache / "1.2.3"

    def _racing_replace(src: object, dst: object) -> object:
        # Simulate the winner filling *dest* just before our rename, so os.replace onto a
        # non-empty directory fails.
        _products(dest)
        raise OSError("directory not empty")

    monkeypatch.setattr(_bundled_runner.os, "replace", _racing_replace)

    result = _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)
    assert result == dest / "BajutsuRunner.xctestrun"
    assert result.is_file()
    # No `.partial-*` temp directory is left behind.
    assert not list(cache.glob("1.2.3.partial-*"))


def test_materialize_reraises_a_real_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failure that is not a lost race (dest never appears) propagates, with the temp dir cleaned.
    source = _products(tmp_path / "bundle")
    cache = tmp_path / "cache"

    def _failing_replace(src: object, dst: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(_bundled_runner.os, "replace", _failing_replace)

    with pytest.raises(OSError, match="disk full"):
        _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)
    assert not list((cache).glob("1.2.3.partial-*"))


def test_bundled_products_dir_absent_by_default() -> None:
    # A source checkout / Linux wheel ships no compiled runner, so resolution treats it as absent.
    assert _bundled_runner.bundled_products_dir() is None
