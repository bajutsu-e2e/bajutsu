"""Tests for the server-settings operation (BE-0318).

Operations-level: no HTTP, no Simulator. The endpoint reads the already-resolved `ServeState` and
probes the filesystem for the bundled iOS runner, so every branch is exercised with a plain state
and monkeypatched probes — device-free, running on the Linux gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _shared import project

import bajutsu
from bajutsu.backends import IMPLEMENTED
from bajutsu.serve import operations as ops
from bajutsu.serve.operations import config as config_ops
from bajutsu.serve.state import ServeState


def _state(tmp_path: Path, config_text: str | None = None, **kw: object) -> ServeState:
    """A ServeState bound to the default project config, or *config_text* when given."""
    if config_text is None:
        _scn_dir, cfg, runs = project(tmp_path)
    else:
        cfg = tmp_path / "bajutsu.config.yaml"
        cfg.write_text(config_text, encoding="utf-8")
        runs = tmp_path / "runs"
        runs.mkdir()
    return ServeState(runs_dir=runs, config=cfg, cwd=tmp_path, **kw)  # type: ignore[arg-type]


def _no_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the bundled-runner probes to report "no bundle", so a checkout that happens to ship one
    (a `make runner-bundle` artifact) can't make an assertion about absence flake."""
    monkeypatch.setattr(config_ops, "bundled_products_dir", lambda: None)
    monkeypatch.setattr(config_ops, "bundled_runner_build_info", lambda: None)


def test_local_exposes_host_paths_and_static_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_bundle(monkeypatch)
    state = _state(tmp_path)
    payload, status = ops.server_settings(state)
    assert status == 200
    assert payload["mode"] == "local"
    assert payload["version"] == bajutsu.__version__
    # Host paths are present on a local deployment.
    assert payload["config"] == str(state.config)
    assert payload["runsDir"] == str(state.runs_dir)
    assert payload["baselinesDir"] == str(state.baselines_dir)
    # Backends are the static implemented set, sorted for a stable display.
    assert payload["backends"] == sorted(IMPLEMENTED)
    assert payload["retentionDays"] == state.run_retention_days
    assert payload["concurrency"] == {
        "total": state.max_concurrent,
        "perUser": state.max_concurrent_per_user,
        "perOrg": state.max_concurrent_per_org,
    }


def test_hosted_withholds_host_paths_but_keeps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_bundle(monkeypatch)
    state = _state(tmp_path, hosted=True)
    payload, status = ops.server_settings(state)
    assert status == 200
    # The three host filesystem paths are dead information to a hosted user (BE-0108) — withheld.
    assert "config" not in payload
    assert "runsDir" not in payload
    assert "baselinesDir" not in payload
    # The non-path fields are shown either way.
    assert payload["mode"] == "hosted"
    assert payload["hasConfig"] is True
    assert payload["backends"] == sorted(IMPLEMENTED)
    assert payload["retentionDays"] == state.run_retention_days
    assert "concurrency" in payload and "iosRunner" in payload


def test_no_config_bound_still_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_bundle(monkeypatch)
    runs = tmp_path / "runs"
    runs.mkdir()
    state = ServeState(runs_dir=runs, config=None, cwd=tmp_path)
    payload, status = ops.server_settings(state)
    assert status == 200
    assert payload["hasConfig"] is False
    assert payload["config"] is None  # local: the key is present, its value None
    assert payload["configSource"] is None


def test_config_source_provenance_is_surfaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_bundle(monkeypatch)
    state = _state(tmp_path)
    prov = {"host": "github.com", "owner": "acme", "repo": "app", "ref": "main", "sha": "abc123"}
    state.config_provenance = prov
    payload, _ = ops.server_settings(state)
    assert payload["configSource"] == prov


def test_bundled_runner_reported_with_build_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_ops, "bundled_products_dir", lambda: tmp_path / "_runner")
    monkeypatch.setattr(
        config_ops, "bundled_runner_build_info", lambda: {"xcode": "16.2", "sdk": "18.2"}
    )
    payload, _ = ops.server_settings(_state(tmp_path))
    ios = payload["iosRunner"]
    assert ios["bundled"] is True
    assert ios["buildInfo"] == {"xcode": "16.2", "sdk": "18.2"}
    assert ios["override"] is False  # the default project config names no testRunner


def test_absent_runner_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_bundle(monkeypatch)
    payload, _ = ops.server_settings(_state(tmp_path))
    ios = payload["iosRunner"]
    assert ios["bundled"] is False
    assert ios["buildInfo"] is None
    assert ios["override"] is False


def test_testrunner_override_is_reflected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_bundle(monkeypatch)
    config_text = (
        "defaults: { backend: [ios] }\n"
        "targets:\n"
        "  demo: { bundleId: com.example.demo, xcuitest: { testRunner: /r/App.xctestrun } }\n"
    )
    payload, _ = ops.server_settings(_state(tmp_path, config_text))
    assert payload["iosRunner"]["override"] is True


def test_testrunner_override_survives_an_unresolvable_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One target that fails to resolve must not mask a pinned runner on another (BE-0318): the
    # per-target resolve is guarded, so a raise on the first target is skipped, not fatal to the row.
    _no_bundle(monkeypatch)
    config_text = (
        "defaults: { backend: [ios] }\n"
        "targets:\n"
        "  broken: { bundleId: com.example.broken }\n"
        "  pinned: { bundleId: com.example.pinned }\n"
    )

    def fake_resolve(cfg: object, name: str) -> str:
        if name == "broken":
            raise ValueError("unresolvable target")
        return name  # a sentinel the patched pins-checker recognizes

    monkeypatch.setattr(config_ops, "resolve", fake_resolve)
    monkeypatch.setattr(config_ops, "xcuitest_pins_runner", lambda eff: eff == "pinned")
    payload, _ = ops.server_settings(_state(tmp_path, config_text))
    assert payload["iosRunner"]["override"] is True


def test_unloadable_config_reads_as_no_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A wholesale load failure (not a single target's resolve) falls back to no override (BE-0318),
    # so a broken config reads as "no override" rather than 500-ing the whole tab.
    _no_bundle(monkeypatch)

    def _boom(_text: str) -> object:
        raise ValueError("bad config")

    monkeypatch.setattr(config_ops, "load_config", _boom)
    payload, _ = ops.server_settings(_state(tmp_path))
    assert payload["iosRunner"]["override"] is False


def test_testrunner_override_true_when_any_target_pins_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Any iOS target with an explicit testRunner flips the server-wide flag to True (BE-0318).
    _no_bundle(monkeypatch)
    config_text = (
        "defaults: { backend: [ios] }\n"
        "targets:\n"
        "  plain: { bundleId: com.example.plain }\n"
        "  pinned: { bundleId: com.example.pinned, xcuitest: { testRunner: /r/App.xctestrun } }\n"
    )
    payload, _ = ops.server_settings(_state(tmp_path, config_text))
    assert payload["iosRunner"]["override"] is True
