"""Tests for `run`'s multi-target resolution and device bring-up (BE-0428).

`--target` becomes optional once every `--scenario` file declares its own `targets`, and every
declared name is resolved, device-leased, and pooled before the run starts. These cover the
selection rules and the bring-up bookkeeping with no device and no Simulator: the fake backend
stands in for a real one, and the device provider is the inert local one.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import typer

from bajutsu.cli._shared import LoadedConfig
from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.platform_lifecycle import ProvisionProfile
from bajutsu.common.runner.device_provider import DeviceLease
from bajutsu.common.runner.types import Lease, LeaseFn
from bajutsu.common.scenario import Scenario
from bajutsu.run.cli import (
    _acquire_targets,
    _close_pools,
    _declared_targets_in,
    _pool_demand,
    _reject_incompatible_actuator_sharing,
    _reject_self_declaring_in_dir,
    _reject_web_flags_across_targets,
    _release_devices,
    _resolve_multi_target_workers,
    _resolve_primary_target,
    _resolve_target_effs,
    _TargetSetup,
)

_CONFIG = """
defaults: { backend: [fake] }
targets:
  app: { bundleId: com.example.app }
  site: { baseUrl: 'http://localhost:1/' }
"""


def _effs() -> dict[str, Effective]:
    cfg = load_config(_CONFIG)
    return {"app": resolve(cfg, "app"), "site": resolve(cfg, "site")}


# --- reading a file's declared targets --------------------------------------------------------


def test_declared_targets_reads_them_before_any_expansion(tmp_path: Path) -> None:
    scn = tmp_path / "cross.yaml"
    scn.write_text(
        "- name: cross\n  targets: [app, site]\n  steps:\n    - target: app\n      tap: { id: a }\n",
        encoding="utf-8",
    )
    assert _declared_targets_in(scn) == ["app", "site"]


def test_declared_targets_is_empty_for_a_legacy_scenario(tmp_path: Path) -> None:
    scn = tmp_path / "legacy.yaml"
    scn.write_text("- name: legacy\n  steps:\n    - tap: { id: a }\n", encoding="utf-8")
    assert _declared_targets_in(scn) == []


def test_declared_targets_exits_2_on_an_unparseable_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The pre-pass reads the file before the ordinary load does, so its own parse errors have to
    # exit cleanly rather than surface as a traceback from outside any handler.
    scn = tmp_path / "broken.yaml"
    scn.write_text("- name: broken\n  steps: [{ nope: 1 }]\n", encoding="utf-8")
    with pytest.raises(typer.Exit) as exc:
        _declared_targets_in(scn)
    assert exc.value.exit_code == 2
    assert "broken.yaml" in capsys.readouterr().out


# --- resolving the primary target -------------------------------------------------------------


def test_an_explicit_target_is_the_primary_unchanged() -> None:
    assert _resolve_primary_target("demo", []) == "demo"


def test_omitting_target_takes_the_first_files_first_declared_name(tmp_path: Path) -> None:
    scn = tmp_path / "cross.yaml"
    scn.write_text(
        "- name: cross\n  targets: [site, app]\n  steps:\n    - target: site\n      tap: { id: a }\n",
        encoding="utf-8",
    )
    assert _resolve_primary_target("", [str(scn)]) == "site"


def test_omitting_target_without_any_scenario_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The directory-glob shorthand belongs to one target, so a run driven by self-declaring
    # scenarios has to name its files.
    with pytest.raises(typer.Exit) as exc:
        _resolve_primary_target("", [])
    assert exc.value.exit_code == 2
    assert "--scenario" in capsys.readouterr().out


def test_omitting_target_with_a_legacy_file_exits_2_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scn = tmp_path / "legacy.yaml"
    scn.write_text("- name: legacy\n  steps:\n    - tap: { id: a }\n", encoding="utf-8")
    with pytest.raises(typer.Exit) as exc:
        _resolve_primary_target("", [str(scn)])
    assert exc.value.exit_code == 2
    assert "legacy.yaml" in capsys.readouterr().out


def test_omitting_target_with_a_missing_file_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(typer.Exit) as exc:
        _resolve_primary_target("", [str(tmp_path / "absent.yaml")])
    assert exc.value.exit_code == 2
    assert "not found" in capsys.readouterr().out


# --- the scenarios-dir rejection ---------------------------------------------------------------


def test_the_scenarios_dir_refuses_a_self_declaring_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Rejected at discovery: a matching name would silently launch every other target the file
    # declares, and a mismatching one would fail every other scenario in the same batch.
    legacy = tmp_path / "ok.yaml"
    legacy.write_text("- name: ok\n  steps:\n    - tap: { id: a }\n", encoding="utf-8")
    cross = tmp_path / "cross.yaml"
    cross.write_text(
        "- name: cross\n  targets: [app, site]\n  steps:\n    - target: app\n      tap: { id: a }\n",
        encoding="utf-8",
    )
    with pytest.raises(typer.Exit) as exc:
        _reject_self_declaring_in_dir([legacy, cross], "app")
    assert exc.value.exit_code == 2
    out = capsys.readouterr().out
    assert "cross.yaml" in out
    assert "--scenario" in out


def test_the_scenarios_dir_passes_a_suite_of_legacy_files(tmp_path: Path) -> None:
    scn = tmp_path / "ok.yaml"
    scn.write_text("- name: ok\n  steps:\n    - tap: { id: a }\n", encoding="utf-8")
    _reject_self_declaring_in_dir([scn], "app")  # no exception


# --- resolving every declared name against the config ------------------------------------------


def test_every_declared_name_resolves_against_the_loaded_config() -> None:
    effs = _effs()
    scenarios = [
        Scenario.model_validate(
            {
                "name": "cross",
                "targets": ["app", "site"],
                "steps": [{"target": "app", "tap": {"id": "a"}}],
            }
        )
    ]
    # The primary's own already-resolved config is reused, so a `--headed` / `--browser` override
    # applied to it is not silently dropped for the target it was meant for.
    resolved = _resolve_target_effs(_loaded(), scenarios, "app", effs["app"])
    assert sorted(resolved) == ["app", "site"]
    assert resolved["app"] is effs["app"]


def test_headed_and_browser_apply_to_a_non_primary_target_too() -> None:
    # A run's single web target need not be the primary — `_reject_web_flags_across_targets` is
    # what refuses the flags outright once a run declares two, so short of that this target must
    # still see `--headed`/`--browser` the same as it would if it had been named `--target` itself.
    from bajutsu.common.config import WebConfig

    effs = _effs()
    scenarios = [
        Scenario.model_validate(
            {
                "name": "cross",
                "targets": ["app", "site"],
                "steps": [{"target": "app", "tap": {"id": "a"}}],
            }
        )
    ]
    resolved = _resolve_target_effs(
        _loaded(), scenarios, "app", effs["app"], headed=True, browser="firefox"
    )
    site_config = resolved["site"].platform_config
    assert isinstance(site_config, WebConfig)
    assert site_config.headless is False
    assert site_config.browser == "firefox"


def _loaded() -> LoadedConfig:
    """The parsed `_CONFIG`, wrapped the way the shared CLI loader hands it over."""
    return LoadedConfig(
        config=load_config(_CONFIG), path=Path("bajutsu.config.yaml"), source=None, root=None
    )


def test_an_unknown_declared_name_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    effs = _effs()
    scenarios = [
        Scenario.model_validate(
            {
                "name": "cross",
                "targets": ["app", "ghost"],
                "steps": [{"target": "app", "tap": {"id": "a"}}],
            }
        )
    ]
    with pytest.raises(typer.Exit) as exc:
        _resolve_target_effs(_loaded(), scenarios, "app", effs["app"])
    assert exc.value.exit_code == 2
    assert "ghost" in capsys.readouterr().out


# --- the web engine flags ----------------------------------------------------------------------


def test_the_web_flags_are_refused_across_two_web_targets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = load_config(
        "defaults: { backend: [fake] }\n"
        "targets:\n"
        "  a: { baseUrl: 'http://localhost:1/' }\n"
        "  b: { baseUrl: 'http://localhost:2/' }\n"
    )
    effs = {"a": resolve(cfg, "a"), "b": resolve(cfg, "b")}
    with pytest.raises(typer.Exit) as exc:
        _reject_web_flags_across_targets(effs, headed=True, browser="", browsers="")
    assert exc.value.exit_code == 2
    out = capsys.readouterr().out
    assert "a" in out and "b" in out


def test_the_web_flags_pass_with_one_web_target() -> None:
    _reject_web_flags_across_targets(_effs(), headed=True, browser="", browsers="")


def test_two_web_targets_without_the_flags_are_fine() -> None:
    cfg = load_config(
        "defaults: { backend: [fake] }\n"
        "targets:\n"
        "  a: { baseUrl: 'http://localhost:1/' }\n"
        "  b: { baseUrl: 'http://localhost:2/' }\n"
    )
    effs = {"a": resolve(cfg, "a"), "b": resolve(cfg, "b")}
    _reject_web_flags_across_targets(effs, headed=None, browser="", browsers="")


# --- device bring-up and pools ------------------------------------------------------------------


def _setup(
    name: str,
    actuator: str,
    udids: list[str],
    released: list[str],
    *,
    udid_spec: str = "booted",
) -> _TargetSetup:
    effs = _effs()
    return _TargetSetup(
        name=name,
        eff=effs["app" if name == "app" else "site"],
        actuator=actuator,
        backends=["fake"],
        device=DeviceLease(
            udid_spec=udid_spec,
            provision=ProvisionProfile(),
            release=lambda: released.append(name),
        ),
        udids=udids,
        workers=len(udids),
    )


def test_acquiring_every_declared_target_leases_one_device_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquired: list[str] = []

    def fake_acquire(eff: object, udid: str) -> DeviceLease:
        acquired.append(getattr(eff, "target", "?"))
        return DeviceLease(udid_spec=udid, provision=ProvisionProfile(), release=lambda: None)

    monkeypatch.setattr("bajutsu.run.cli.acquire_device", fake_acquire)
    monkeypatch.setattr(
        "bajutsu.run.cli.environment_for",
        lambda *a, **k: _FakeEnv(),
    )
    setups = _acquire_targets(_effs(), "fake", [], "booted", 1)
    assert sorted(setups) == ["app", "site"]
    assert sorted(acquired) == ["app", "site"]


def test_a_failed_acquisition_hands_back_what_was_already_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cloud device reserved for the first target must not leak when the second target's own
    # provider fails.
    released: list[str] = []
    calls: list[str] = []

    def fake_acquire(eff: object, udid: str) -> DeviceLease:
        name = getattr(eff, "target", "?")
        calls.append(name)
        if len(calls) == 2:
            raise RuntimeError("provider down")
        return DeviceLease(
            udid_spec=udid, provision=ProvisionProfile(), release=lambda: released.append(name)
        )

    monkeypatch.setattr("bajutsu.run.cli.acquire_device", fake_acquire)
    monkeypatch.setattr(
        "bajutsu.run.cli.environment_for",
        lambda *a, **k: _FakeEnv(),
    )
    with pytest.raises(RuntimeError, match="provider down"):
        _acquire_targets(_effs(), "fake", [], "booted", 1)
    assert len(released) == 1


def test_releasing_devices_warns_and_continues_when_one_raises(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A provider's teardown failure must not flip the machine-only verdict, and must not stop the
    # remaining targets being handed back.
    released: list[str] = []
    boom = _setup("app", "fake", ["UD-1"], released)
    boom = _TargetSetup(
        name="app",
        eff=boom.eff,
        actuator="fake",
        backends=["fake"],
        device=DeviceLease(udid_spec="booted", provision=ProvisionProfile(), release=_raise),
        udids=["UD-1"],
        workers=1,
    )
    _release_devices({"app": boom, "site": _setup("site", "playwright", ["web-0"], released)})
    assert released == ["site"]
    assert "device release for target 'app' failed" in capsys.readouterr().err


def _raise() -> None:
    raise RuntimeError("teardown exploded")


class _FakeEnv:
    """Just enough `RunEnvironment` for lane resolution: a udid resolver that echoes its input."""

    def resolve_device(self, udid: str) -> str:
        return udid


# --- pool demand and worker capping --------------------------------------------------------------


def test_two_targets_on_one_pool_demand_two_devices() -> None:
    released: list[str] = []
    setups = {
        "app": _setup("app", "fake", ["UD-1", "UD-2"], released),
        "site": _setup("site", "fake", ["UD-1", "UD-2"], released),
    }
    scenarios = [
        Scenario.model_validate(
            {
                "name": "cross",
                "targets": ["app", "site"],
                "steps": [{"target": "app", "tap": {"id": "a"}}],
            }
        )
    ]
    assert _pool_demand(scenarios, setups) == {"fake": 2}


def test_two_targets_on_two_pools_demand_one_device_each() -> None:
    released: list[str] = []
    setups = {
        "app": _setup("app", "fake", ["UD-1"], released),
        "site": _setup("site", "playwright", ["web-0"], released),
    }
    scenarios = [
        Scenario.model_validate(
            {
                "name": "cross",
                "targets": ["app", "site"],
                "steps": [{"target": "app", "tap": {"id": "a"}}],
            }
        )
    ]
    assert _pool_demand(scenarios, setups) == {"fake": 1, "playwright": 1}


def test_a_pool_with_too_few_devices_is_refused_up_front(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Refused rather than left to block on a queue that will never free a device.
    released: list[str] = []
    setups = {
        "app": _setup("app", "fake", ["UD-1"], released),
        "site": _setup("site", "fake", ["UD-1"], released),
    }
    scenarios = [
        Scenario.model_validate(
            {
                "name": "cross",
                "targets": ["app", "site"],
                "steps": [{"target": "app", "tap": {"id": "a"}}],
            }
        )
    ]
    with pytest.raises(typer.Exit) as exc:
        _resolve_multi_target_workers(scenarios, setups, 1)
    assert exc.value.exit_code == 2
    assert "--udid" in capsys.readouterr().out


def test_workers_are_capped_to_what_the_pools_can_serve() -> None:
    # Each worker holds one device per declared target for its scenario's whole length, so four
    # devices serve at most two concurrent two-target scenarios.
    released: list[str] = []
    setups = {
        "app": _setup("app", "fake", ["a", "b", "c", "d"], released),
        "site": _setup("site", "fake", ["a", "b", "c", "d"], released),
    }
    scenarios = [
        Scenario.model_validate(
            {
                "name": "cross",
                "targets": ["app", "site"],
                "steps": [{"target": "app", "tap": {"id": "a"}}],
            }
        )
    ]
    assert _resolve_multi_target_workers(scenarios, setups, 4) == 2


def test_a_single_target_run_keeps_its_requested_workers() -> None:
    released: list[str] = []
    setups = {"app": _setup("app", "fake", ["a", "b"], released)}
    scenarios = [Scenario.model_validate({"name": "legacy", "steps": [{"tap": {"id": "a"}}]})]
    assert _resolve_multi_target_workers(scenarios, setups, 2) == 2


# --- refusing an actuator two targets would otherwise silently share ---------------------------


def test_two_same_actuator_targets_sharing_one_provider_are_fine() -> None:
    # The common case: both `booted` (or both naming the same explicit `--udid`) resolve the same
    # `udid_spec`, so sharing `_open_pools`'s one pool for that actuator is exactly what either
    # target would have built alone.
    released: list[str] = []
    setups = {
        "app": _setup("app", "fake", ["UD-1"], released, udid_spec="booted"),
        "site": _setup("site", "fake", ["UD-1"], released, udid_spec="booted"),
    }
    _reject_incompatible_actuator_sharing(setups)  # does not raise


def test_two_same_actuator_targets_on_different_devices_are_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A second target's own `deviceProvider` (BE-0236) reserved a different device than the first
    # target sharing its actuator — `_open_pools` would build the shared pool from the first
    # target's device alone, silently running the second target on a device its own provider never
    # gave it (and, for a device-cloud provider, leaving that reservation billed but never driven).
    released: list[str] = []
    setups = {
        "ios-local": _setup("app", "xcuitest", ["local-udid"], released, udid_spec="local-udid"),
        "ios-cloud": _setup("site", "xcuitest", ["cloud-udid"], released, udid_spec="cloud-udid"),
    }
    with pytest.raises(typer.Exit) as exc:
        _reject_incompatible_actuator_sharing(setups)
    assert exc.value.exit_code == 2
    out = capsys.readouterr().out
    assert "ios-local" in out and "ios-cloud" in out and "xcuitest" in out


def test_two_targets_on_different_actuators_are_never_compared() -> None:
    # Nothing to refuse here: each actuator gets its own pool regardless of udid_spec, so two
    # different devices under two different actuators is the ordinary cross-platform case.
    released: list[str] = []
    setups = {
        "app": _setup("app", "fake", ["UD-1"], released, udid_spec="UD-1"),
        "site": _setup("site", "playwright", ["web-0"], released, udid_spec="web-0"),
    }
    _reject_incompatible_actuator_sharing(setups)  # does not raise


# --- _close_pools: never mask a bring-up error, but still surface a genuine teardown defect ----


def _pool_pair(shutdown: Callable[[], None]) -> dict[str, tuple[LeaseFn, Callable[[], None]]]:
    def _lease_unused(eff: Effective, s: Scenario) -> Lease:
        raise AssertionError("this pool's lease is never called in a teardown-only test")

    return {"fake": (_lease_unused, shutdown)}


def test_close_pools_raises_the_first_teardown_defect_when_nothing_else_is_in_flight() -> None:
    def boom() -> None:
        raise RuntimeError("wiring defect")

    with pytest.raises(RuntimeError, match="wiring defect"):
        _close_pools(_pool_pair(boom))


def test_close_pools_mid_run_swallows_and_warns_instead_of_raising(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom() -> None:
        raise RuntimeError("wiring defect")

    _close_pools(_pool_pair(boom), mid_run=True)  # must not raise
    assert "wiring defect" in capsys.readouterr().err


def _raise_the_real_failure() -> None:
    def boom() -> None:
        raise RuntimeError("teardown defect")

    try:
        raise ValueError("the real failure")
    finally:
        _close_pools(_pool_pair(boom))  # mid_run not passed — self-detects via sys.exc_info()


def test_close_pools_never_masks_an_exception_already_propagating(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # BE-0428 regression: called from a `finally` while a different exception unwinds, raising the
    # first teardown defect would silently replace it — even with `mid_run` left at its default.
    with pytest.raises(ValueError, match="the real failure"):
        _raise_the_real_failure()
    assert "teardown defect" in capsys.readouterr().err
