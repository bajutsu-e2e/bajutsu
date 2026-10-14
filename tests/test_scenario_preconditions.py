"""A scenario's preconditions grouped as the target's `app`, `runsOn`, and `run` (BE-0452 unit 7).

Covers the model's refusal of the old flat keys, the merge over each target's config, the checks a
run makes against the platforms it drives before any device work, and the upgrade that keeps a run
stored before the regrouping loadable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from run_cli import invoke
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli._shared import _refuse_bad_preconditions
from bajutsu.common import _yaml
from bajutsu.common.config import (
    Effective,
    check_preconditions,
    command_preconditions,
    load_config,
    resolve,
    resolve_preconditions,
    scenario_conditions,
)
from bajutsu.common.report.rows import _preconditions_rows
from bajutsu.common.scenario import Preconditions, Scenario, load_scenarios
from bajutsu.common.scenario.load import _upgraded_scenario_text, load_stored_scenario_file
from bajutsu.common.scenario.serialize import scenario_dict

_CONFIG = """
targets:
  phone:
    platform: ios
    app:
      id: com.example.phone
      reinstall: overwrite
      launch: { env: { A: "1" }, args: ["-target"], deeplink: "app://home" }
    runsOn: { locale: ja_JP, seedPhotos: [photos/red.png] }
    run: { erase: true }
  droid:
    platform: android
    app: { id: com.example.droid, launch: { deeplink: "app://droid" } }
  site:
    platform: web
    app: { url: "http://localhost:1/" }
  fake:
    platform: fake
