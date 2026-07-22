"""Tests for the wheel-bundled XCUITest runner resolution (BE-0292).

BE-0019 required `xcuitest.testRunner` (or a `build` command) in config; this adds a third,
lowest-priority tier — a runner shipped in the wheel — so a Simulator run with neither configured
resolves to it. The resolution and the version-keyed materialize-to-cache are pure file-path logic,
so both are exercised here without a Simulator (the sanctioned gate boundary). A real device stays
explicit, since its runner must be signed and is not bundled (BE-0288).
"""

from __future__ import annotations

import os
import plistlib
import time
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


def test_build_without_a_test_runner_fails_instead_of_using_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `build` only ever refreshes the file at `testRunner`; without that path configured, silently
    # falling back to the bundled runner would drop the configured build on the floor.
    bundle = _products(tmp_path / "bundle")
    monkeypatch.setattr(xcuitest, "bundled_products_dir", lambda: bundle)
    cfg = XcuitestConfig.model_validate({"build": "make runner"})
    with pytest.raises(simctl.DeviceError, match=r"xcuitest\.build requires xcuitest\.testRunner"):
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
    # The cache directory is keyed by version *and* a content digest, so it stays stable for the
    # same products and reuses the warm copy without recopying.
    digest = _bundled_runner._products_digest(source)
    assert first == cache / f"1.2.3-{digest}" / "BajutsuRunner.xctestrun"
    assert copies["n"] == 1

    # A warm cache is reused without recopying.
    second = _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)
    assert second == first
    assert copies["n"] == 1


def test_materialize_refreshes_on_changed_content(tmp_path: Path) -> None:
    # Updated runner products must land in a fresh directory even when the version string is
    # unchanged — the pre-release version is a static placeholder (BE-0272), so the content digest
    # is what detects a rebuild and prevents silently reusing stale products.
    source = _products(tmp_path / "bundle")
    cache = tmp_path / "cache"

    first = _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)

    # Add a file to the products tree: the digest shifts, so a new cache directory is used.
    (source / "Added.bundle").mkdir()
    (source / "Added.bundle" / "payload").write_bytes(b"x")
    second = _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)

    assert second != first
    assert second.is_file()
    assert second.parent != first.parent


def test_products_digest_skips_rehashing_an_unchanged_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cheap per-file (size, mtime) signature gates the expensive full-content hash, so a repeat
    # call against the same unchanged tree (e.g. one materialize() per simulator in a device-pool
    # run) must not re-read every file's bytes.
    source = _products(tmp_path / "bundle")

    calls = {"n": 0}
    real_sha256 = _bundled_runner.hashlib.sha256

    def _counting_sha256(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        return real_sha256(*args, **kwargs)

    monkeypatch.setattr(_bundled_runner.hashlib, "sha256", _counting_sha256)

    first = _bundled_runner._products_digest(source)
    second = _bundled_runner._products_digest(source)

    assert first == second
    assert calls["n"] == 1


def test_materialize_refreshes_on_same_size_content_change(tmp_path: Path) -> None:
    # A rebuild that changes a file's bytes while its size stays identical (e.g. a recompiled
    # binary, or a plist value swapped for another of equal length) must still shift the digest —
    # hashing path + size alone would miss it and silently reuse the stale cached runner.
    source = _products(tmp_path / "bundle")
    cache = tmp_path / "cache"

    first = _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)

    plist_path = source / "BajutsuRunner.xctestrun"
    plist_path.write_bytes(plist_path.read_bytes()[:-1] + b"\x00")  # same size, different bytes
    second = _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)

    assert second != first
    assert second.is_file()
    assert second.parent != first.parent


def test_materialize_sweeps_a_stale_partial(tmp_path: Path) -> None:
    # A hard kill can leave a `.partial-*` temp dir that nothing else cleans; a materialize that
    # copies must sweep such siblings before creating its own temp dir, once they are old enough
    # to no longer be a plausibly in-flight concurrent copy.
    source = _products(tmp_path / "bundle")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    stale = cache / f"1.2.3-deadbeef{_bundled_runner._PARTIAL_MARKER}orphan"
    stale.mkdir()
    (stale / "leftover").write_bytes(b"x")
    old = time.time() - _bundled_runner._STALE_PARTIAL_AGE_SECONDS - 1
    os.utime(stale, (old, old))

    _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)

    assert not stale.exists()
    assert not list(cache.glob(f"*{_bundled_runner._PARTIAL_MARKER}*"))


