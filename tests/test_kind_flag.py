"""`--kind` and the browser-list check: a flag picks among the kinds a target declares (BE-0468)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import typer
from run_cli import invoke
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli._shared import (
    _resolve_browser,
    _resolve_kind,
    check_listed_browsers,
    parse_kind_flag,
)
from bajutsu.common.config import Effective, WebConfig, load_config, resolve
from bajutsu.common.config.accessors import xcuitest_targets_real_device

runner = CliRunner()

_BOTH = "runsOn: {ios: {kind: [simulator, device], family: ipad}}"


def _eff(target: str) -> Effective:
    return resolve(load_config(f"targets:\n  t: {{ {target} }}\n"), "t")


def _ios(runs_on: str = "") -> Effective:
    return _eff("platform: ios, subject: { id: com.x }" + (f", {runs_on}" if runs_on else ""))


def _web(kind: str) -> Effective:
    return _eff(
        f"platform: web, subject: {{ url: 'http://x/' }}, runsOn: {{browser: {{kind: {kind}}}}}"
    )


def _exit_code(call: Callable[[], object]) -> int:
    with pytest.raises(typer.Exit) as exc:
        call()
    return int(exc.value.exit_code)


# --- parsing ------------------------------------------------------------------------------------


def test_parse_kind_flag_dedupes_in_order() -> None:
    assert parse_kind_flag(" device , simulator,device,") == ["device", "simulator"]


@pytest.mark.parametrize("value", [",", " ", ",,"])
def test_parse_kind_flag_rejects_a_value_naming_no_kind(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _exit_code(lambda: parse_kind_flag(value)) == 2
    assert "names no kind" in capsys.readouterr().out


def test_parse_kind_flag_rejects_an_unknown_kind(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit_code(lambda: parse_kind_flag("simulator,emulator")) == 2
    assert "unknown --kind 'emulator': use one of simulator, device" in capsys.readouterr().out


def test_parse_kind_flag_takes_one_kind_where_the_command_runs_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert parse_kind_flag("device", single=True) == ["device"]
    assert _exit_code(lambda: parse_kind_flag("simulator,device", single=True)) == 2
    assert "--kind takes one kind here" in capsys.readouterr().out


# --- `--kind` over the target's kinds -----------------------------------------------------------


def test_no_flag_keeps_the_target() -> None:
    eff = _ios(_BOTH)
    assert _resolve_kind(eff, "") == eff


def test_kind_narrows_a_listed_target_and_keeps_the_coordinate() -> None:
    eff = _resolve_kind(_ios(_BOTH), "device")
    # A one-kind list still counts, so the run keys the history of the full list's device run.
    assert eff.conditions == {"kind": ["device"], "family": "ipad"}
    assert xcuitest_targets_real_device(eff)


def test_kind_keeps_the_target_order_whatever_the_flag_order() -> None:
    eff = _resolve_kind(_ios(_BOTH), "device,simulator")
    assert eff.conditions["kind"] == ["simulator", "device"]
    assert not xcuitest_targets_real_device(eff)


def test_kind_outside_the_target_list_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    eff = _ios("runsOn: {ios: {kind: [device]}}")
    assert _exit_code(lambda: _resolve_kind(eff, "simulator")) == 2
    assert "--kind simulator: target 't' runs on runsOn.ios.kind device" in capsys.readouterr().out


def test_a_single_kind_target_accepts_only_its_own_kind(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A target with no kind runs on a Simulator, so the flag may repeat that kind and nothing else.
    plain = _ios()
    assert _resolve_kind(plain, "simulator") == plain
    assert _exit_code(lambda: _resolve_kind(plain, "device")) == 2
    assert "runs on runsOn.ios.kind simulator" in capsys.readouterr().out
    device = _ios("runsOn: {ios: {kind: device}}")
    assert _resolve_kind(device, "device") == device
    assert _exit_code(lambda: _resolve_kind(device, "simulator")) == 2


def test_kind_on_a_non_ios_target_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit_code(lambda: _resolve_kind(_web("chromium"), "simulator")) == 2
    assert "--kind is iOS-only; target 't' runs on web" in capsys.readouterr().out


# --- `--browser` / `--browsers` over the target's browsers --------------------------------------


def test_browser_flag_picks_among_a_listed_target() -> None:
    eff = _resolve_browser(_web("[chrome, firefox]"), "firefox")
    assert isinstance(eff.platform_config, WebConfig)
    assert eff.platform_config.browser == "firefox"


def test_browser_outside_the_target_list_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit_code(lambda: _resolve_browser(_web("[chrome, firefox]"), "webkit")) == 2
    assert "--browser webkit: target 't' lists runsOn.browser.kind chrome, firefox" in (
        capsys.readouterr().out
    )


def test_a_target_without_a_list_takes_any_browser() -> None:
    # A single kind is how the target is driven, not a support set, so the flag replaces it.
    check_listed_browsers(_web("chrome"), "--browsers", ["webkit", "edge"])
    eff = _resolve_browser(_web("chrome"), "webkit")
    assert isinstance(eff.platform_config, WebConfig)
    assert eff.platform_config.browser == "webkit"


def test_browsers_names_every_kind_the_target_leaves_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    eff = _web("[chrome, edge]")
    check_listed_browsers(eff, "--browsers", ["edge", "chrome"])
    assert _exit_code(lambda: check_listed_browsers(eff, "--browsers", ["chrome", "webkit"])) == 2
    assert "--browsers webkit: target 't'" in capsys.readouterr().out


# --- the commands --------------------------------------------------------------------------------


def _config(tmp_path: Path) -> Path:
    scenario = tmp_path / "s.yaml"
    scenario.write_text("scenarios:\n  - name: s\n    steps: []\n", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n"
        f"  both: {{ platform: ios, subject: {{ id: com.x }}, {_BOTH} }}\n"
        "  site: { platform: web, subject: { url: 'http://x/' }, "
        "runsOn: {browser: {kind: [chrome, edge]}} }\n",
        encoding="utf-8",
    )
    return cfg


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["run", "--target", "both", "--kind", "simulator,phone"], "unknown --kind 'phone'"),
        (["run", "--target", "site", "--kind", "device"], "--kind is iOS-only"),
        (["run", "--target", "site", "--browsers", "chrome,firefox"], "--browsers firefox"),
        (["run", "--target", "site", "--browser", "webkit"], "--browser webkit"),
        (
            ["run", "--target", "site", "--browser", "chrome", "--browsers", "chrome,webkit"],
            "--browsers webkit",
        ),
        (["crawl", "--target", "both", "--kind", "simulator,device"], "--kind takes one kind"),
        (["repl", "--target", "both", "--kind", "simulator,device"], "--kind takes one kind"),
        (
            ["record", "--target", "both", "--goal", "x", "--kind", "simulator,device"],
            "--kind takes one kind",
        ),
    ],
)
def test_commands_refuse_a_kind_the_target_does_not_declare(
    tmp_path: Path, argv: list[str], message: str
) -> None:
    cfg = _config(tmp_path)
    extra = ["--scenario", str(tmp_path / "s.yaml")] if argv[0] == "run" else []
    r = invoke(runner, app, [*argv, *extra, "--config", str(cfg)])
    assert r.exit_code == 2, r.output
    assert message in r.output


def test_run_refuses_kind_across_two_ios_targets(capsys: pytest.CaptureFixture[str]) -> None:
    # The flag narrows the command's own target, so a second iOS target would keep its kinds unsaid.
    from bajutsu.run.cli import _reject_kind_across_targets

    effs = {"both": _ios(_BOTH), "other": _ios(), "site": _web("chromium")}
    _reject_kind_across_targets(effs, kind="")
    _reject_kind_across_targets({"both": effs["both"], "site": effs["site"]}, kind="device")
    assert _exit_code(lambda: _reject_kind_across_targets(effs, kind="device")) == 2
    assert "--kind applies to a run's single iOS target, but this run declares 2: both, other" in (
        capsys.readouterr().out
    )