"""


def _effs() -> dict[str, Effective]:
    cfg = load_config(_CONFIG)
    return {name: resolve(cfg, name) for name in cfg.targets}


def _pre(text: str) -> Preconditions:
    return load_scenarios(f"- name: s\n  preconditions: {text}\n  steps: []\n")[0].preconditions


# --- the model ---


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("erase: true", "preconditions.run.erase"),
        ("reinstall: clean", "preconditions.app.reinstall"),
        ("launchEnv: { A: '1' }", "preconditions.app.launch.env"),
        ("launchArgs: [-x]", "preconditions.app.launch.args"),
        ("deeplink: 'app://x'", "preconditions.app.launch.deeplink"),
        ("locale: ja_JP", "preconditions.runsOn.ios.locale"),
        ("seedPhotos: [a.png]", "preconditions.runsOn.ios.seedPhotos"),
    ],
)
def test_a_flat_key_fails_naming_its_new_home(old: str, new: str) -> None:
    with pytest.raises(ValueError, match=f"moved to '{new}'"):
        _pre(f"{{ {old} }}")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("systemAlertHandling: false", "preconditions.run.systemAlertHandling"),
        ("iosTipKitHandling: true", "preconditions.run.tipKitHandling"),
    ],
)
def test_a_top_level_run_field_fails_naming_its_new_home(old: str, new: str) -> None:
    with pytest.raises(ValueError, match=f"moved to '{new}'"):
        load_scenarios(f"- name: s\n  {old}\n  steps: []\n")


def test_a_scenario_cannot_set_a_field_that_identifies_the_app() -> None:
    with pytest.raises(ValueError, match="id"):
        _pre("{ app: { id: com.example.other } }")


def test_a_non_mapping_preconditions_fails_as_a_type_error() -> None:
    with pytest.raises(ValueError, match="dictionary"):
        Preconditions.model_validate("erase")


def test_seed_photo_paths_resolve_only_where_a_block_lists_them() -> None:
    pre = _pre("{ runsOn: { ios: { locale: ja_JP, seedPhotos: [a.png] }, web: { browser: {} } } }")
    pre.resolve_seed_photos(lambda ref: f"/root/{ref}")
    assert pre.runs_on == {
        "ios": {"locale": "ja_JP", "seedPhotos": ["/root/a.png"]},
        "web": {"browser": {}},
    }


def test_the_groups_round_trip_through_a_dump() -> None:
    text = (
        "- name: s\n"
        "  preconditions:\n"
        "    app: { reinstall: overwrite, launch: { env: { A: '1' }, deeplink: 'app://x' } }\n"
        "    runsOn: { ios: { locale: ja_JP } }\n"
        "    run: { erase: true, tipKitHandling: true, systemAlertHandling: false }\n"
        "  steps: []\n"
    )
    (scenario,) = load_scenarios(text)
    dumped = scenario_dict(scenario)
    assert Scenario.model_validate(dumped) == scenario
    assert dumped["preconditions"]["run"] == {
        "erase": True,
        "systemAlertHandling": False,
        "tipKitHandling": True,
    }


# --- the merge over a target ---


def test_the_new_target_fields_resolve_onto_effective() -> None:
    effs = _effs()
    phone = effs["phone"]
    assert (phone.reinstall, phone.deeplink, phone.seed_photos) == (
        "overwrite",
        "app://home",
        ["photos/red.png"],
    )
    assert phone.schema_platform == "ios"
    assert effs["droid"].deeplink == "app://droid"
    # A `fake` target resolves to the iOS-shaped sub-config but keeps its own platform for checks.
    assert (effs["fake"].platform, effs["fake"].schema_platform) == ("ios", "fake")


def test_seed_photos_rebase_against_the_config_file(tmp_path: Path) -> None:
    rebased = _effs()["phone"].rebased(tmp_path, confine=False)
    assert rebased.seed_photos == [str(tmp_path / "photos/red.png")]


def test_the_target_fills_what_the_scenario_leaves_unset() -> None:
    merged = resolve_preconditions(_effs()["phone"], Preconditions())
    assert merged.reinstall == "overwrite"
    assert merged.deeplink == "app://home"
    assert merged.erase is True
    assert merged.seed_photos == ["photos/red.png"]
    assert merged.resolved_locale("en_US") == "en_US"  # the target's locale stays on `Effective`


def test_the_scenario_wins_over_the_target() -> None:
    pre = _pre(
        "{ app: { reinstall: clean, launch: { env: { B: '2' }, args: [-s], deeplink: 'app://s' } },"
        " runsOn: { ios: { locale: en_GB, seedPhotos: [] } }, run: { erase: false } }"
    )
    merged = resolve_preconditions(_effs()["phone"], pre)
    assert merged.reinstall == "clean"
    assert merged.deeplink == "app://s"
    assert merged.erase is False
    assert merged.seed_photos == []  # a scenario list replaces the target's
    assert merged.resolved_locale("ja_JP") == "en_GB"
    # The scenario's launch additions stay separate: the environment merges them over the target's.
    assert (merged.launch_env, merged.launch_args) == ({"B": "2"}, ["-s"])


def test_reinstall_falls_back_to_the_built_in_clean() -> None:
    assert resolve_preconditions(_effs()["droid"], Preconditions()).reinstall == "clean"


def test_a_block_for_another_platform_has_no_effect() -> None:
    pre = _pre("{ runsOn: { ios: { locale: en_GB } } }")
    assert resolve_preconditions(_effs()["droid"], pre).locale is None


def test_a_command_with_no_scenario_takes_the_target_and_its_own_erase() -> None:
    merged = command_preconditions(_effs()["phone"], erase=False)
    assert (merged.erase, merged.deeplink) == (False, "app://home")


# --- the checks against the driven platforms ---


def test_an_app_field_no_driven_platform_has_fails() -> None:
    (problem,) = check_preconditions(_pre("{ app: { launch: { args: [-x] } } }"), [_effs()["site"]])
    assert problem.startswith("preconditions.app.launch.args applies to no target")
    assert "(platform web)" in problem


def test_an_app_field_one_driven_platform_has_passes() -> None:
    effs = _effs()
    pre = _pre("{ app: { launch: { args: [-x] }, reinstall: overwrite } }")
    assert check_preconditions(pre, [effs["phone"], effs["site"]]) == []


def test_the_fake_platform_has_no_launch_fields() -> None:
    (problem,) = check_preconditions(
        _pre("{ app: { launch: { env: { A: '1' } } } }"), [_effs()["fake"]]
    )
    assert "preconditions.app.launch.env" in problem


def test_an_ios_only_run_field_fails_without_an_ios_target() -> None:
    effs = _effs()
    pre = _pre("{ run: { tipKitHandling: true } }")
    (problem,) = check_preconditions(pre, [effs["droid"]])
    assert problem.startswith("preconditions.run.tipKitHandling applies to no target")
    assert check_preconditions(pre, [effs["phone"]]) == []
    # The shared run fields apply everywhere.
    assert check_preconditions(_pre("{ run: { erase: true } }"), [effs["site"]]) == []


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("{ flutter: { os: '>=1' } }", "invalid platform 'flutter'"),
        ("{ ios: { kind: device } }", "preconditions.runsOn.ios.kind: a scenario cannot set"),
        ("{ web: { emulate: iPhone 13 } }", "preconditions.runsOn.web.emulate: a scenario cannot"),
        ("{ android: { locale: ja_JP } }", "preconditions.runsOn.android.locale: a scenario"),
        ("{ ios: { os: '18.x.y.z' } }", "os"),
    ],
)
def test_a_malformed_runs_on_block_fails_even_for_an_undriven_platform(
    block: str, message: str
) -> None:
    problems = check_preconditions(_pre(f"{{ runsOn: {block} }}"), [_effs()["site"]])
    assert any(message in p for p in problems), problems


def test_scenario_conditions_name_only_the_driven_platforms() -> None:
    pre = _pre(
        "{ runsOn: { ios: { model: iPhone 16, os: '>=18', locale: ja_JP },"
        " web: { browser: { version: '>=120' } } } }"
    )
    assert scenario_conditions(pre, ["ios"]) == ["runsOn.ios.model", "runsOn.ios.os"]
    assert scenario_conditions(pre, ["web"]) == ["runsOn.web.browser.version"]
    assert scenario_conditions(pre, ["android"]) == []


def test_a_scenario_condition_is_refused_until_matching_lands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    (scenario,) = load_scenarios(
        "- name: s\n  preconditions: { runsOn: { ios: { os: '>=18' } } }\n  steps: []\n"
    )
    with pytest.raises(typer.Exit) as exc:
        _refuse_bad_preconditions([(scenario, [_effs()["phone"]])], erase=None, fake=False)
    assert exc.value.exit_code == 2
    assert "preconditions.runsOn.ios.os" in capsys.readouterr().out
    # The fake backend evaluates no condition, so it lets the run through.
    _refuse_bad_preconditions([(scenario, [_effs()["phone"]])], erase=None, fake=True)


_WEB_CONFIG = """
targets:
  site:
    platform: web
    app: {{ url: "http://localhost:1/" }}
    paths: {{ scenarios: {scenarios} }}