def test_materialize_does_not_sweep_a_fresh_partial(tmp_path: Path) -> None:
    # A partial younger than the staleness threshold could be another lane's in-flight copytree —
    # sweeping it would corrupt that concurrent process's copy, so it must survive untouched.
    source = _products(tmp_path / "bundle")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    other_digest = _bundled_runner._products_digest(source) + "-different"
    fresh = cache / f"1.2.3-{other_digest}{_bundled_runner._PARTIAL_MARKER}inflight"
    fresh.mkdir()
    (fresh / "leftover").write_bytes(b"x")

    _bundled_runner.materialize(source, version="1.2.3", cache_root=cache)

    assert fresh.exists()


def test_materialize_survives_a_concurrent_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two lanes on the same host + version race: the loser's os.replace lands on a dest another
    # process already materialized. It must keep the winner's copy, return the runner, and not leak
    # its own temp dir.
    source = _products(tmp_path / "bundle")
    cache = tmp_path / "cache"
    dest = cache / f"1.2.3-{_bundled_runner._products_digest(source)}"

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
    assert not list(cache.glob(f"*{_bundled_runner._PARTIAL_MARKER}*"))


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
    assert not list(cache.glob(f"*{_bundled_runner._PARTIAL_MARKER}*"))


def test_bundled_products_dir_absent_by_default() -> None:
    # A source checkout / Linux wheel ships no compiled runner, so resolution treats it as absent.
    assert _bundled_runner.bundled_products_dir() is None


# --- runner_source: the same precedence, disclosed without acting on it (BE-0292) --- #


def test_runner_source_reports_bundled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = _products(tmp_path / "bundle")
    monkeypatch.setattr(xcuitest, "bundled_products_dir", lambda: bundle)
    assert xcuitest.runner_source(None, "simulator") == "bundled (wheel-shipped Simulator runner)"


def test_runner_source_reports_no_bundle_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xcuitest, "bundled_products_dir", lambda: None)
    assert xcuitest.runner_source(None, "simulator") == (
        "none: no bundled runner in this build (set xcuitest.testRunner)"
    )


def test_runner_source_reports_device_requires_test_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(xcuitest, "bundled_products_dir", lambda: _products(tmp_path / "bundle"))
    assert xcuitest.runner_source(None, "device") == (
        "none: xcuitest.deviceType: device requires an explicit testRunner"
    )


def test_runner_source_reports_an_existing_test_runner(tmp_path: Path) -> None:
    runner = tmp_path / "Explicit.xctestrun"
    runner.write_bytes(b"")
    cfg = XcuitestConfig.model_validate({"testRunner": str(runner)})
    assert xcuitest.runner_source(cfg, "simulator") == f"testRunner: {runner}"


def test_runner_source_reports_a_missing_test_runner_with_build(tmp_path: Path) -> None:
    runner = tmp_path / "Built.xctestrun"
    cfg = XcuitestConfig.model_validate({"testRunner": str(runner), "build": "make runner"})
    assert xcuitest.runner_source(cfg, "simulator") == (
        f"testRunner: {runner} (missing, built on demand via: make runner)"
    )


def test_runner_source_reports_a_missing_test_runner_without_build(tmp_path: Path) -> None:
    runner = tmp_path / "nope.xctestrun"
    cfg = XcuitestConfig.model_validate({"testRunner": str(runner)})
    assert xcuitest.runner_source(cfg, "simulator") == (
        f"testRunner: {runner} (missing, no build configured)"
    )


def test_runner_source_reports_build_without_test_runner_as_misconfigured() -> None:
    cfg = XcuitestConfig.model_validate({"build": "make runner"})
    assert xcuitest.runner_source(cfg, "simulator") == (
        "misconfigured: xcuitest.build requires xcuitest.testRunner"
    )


def test_runner_source_never_runs_build_or_materializes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The whole point of `runner_source` is to disclose without acting: it must not shell out to a
    # configured `build`, nor materialize the bundled runner into the cache.
    bundle = _products(tmp_path / "bundle")
    monkeypatch.setattr(xcuitest, "bundled_products_dir", lambda: bundle)

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("runner_source must not run build or materialize")

    monkeypatch.setattr(xcuitest.subprocess, "run", _boom)
    monkeypatch.setattr(xcuitest, "materialize", _boom)

    assert xcuitest.runner_source(None, "simulator") == "bundled (wheel-shipped Simulator runner)"
    missing = tmp_path / "missing.xctestrun"
    cfg = XcuitestConfig.model_validate({"testRunner": str(missing), "build": "make runner"})
    assert xcuitest.runner_source(cfg, "simulator") == (
        f"testRunner: {missing} (missing, built on demand via: make runner)"
    )
