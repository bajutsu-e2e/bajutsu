"""A scenario's preconditions grouped as the target's `app` and `run` (BE-0452 unit 7), with its
device state under `device` (BE-0469).

Covers the model's refusal of the old flat keys and of `preconditions.runsOn`, the merge over each
target's config, the checks a run makes against the platforms it drives before any device work, and
the upgrade that keeps a run stored before either regrouping loadable.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
import typer
from run_cli import invoke
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli._shared import (
    LoadedConfig,
    _command_preconditions_or_exit,
    _precondition_checks,
    _refuse_bad_preconditions,
)
from bajutsu.common import _yaml
from bajutsu.common.config import (
    Effective,
    ResolvedPreconditions,
    check_preconditions,
    command_preconditions,
    load_config,
    resolve,
    resolve_preconditions,
)
from bajutsu.common.lint import lint_diagnostics, lint_text
from bajutsu.common.report.rows import _preconditions_rows
from bajutsu.common.scenario import Preconditions, Scenario, load_scenarios
from bajutsu.common.scenario.load import _upgraded_scenario_text, load_stored_scenario_file
from bajutsu.common.scenario.serialize import scenario_dict

_CONFIG = """
targets:
  phone:
    platform: ios
    subject:
      id: com.example.phone
      reinstall: erase
      launch: { env: { A: "1" }, args: ["-target"], deeplink: "app://home" }
    runsOn: {ios: {locale: ja_JP, seedPhotos: [photos/red.png]}}
  droid:
    platform: android
    subject: { id: com.example.droid, launch: { deeplink: "app://droid" } }
  site:
    platform: web
    subject: { url: "http://localhost:1/" }
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
        ("erase: true", "preconditions.subject.reinstall: erase"),
        ("reinstall: clean", "preconditions.subject.reinstall"),
        ("launchEnv: { A: '1' }", "preconditions.subject.launch.env"),
        ("launchArgs: [-x]", "preconditions.subject.launch.args"),
        ("deeplink: 'app://x'", "preconditions.subject.launch.deeplink"),
        ("locale: ja_JP", "preconditions.device.ios.locale"),
        ("seedPhotos: [a.png]", "preconditions.device.ios.seedPhotos"),
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
        _pre("{ subject: { id: com.example.other } }")


def test_a_non_mapping_preconditions_fails_as_a_type_error() -> None:
    with pytest.raises(ValueError, match="dictionary"):
        Preconditions.model_validate("erase")


def test_seed_photo_paths_resolve_only_where_a_block_lists_them() -> None:
    pre = _pre("{ device: { ios: { locale: ja_JP, seedPhotos: [a.png] } } }")
    pre.resolve_seed_photos(lambda ref: f"/root/{ref}")
    assert (pre.device.ios.locale, pre.device.ios.seed_photos) == ("ja_JP", ["/root/a.png"])
    unset = _pre("{ device: { ios: { locale: ja_JP } } }")
    unset.resolve_seed_photos(lambda ref: f"/root/{ref}")
    assert unset.device.ios.seed_photos is None


def test_the_groups_round_trip_through_a_dump() -> None:
    text = (
        "- name: s\n"
        "  preconditions:\n"
        "    subject: { reinstall: overwrite, launch: { env: { A: '1' }, deeplink: 'app://x' } }\n"
        "    device: { ios: { locale: ja_JP } }\n"
        "    run: { tipKitHandling: true, systemAlertHandling: false }\n"
        "  steps: []\n"
    )
    (scenario,) = load_scenarios(text)
    dumped = scenario_dict(scenario)
    assert Scenario.model_validate(dumped) == scenario
    assert dumped["preconditions"]["run"] == {
        "systemAlertHandling": False,
        "tipKitHandling": True,
    }


# --- the merge over a target ---


