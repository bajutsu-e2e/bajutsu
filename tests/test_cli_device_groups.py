"""Tests for `run`'s device-group preflight against the config (BE-0447, unit 4).

The scenario model checks a group's shape; these refusals need the config: whether a group's
members can share one device (one platform, one device route, one system locale, no web target),
whether its starting members would overwrite each other's build, whether each `installApp.from`
target has a build to install, and whether a `setPrimaryTarget` names a member an earlier install
retired. Every refusal exits 2 before any device is acquired.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.scenario import Scenario
from bajutsu.run.cli import _reject_bad_device_groups


def _effs(tmp_path: Path, extra: str = "") -> dict[str, Effective]:
    build = tmp_path / "New.app"
    build.mkdir()
    cfg = load_config(
        f"""
defaults: {{ backend: [fake] }}
targets:
  old: {{ bundleId: com.example.showcase }}
  new: {{ bundleId: com.example.showcase, appPath: {build} }}
  auth: {{ bundleId: com.example.auth }}
  ja: {{ bundleId: com.example.ja, locale: ja_JP }}
  phone: {{ bundleId: com.example.phone, device: "iPhone 16" }}
  droid: {{ package: com.example.droid }}
  site: {{ baseUrl: "http://localhost:1/" }}
  nobuild: {{ bundleId: com.example.showcase }}
{extra}"""
    )
    return {name: resolve(cfg, name) for name in cfg.targets}


def _scenario(**fields: object) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": [{"tap": {"id": "a"}}]} | fields)


def _refused(tmp_path: Path, capsys: pytest.CaptureFixture[str], scenario: Scenario) -> str:
    with pytest.raises(typer.Exit) as exc:
        _reject_bad_device_groups([scenario], _effs(tmp_path), checkout_root=None)
    assert exc.value.exit_code == 2
    return capsys.readouterr().out


def test_the_update_journey_passes(tmp_path: Path) -> None:
    s = _scenario(
        targets=[["old", "new"]],
        primaryTarget="old",
        steps=[
            {"installApp": {"from": "new"}},
            {"setPrimaryTarget": {"target": "new"}},
            {"foreground": {}},
        ],
    )
    _reject_bad_device_groups([s], _effs(tmp_path), checkout_root=None)


def test_a_flat_scenario_is_never_checked(tmp_path: Path) -> None:
    s = _scenario(targets=["old", "site"], primaryTarget="old")
    _reject_bad_device_groups([s], _effs(tmp_path), checkout_root=None)


def test_starting_members_sharing_an_identifier_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _refused(
        tmp_path, capsys, _scenario(targets=[["old", "new"]], primaryTarget="old", installs=["new"])
    )
    assert "starting members 'old' and 'new' share the identifier 'com.example.showcase'" in out


def test_a_web_target_cannot_join_a_group(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _refused(
        tmp_path,
        capsys,
        _scenario(targets=[["old", "site"]], primaryTarget="old", installs=["site"]),
    )
    assert "web target 'site' has no device to share" in out


@pytest.mark.parametrize(
    ("other", "cause"),
    [
        ("droid", "platform or device route"),
        ("phone", "platform or device route"),
        ("ja", "locale"),
    ],
)
def test_members_that_cannot_share_a_device_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], other: str, cause: str
) -> None:
    out = _refused(
        tmp_path,
        capsys,
        _scenario(targets=[["auth", other]], primaryTarget="auth", installs=[other]),
    )
    assert cause in out


def test_a_scenario_locale_pins_every_member_alike(tmp_path: Path) -> None:
    # The scenario's own locale overrides each member's config, so the members agree again.
    s = _scenario(
        targets=[["auth", "ja"]],
        primaryTarget="auth",
        installs=["ja"],
        preconditions={"locale": "en_US"},
    )
    _reject_bad_device_groups([s], _effs(tmp_path), checkout_root=None)


def test_an_install_from_a_target_with_no_app_path_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _refused(
        tmp_path,
        capsys,
        _scenario(
            targets=[["old", "nobuild"]],
            primaryTarget="old",
            steps=[{"installApp": {"from": "nobuild"}}],
        ),
    )
    assert "installApp from 'nobuild': that target defines no appPath" in out


def test_an_install_whose_build_is_missing_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "Gone.app"
    effs = _effs(tmp_path)
    cfg = load_config(
        f"defaults: {{ backend: [fake] }}\ntargets:\n  gone: {{ bundleId: com.example.showcase, "
        f"appPath: {missing} }}\n"
    )
    effs["gone"] = resolve(cfg, "gone")
    s = _scenario(
        targets=[["old", "gone"]], primaryTarget="old", steps=[{"installApp": {"from": "gone"}}]
    )
    with pytest.raises(typer.Exit):
        _reject_bad_device_groups([s], effs, checkout_root=None)
    assert f"its appPath {str(missing)!r} does not exist" in capsys.readouterr().out


def test_moving_the_primary_to_a_retired_member_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _refused(
        tmp_path,
        capsys,
        _scenario(
            targets=[["old", "new"]],
            primaryTarget="old",
            steps=[
                {"installApp": {"from": "new"}},
                {"setPrimaryTarget": {"target": "old"}},
            ],
        ),
    )
    assert "setPrimaryTarget 'old': an earlier installApp retired that member" in out


def test_a_companion_install_retires_nothing(tmp_path: Path) -> None:
    effs = _effs(tmp_path)
    s = _scenario(
        targets=[["auth", "new"]],
        primaryTarget="auth",
        steps=[
            {"installApp": {"from": "new"}},
            {"setPrimaryTarget": {"target": "new"}},
            {"setPrimaryTarget": {"target": "auth"}},
        ],
    )
    _reject_bad_device_groups([s], effs, checkout_root=None)


def test_a_git_sourced_install_builds_on_demand_and_reports_a_failed_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "Built.app"
    effs = _effs(tmp_path)
    cfg = load_config(
        f"defaults: {{ backend: [fake] }}\ntargets:\n  built: {{ bundleId: com.example.showcase, "
        f"appPath: {missing}, build: 'false' }}\n"
    )
    effs["built"] = resolve(cfg, "built")
    s = _scenario(
        targets=[["old", "built"]], primaryTarget="old", steps=[{"installApp": {"from": "built"}}]
    )
    with pytest.raises(typer.Exit):
        _reject_bad_device_groups([s], effs, checkout_root=tmp_path)
    assert "installApp from 'built': build failed (exit 1): false" in capsys.readouterr().out


def test_an_install_in_before_retires_a_member_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _refused(
        tmp_path,
        capsys,
        _scenario(
            targets=[["old", "new"]],
            primaryTarget="old",
            before=[{"installApp": {"from": "new"}}],
            steps=[{"setPrimaryTarget": {"target": "old"}}],
        ),
    )
    assert "setPrimaryTarget 'old': an earlier installApp retired that member" in out


def test_a_shared_build_is_checked_once_for_the_whole_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    effs = _effs(tmp_path)
    one, two = (
        _scenario(
            name=name,
            targets=[["old", "nobuild"]],
            primaryTarget="old",
            steps=[{"installApp": {"from": "nobuild"}}],
        )
        for name in ("one", "two")
    )
    with pytest.raises(typer.Exit):
        _reject_bad_device_groups([one, two], effs, checkout_root=None)
    assert capsys.readouterr().out.count("that target defines no appPath") == 1