"""


def test_run_refuses_a_field_no_driven_target_has_before_any_device_work(tmp_path: Path) -> None:
    scn = tmp_path / "s.yaml"
    scn.write_text(
        "- name: args on the web\n"
        "  preconditions: { app: { launch: { args: [-x] } } }\n"
        "  steps: [{ tap: { id: a } }]\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(_WEB_CONFIG.format(scenarios=tmp_path), encoding="utf-8")
    r = invoke(
        CliRunner(), app, ["run", "--scenario", str(scn), "--target", "site", "--config", str(cfg)]
    )
    assert r.exit_code == 2
    assert "args on the web: preconditions.app.launch.args applies to no target" in r.output


# --- stored runs and the report ---


_PRE_REGROUP_PLAN = (
    "- name: old\n"
    "  systemAlertHandling: false\n"
    "  iosTipKitHandling: true\n"
    "  preconditions:\n"
    "    erase: true\n"
    "    reinstall: overwrite\n"
    "    launchEnv: { A: '1' }\n"
    "    launchArgs: [-x]\n"
    "    deeplink: 'app://x'\n"
    "    locale: ja_JP\n"
    "    seedPhotos: [red.png]\n"
    "  steps: []\n"
)


def test_a_plan_stored_before_the_regrouping_still_loads() -> None:
    (scenario,) = load_stored_scenario_file(_PRE_REGROUP_PLAN).scenarios
    pre = scenario.preconditions
    assert (pre.run.erase, pre.run.system_alert_handling, pre.run.tip_kit_handling) == (
        True,
        False,
        True,
    )
    assert pre.app.reinstall == "overwrite"
    assert (pre.app.launch.env, pre.app.launch.args, pre.app.launch.deeplink) == (
        {"A": "1"},
        ["-x"],
        "app://x",
    )
    assert pre.runs_on == {"ios": {"locale": "ja_JP", "seedPhotos": ["red.png"]}}


def test_the_upgrade_leaves_a_regrouped_plan_alone() -> None:
    text = "- name: s\n  preconditions: { run: { erase: true } }\n  steps: []\n"
    assert _yaml.safe_load(_upgraded_scenario_text(text)) == _yaml.safe_load(text)


def test_the_report_rows_read_the_groups() -> None:
    (scenario,) = load_stored_scenario_file(_PRE_REGROUP_PLAN).scenarios
    rows = _preconditions_rows(scenario_dict(scenario))
    assert rows == [
        ("erase", "true"),
        ("reinstall", "overwrite"),
        ("deeplink", "app://x"),
        ("locale", "ja_JP"),
        ("A", "1"),
        ("launch args", "-x"),
    ]