def test_the_new_target_fields_resolve_onto_effective() -> None:
    effs = _effs()
    phone = effs["phone"]
    assert (phone.reinstall, phone.deeplink, phone.seed_photos) == (
        "erase",
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
    assert merged.reinstall == "erase"
    assert merged.deeplink == "app://home"
    assert merged.erase is True
    assert merged.seed_photos == ["photos/red.png"]
    assert merged.resolved_locale("en_US") == "en_US"  # the target's locale stays on `Effective`


def test_the_scenario_wins_over_the_target() -> None:
    pre = _pre(
        "{ subject: { reinstall: clean, launch: { env: { B: '2' }, args: [-s], deeplink: 'app://s' } },"
        " device: { ios: { locale: en_GB, seedPhotos: [] } } }"
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
    pre = _pre("{ device: { ios: { locale: en_GB } } }")
    assert resolve_preconditions(_effs()["droid"], pre).locale is None
    assert pre.seed_photos_for("android") is None


def test_a_command_with_no_scenario_takes_the_target_and_its_own_erase() -> None:
    merged = command_preconditions(_effs()["phone"], erase=True)
    assert (merged.erase, merged.deeplink, merged.seed_photos) == (
        True,
        "app://home",
        ["photos/red.png"],
    )
    assert command_preconditions(_effs()["droid"], erase=False).erase is False
    # No flag keeps the target's own rung.
    assert command_preconditions(_effs()["phone"], erase=None).reinstall == "erase"


@pytest.mark.parametrize(
    ("merged", "flag", "want"),
    [
        ("overwrite", True, "erase"),
        ("clean", True, "erase"),
        ("erase", False, "clean"),
        ("overwrite", False, "overwrite"),  # `--no-erase` never lowers below what was asked
        ("clean", False, "clean"),
        ("erase", None, "erase"),
        ("overwrite", None, "overwrite"),
    ],
)
def test_the_erase_flag_moves_the_reinstall_ladder(
    merged: str, flag: bool | None, want: str
) -> None:
    pre = ResolvedPreconditions(reinstall=merged)  # type: ignore[arg-type]
    assert pre.with_erase(flag).reinstall == want


def test_a_run_erase_in_preconditions_names_its_new_home() -> None:
    with pytest.raises(
        ValueError, match=r"preconditions\.run\.erase moved to preconditions\.subject\.reinstall"
    ):
        _pre("{ run: { erase: true } }")


def test_a_command_refuses_no_erase_on_a_target_that_seeds_photos(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # `record`, `crawl`, and `repl` seed only on the erase path, so `--no-erase` would seed nothing.
    with pytest.raises(typer.Exit) as exc:
        _command_preconditions_or_exit(_effs()["phone"], erase=False)
    assert exc.value.exit_code == 2
    assert "runsOn.ios.seedPhotos" in capsys.readouterr().out


def test_a_fake_target_runs_under_the_scenario_ios_locale() -> None:
    # A `fake` target resolves to the iOS-shaped sub-config, so the scenario's iOS block applies.
    pre = _pre("{ device: { ios: { locale: ja_JP } } }")
    fake = _effs()["fake"]
    assert resolve_preconditions(fake, pre).locale == "ja_JP"
    assert pre.resolved_locale(fake.locale, fake.platform) == "ja_JP"


def test_seed_photos_on_a_non_primary_target_are_refused() -> None:
    effs = _effs()
    other = replace(effs["phone"], target="phone-2", seed_photos=[])
    pre = _pre("{ subject: { reinstall: erase } }")
    # The primary seeds; a second target with photos of its own would seed nothing.
    assert check_preconditions(pre, [effs["phone"], other]) == []
    (problem,) = check_preconditions(pre, [other, effs["phone"]])
    assert "seedPhotos on target 'phone' applies to the primary target alone" in problem


def test_lint_reports_a_scenario_runs_on_and_a_malformed_device_block() -> None:
    old = "- name: s\n  preconditions: { runsOn: { ios: { locale: ja_JP } } }\n  steps: []\n"
    (error,) = lint_text(old)
    assert "preconditions.runsOn.ios.locale moved to preconditions.device.ios.locale" in error
    typo = "- name: s\n  preconditions: { device: { ios: { modle: x } } }\n  steps: []\n"
    assert any("preconditions.device.ios.modle" in e for e in lint_text(typo))
    assert lint_diagnostics(typo)
    assert (
        lint_text(
            "- name: s\n  preconditions: { device: { ios: { locale: ja_JP } } }\n  steps: []\n"
        )
        == []
    )


# --- the checks against the driven platforms ---


def test_an_app_field_no_driven_platform_has_fails() -> None:
    (problem,) = check_preconditions(
        _pre("{ subject: { launch: { args: [-x] } } }"), [_effs()["site"]]
    )
    assert problem.startswith("preconditions.subject.launch.args applies to no target")
    assert "(platform web)" in problem


def test_an_app_field_one_driven_platform_has_passes() -> None:
    effs = _effs()
    args = _pre("{ subject: { launch: { args: [-x] } } }")
    assert check_preconditions(args, [effs["phone"], effs["site"]]) == []
    reinstall = _pre("{ subject: { reinstall: overwrite } }")
    assert check_preconditions(reinstall, [effs["droid"], effs["site"]]) == []


def test_the_fake_platform_has_no_launch_fields() -> None:
    (problem,) = check_preconditions(
        _pre("{ subject: { launch: { env: { A: '1' } } } }"), [_effs()["fake"]]
    )
    assert "preconditions.subject.launch.env" in problem


def test_an_ios_only_run_field_fails_without_an_ios_target() -> None:
    effs = _effs()
    pre = _pre("{ run: { tipKitHandling: true } }")
    (problem,) = check_preconditions(pre, [effs["droid"]])
    assert problem.startswith("preconditions.run.tipKitHandling applies to no target")
    assert check_preconditions(pre, [effs["phone"]]) == []
    # A web target has no `reinstall`, yet its fresh browser context satisfies `erase` (BE-0473).
    assert check_preconditions(_pre("{ subject: { reinstall: erase } }"), [effs["site"]]) == []
    (problem,) = check_preconditions(_pre("{ subject: { reinstall: clean } }"), [effs["site"]])
    assert problem.startswith("preconditions.subject.reinstall applies to no target")


@pytest.mark.parametrize(
    ("block", "message"),
    [
        (
            "{ ios: { locale: ja_JP } }",
            "preconditions.runsOn.ios.locale moved to preconditions.device.ios.locale",
        ),
        (
            "{ ios: { seedPhotos: [a.png] } }",
            "preconditions.runsOn.ios.seedPhotos moved to preconditions.device.ios.seedPhotos",
        ),
        ("{ ios: { model: iPhone 16 } }", "name a target whose runsOn.ios.model declares it"),
        # Only iOS ever read a scenario's device state, so another platform's is not sent to `ios`.
        ("{ android: { locale: ja_JP } }", "platform 'android' takes no scenario device state"),
        ("{ android: { apiLevel: '34' } }", "name a target whose runsOn.android.apiLevel declares"),
        ("{ web: { browser: { version: '>=120' } } }", "name a target whose runsOn.browser"),
        ("ios", "preconditions.runsOn was removed"),
        ("{}", "preconditions.runsOn was removed"),
    ],
)
def test_a_scenario_runs_on_fails_naming_where_it_went(block: str, message: str) -> None:
    # Where a run happens belongs to the target alone since BE-0469; device state moved to `device`.
    with pytest.raises(ValueError, match=re.escape(message)):
        _pre(f"{{ runsOn: {block} }}")


def test_each_scenario_is_checked_against_the_targets_it_declares() -> None:
    # The phone's seed photos would trip the primary-only check, which is not what this pins.
    cfg = load_config(_CONFIG.replace(", seedPhotos: [photos/red.png]", ""))
    loaded = LoadedConfig(config=cfg, path=Path("c.yaml"), source=None, root=None)
    both, own, typo = load_scenarios(
        "- name: both\n  targets: [site, phone]\n  steps: []\n"
        "- name: own\n  steps: []\n"
        "- name: typo\n  targets: [nope]\n  steps: []\n"
    )
    checks = _precondition_checks(loaded, [both, own, typo], "site", resolve(cfg, "site"))
    # `run` and `audit` share this: a scenario declaring `site` and `phone` drives both, so an
    # iOS-only field it writes is honored under `--target site`. A scenario whose every declared
    # name is unknown is left to the target checks, which name the typo.
    assert [(s.name, [t.target for t in ts]) for s, ts in checks] == [
        ("both", ["site", "phone"]),
        ("own", ["site"]),
    ]
    both.preconditions = _pre("{ subject: { launch: { args: [-x] } } }")
    _refuse_bad_preconditions(checks, erase=None)


_WEB_CONFIG = """
targets:
  site:
    platform: web
    subject: {{ url: "http://localhost:1/" }}
    paths: {{ scenarios: {scenarios} }}
"""


def test_run_refuses_a_field_no_driven_target_has_before_any_device_work(tmp_path: Path) -> None:
    scn = tmp_path / "s.yaml"
    scn.write_text(
        "- name: args on the web\n"
        "  preconditions: { subject: { launch: { args: [-x] } } }\n"
        "  steps: [{ tap: { id: a } }]\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(_WEB_CONFIG.format(scenarios=tmp_path), encoding="utf-8")
    r = invoke(
        CliRunner(), app, ["run", "--scenario", str(scn), "--target", "site", "--config", str(cfg)]
    )
    assert r.exit_code == 2
    assert "args on the web: preconditions.subject.launch.args applies to no target" in r.output


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
    assert (pre.run.system_alert_handling, pre.run.tip_kit_handling) == (False, True)
    # A stored erase wiped the device whatever `reinstall` said, so it lands on the top rung.
    assert pre.subject.reinstall == "erase"
    assert (pre.subject.launch.env, pre.subject.launch.args, pre.subject.launch.deeplink) == (
        {"A": "1"},
        ["-x"],
        "app://x",
    )
    assert (pre.device.ios.locale, pre.device.ios.seed_photos) == ("ja_JP", ["red.png"])


def test_a_plan_stored_with_a_scenario_runs_on_keeps_its_device_state() -> None:
    # BE-0469: the iOS device state moves to `device.ios`; the conditions are dropped, since they
    # already chose the devices the stored run ran on.
    text = (
        "- name: old\n"
        "  preconditions:\n"
        "    runsOn:\n"
        "      ios: { locale: ja_JP, seedPhotos: [red.png], model: iPhone 16, os: '>=18' }\n"
        "      web: { browser: { version: '>=120' } }\n"
        "  steps: []\n"
    )
    upgraded = _yaml.safe_load(_upgraded_scenario_text(text))
    assert upgraded[0]["preconditions"] == {
        "device": {"ios": {"locale": "ja_JP", "seedPhotos": ["red.png"]}}
    }
    (scenario,) = load_stored_scenario_file(text).scenarios
    assert scenario.preconditions.resolved_locale("en_US") == "ja_JP"
    conditions_only = "- name: c\n  preconditions: { runsOn: { ios: { os: '18' } } }\n  steps: []\n"
    assert not _yaml.safe_load(_upgraded_scenario_text(conditions_only))[0].get("preconditions")


@pytest.mark.parametrize(
    ("stored", "want"),
    [
        (
            "{ run: { erase: true }, subject: { reinstall: overwrite } }",
            {"subject": {"reinstall": "erase"}},
        ),
        (
            "{ run: { erase: false }, subject: { reinstall: overwrite } }",
            {"subject": {"reinstall": "overwrite"}},
        ),
        ("{ run: { erase: false, tipKitHandling: true } }", {"run": {"tipKitHandling": True}}),
    ],
)
def test_a_plan_stored_with_run_erase_folds_it_into_reinstall(stored: str, want: object) -> None:
    # A stored plan carries the concrete bool the run resolved (BE-0473).
    text = f"- name: s\n  preconditions: {stored}\n  steps: []\n"
    assert _yaml.safe_load(_upgraded_scenario_text(text))[0]["preconditions"] == want
    load_stored_scenario_file(text)


def test_the_app_group_fails_naming_subject() -> None:
    # BE-0470 renamed the group; the loader accepts one spelling.
    with pytest.raises(ValueError, match=r"'preconditions\.app' moved to 'preconditions\.subject'"):
        _pre("{ app: { reinstall: clean } }")


def test_a_plan_stored_with_the_app_group_still_loads() -> None:
    # A plan stored between BE-0452 and BE-0470 holds `app`; a flat key beside it joins the group.
    text = (
        "- name: s\n"
        "  preconditions:\n"
        "    app: { reinstall: overwrite, launch: { env: { A: '1' } } }\n"
        "    deeplink: 'app://x'\n"
        "  steps: []\n"
    )
    upgraded = _yaml.safe_load(_upgraded_scenario_text(text))
    assert upgraded[0]["preconditions"] == {
        "subject": {"reinstall": "overwrite", "launch": {"env": {"A": "1"}, "deeplink": "app://x"}}
    }
    (scenario,) = load_stored_scenario_file(text).scenarios
    assert scenario.preconditions.subject.reinstall == "overwrite"
    assert scenario.preconditions.subject.launch.deeplink == "app://x"


def test_the_upgrade_leaves_a_regrouped_plan_alone() -> None:
    text = "- name: s\n  preconditions: { subject: { reinstall: erase } }\n  steps: []\n"
    assert _yaml.safe_load(_upgraded_scenario_text(text)) == _yaml.safe_load(text)


def test_the_report_rows_read_the_groups() -> None:
    (scenario,) = load_stored_scenario_file(_PRE_REGROUP_PLAN).scenarios
    rows = _preconditions_rows(scenario_dict(scenario))
    assert rows == [
        ("reinstall", "erase"),
        ("deeplink", "app://x"),
        ("locale", "ja_JP"),
        ("A", "1"),
        ("launch args", "-x"),
    ]


def test_an_empty_device_locale_fails_rather_than_falling_back() -> None:
    # An empty value would quietly yield the target's locale, so a written key would do nothing.
    with pytest.raises(ValueError, match="locale"):
        _pre('{ device: { ios: { locale: "" } } }')
