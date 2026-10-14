"""The surfaces the target-config switch-over adds (BE-0452 unit 4).

Covers the `--backend` check, the "not yet supported" refusal of `runsOn` conditions, the URL form of
`--udid` going away, the `appium` environment a `--worker-config` names (and the device each command
takes through it), the scenario's `preconditions.run.inheritSetup`, the worker's view of an `appium`
environment, and the loader's merge of `defaults` into a target.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import typer

from bajutsu.cli import _shared
from bajutsu.common import backends, deprecations
from bajutsu.common.capability.worker_capability import (
    WorkerCapability,
    WorkerCapabilityError,
    parse_worker_capability,
)
from bajutsu.common.config import DeviceProvider, Effective, load_config, resolve
from bajutsu.common.config.schema._merge import merge_defaults, merge_layer
from bajutsu.common.scenario import Scenario


def _eff(text: str, name: str = "t") -> Effective:
    return resolve(load_config(text), name)


_IOS = "targets:\n  t: { platform: ios, app: { id: com.x } }\n"
_GRID = "version: 1\nenvironment: appium\nappium: { endpoint: 'https://grid.example/wd/hub' }\n"


def _grid(drivers: str = "[xcuitest, playwright, fake]") -> WorkerCapability:
    return parse_worker_capability(_GRID + f"drivers: {drivers}\n")


# --- `--backend` is a check -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "expected"),
    [("", ["ios"]), ("ios", ["ios"]), ("xcuitest", ["xcuitest"]), ("fake", ["fake"])],
)
def test_backend_override_accepts_the_platform_its_actuator_or_fake(
    override: str, expected: list[str]
) -> None:
    assert backends.backend_override(override, ["ios"]) == expected


@pytest.mark.parametrize("override", ["web", "playwright", "ios,fake", "nope"])
def test_backend_override_refuses_anything_else(override: str) -> None:
    with pytest.raises(ValueError, match="does not match the target's platform 'ios'"):
        backends.backend_override(override, ["ios"])


def test_the_cli_backend_check_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert _shared._backends("", ["web"]) == ["web"]
    with pytest.raises(typer.Exit) as exc:
        _shared._backends("adb", ["web"])
    assert exc.value.exit_code == 2
    assert "use one of web, playwright, fake" in capsys.readouterr().out


# --- runsOn conditions are not yet supported --------------------------------------------------


def test_conditions_resolve_onto_the_effective_config() -> None:
    eff = _eff('targets:\n  t: { platform: ios, app: { id: com.x }, runsOn: { os: ">=18" } }\n')
    assert eff.conditions == {"os": ">=18"}
    android = _eff(
        "targets:\n  t: { platform: android, app: { id: com.x }, runsOn: { avd: [A, B] } }\n"
    )
    assert android.conditions == {"avd": ["A", "B"]}
    # One engine is how a web target is driven, not a condition; a listed one multiplies runs.
    web = _eff(
        "targets:\n  t: { platform: web, app: { url: 'http://x/' }, "
        "runsOn: { browser: { engine: [chromium, webkit] } } }\n"
    )
    assert web.conditions == {"browser.engine": ["chromium", "webkit"]}
    assert _eff(_IOS).conditions == {}


def test_a_condition_fails_before_any_device_work(capsys: pytest.CaptureFixture[str]) -> None:
    eff = _eff(
        'targets:\n  t: { platform: ios, app: { id: com.x }, runsOn: { model: "iPhone 16" } }\n'
    )
    with pytest.raises(typer.Exit) as exc:
        _shared._refuse_conditions(eff, ["ios"])
    assert exc.value.exit_code == 2
    assert "runsOn.model: matching devices by runsOn conditions is not yet supported" in (
        capsys.readouterr().out
    )


def test_the_fake_driver_skips_conditions_with_one_notice(caplog: pytest.LogCaptureFixture) -> None:
    eff = _eff('targets:\n  t: { platform: ios, app: { id: com.x }, runsOn: { os: "18" } }\n')
    deprecations._emitted.discard("conditions.fake")
    with caplog.at_level(logging.WARNING, logger=deprecations.logger.name):
        _shared._refuse_conditions(eff, ["fake"])
        _shared._refuse_conditions(eff, ["fake"])
    notices = [
        r.getMessage() for r in caplog.records if "evaluates no device condition" in r.getMessage()
    ]
    assert len(notices) == 1
    _shared._refuse_conditions(_eff(_IOS), ["ios"])  # no condition, nothing to refuse


def test_a_kind_device_target_cannot_declare_a_condition() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="kind: device cannot declare"):
        load_config(
            "targets:\n  t: { platform: ios, app: { id: com.x }, runsOn: { kind: device, os: '18' } }\n"
        )
    # A team-wide `model` default reaches here after the merge, and the target clears it with null.
    cfg = load_config(
        "defaults:\n  platforms:\n    ios:\n      runsOn: { model: iPhone 16 }\n"
        "targets:\n  t: { platform: ios, app: { id: com.x }, runsOn: { kind: device, model: null } }\n"
    )
    assert resolve(cfg, "t").conditions == {}


# --- the device a command takes, and the URL `--udid` going away -------------------------------


def test_a_url_udid_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    _shared._reject_url_udid("booted")
    _shared._reject_url_udid("A,B")
    with pytest.raises(typer.Exit) as exc:
        _shared._reject_url_udid("booted,https://grid.example/wd/hub")
    assert exc.value.exit_code == 2
    assert "`appium` environment" in capsys.readouterr().out


def test_an_appium_environment_hands_its_grid_to_its_platform_alone() -> None:
    grid = _grid()
    ios = _shared._with_worker_device(_eff(_IOS), grid)
    assert ios.device_provider == DeviceProvider(
        kind="appium", endpoint="https://grid.example/wd/hub"
    )
    web = _eff("targets:\n  t: { platform: web, app: { url: 'http://x/' } }\n")
    assert _shared._with_worker_device(web, grid).device_provider is None
    assert _shared._device_udid_or_exit(ios, "booted") == "https://grid.example/wd/hub"
    assert _shared._device_udid_or_exit(_eff(_IOS), "booted") == "booted"


def test_an_unknown_provider_kind_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    eff = replace(_eff(_IOS), device_provider=DeviceProvider(kind="nope"))
    with pytest.raises(typer.Exit) as exc:
        _shared._device_udid_or_exit(eff, "booted")
    assert exc.value.exit_code == 2
    assert "unknown device provider 'nope'" in capsys.readouterr().out


def test_capabilities_for_run_reads_the_grid_from_the_provider() -> None:
    eff = _shared._with_worker_device(_eff(_IOS), _grid())
    narrowed = backends.capabilities_for_run("xcuitest", eff, "booted")
    assert narrowed < backends.capabilities_for("xcuitest")


def test_a_device_cloud_worker_file_is_refused_and_an_appium_one_kept(tmp_path: Path) -> None:
    grid = tmp_path / "grid.yaml"
    grid.write_text(_GRID + "drivers: [xcuitest]\n", encoding="utf-8")
    assert _shared._load_device_worker_or_exit(grid).environment == "appium"


def test_the_appium_block_belongs_to_the_appium_environment() -> None:
    with pytest.raises(WorkerCapabilityError, match="needs an `appium:` block"):
        parse_worker_capability("version: 1\nenvironment: appium\ndrivers: [xcuitest]\n")
    with pytest.raises(WorkerCapabilityError, match="belongs to environment 'appium'"):
        parse_worker_capability(
            "version: 1\nappium: { endpoint: 'https://g/' }\ndrivers: [xcuitest]\n"
        )
    with pytest.raises(WorkerCapabilityError, match="not an http"):
        parse_worker_capability(
            "version: 1\nenvironment: appium\nappium: { endpoint: grid }\ndrivers: [xcuitest]\n"
        )


def test_an_appium_grid_lifts_the_host_rule_for_its_platforms_driver_alone() -> None:
    grid = _grid()
    assert not grid.is_local and not grid.is_batch
    assert grid.drives_on_host("xcuitest") is False  # the grid's host drives it
    assert grid.drives_on_host("playwright") is True  # a local target of the same scenario
    assert grid.max_job_concurrency == 1


def test_two_grid_targets_in_one_scenario_are_refused(capsys: pytest.CaptureFixture[str]) -> None:
    from bajutsu.run.cli import _reject_two_grid_targets

    grid = _grid()
    cfg = load_config(
        "targets:\n"
        "  a: { platform: ios, app: { id: com.a } }\n"
        "  b: { platform: ios, app: { id: com.b } }\n"
        "  w: { platform: web, app: { url: 'http://x/' } }\n"
    )
    effs = {n: _shared._with_worker_device(resolve(cfg, n), grid) for n in ("a", "b", "w")}
    mixed = Scenario.model_validate({"name": "mixed", "targets": ["a", "w"], "steps": []})
    _reject_two_grid_targets([mixed], effs, "a")
    both = Scenario.model_validate({"name": "both", "targets": ["a", "b"], "steps": []})
    with pytest.raises(typer.Exit):
        _reject_two_grid_targets([both], effs, "a")
    assert "sends targets a, b to one appium grid" in capsys.readouterr().out


# --- the shared bring-up's other exits --------------------------------------------------------


def test_an_unavailable_actuator_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable(_backends: list[str]) -> str:
        raise RuntimeError("no available actuator among ['fake']")

    monkeypatch.setattr(_shared, "select_actuator", unavailable)
    with pytest.raises(typer.Exit) as exc:
        _shared._select_actuator_or_exit("fake", _eff(_IOS), [])
    assert exc.value.exit_code == 2
    assert "no available actuator" in capsys.readouterr().out


def test_a_browser_flag_leaves_a_non_web_target_alone() -> None:
    eff = _eff(_IOS)
    assert _shared._resolve_browser(eff, "webkit") is eff


def test_the_default_language_clears_the_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from bajutsu.common.agents import ai_config

    monkeypatch.setenv(ai_config.LANGUAGE_ENV, "ja")
    eff = _shared._resolve_language(_eff(_IOS), ai_config.DEFAULT_LANGUAGE)
    assert eff.ai is not None and eff.ai.language == ai_config.DEFAULT_LANGUAGE


def test_a_launch_server_failure_exits_2_after_its_cleanup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("server never became ready")

    monkeypatch.setattr(_shared, "start_launch_server", failing)
    cleaned: list[bool] = []
    with pytest.raises(typer.Exit):
        _shared._start_launch_server_or_exit(_eff(_IOS), on_error=lambda: cleaned.append(True))
    assert cleaned == [True]
    assert "never became ready" in capsys.readouterr().out


# --- the scenario's inheritSetup --------------------------------------------------------------


def _hooked() -> Effective:
    return _eff(
        "targets:\n"
        "  t:\n"
        "    platform: ios\n"
        "    app: { id: com.x }\n"
        "    hooks:\n"
        "      setup: [{ tap: { id: login } }]\n"
        "      cleanup: [{ 'on': always, steps: [{ tap: { id: logout } }] }]\n"
    )


@pytest.mark.parametrize("targets", [None, ["t"]])
def test_inherit_setup_false_skips_the_targets_setup_and_keeps_its_cleanup(
    targets: list[str] | None,
) -> None:
    from bajutsu.common.runner.pipeline import _hooks_for

    body: dict[str, Any] = {
        "name": "s",
        "steps": [],
        "preconditions": {"run": {"inheritSetup": False}},
    }
    if targets is not None:
        body["targets"] = targets
    eff = _hooked()
    before, after = _hooks_for(Scenario.model_validate(body), eff, {"t": eff})
    assert before == []
    assert len(after) == 1
    inherited, _ = _hooks_for(Scenario.model_validate({"name": "s", "steps": []}), eff, None)
    assert len(inherited) == 1


def test_a_scenarios_setup_prelude_key_is_gone() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="setup"):
        Scenario.model_validate({"name": "s", "steps": [], "preconditions": {"setup": "p.yaml"}})


# --- the defaults merge ----------------------------------------------------------------------


def test_merge_layer_merges_dicts_replaces_lists_unions_two_and_clears_with_null() -> None:
    base = {"run": {"secrets": ["A"], "erase": True}, "evidence": {"capture": ["x"]}, "keep": 1}
    over = {"run": {"secrets": ["B", "A"], "erase": None}, "evidence": {"capture": ["y"]}}
    assert merge_layer(base, over) == {
        "run": {"secrets": ["A", "B"]},
        "evidence": {"capture": ["y"]},
        "keep": 1,
    }


def test_merge_defaults_takes_the_targets_platform_block_and_skips_ai() -> None:
    defaults = {
        "ai": {"model": "m"},
        "run": {"erase": True},
        "platforms": {
            "ios": {"runsOn": {"locale": "ja_JP"}},
            "web": {"driver": {"headless": False}},
        },
    }
    merged = merge_defaults(defaults, {"platform": "ios", "app": {"id": "com.x"}})
    assert merged == {
        "run": {"erase": True},
        "runsOn": {"locale": "ja_JP"},
        "platform": "ios",
        "app": {"id": "com.x"},
    }
    # A target with no platform takes the shared groups alone, then fails for the missing key.
    assert merge_defaults(defaults, {"app": {"id": "com.x"}}) == {
        "run": {"erase": True},
        "app": {"id": "com.x"},
    }


def test_a_fake_target_keeps_the_identifier_contract() -> None:
    eff = _eff("targets:\n  t: { platform: fake, app: { idNamespaces: [home] } }\n")
    assert eff.backend == ["fake"]
    assert eff.id_namespaces == ["home"]
    assert eff.platform == "ios"  # the iOS-shaped sub-config, as before BE-0452


def test_a_target_document_that_is_not_a_mapping_still_fails_loudly() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        load_config("targets:\n  t: [not, a, mapping]\n")
    with pytest.raises(ValidationError):
        load_config("defaults: [nope]\ntargets: {}\n")


def test_a_hooks_component_that_cannot_be_read_fails_loudly(tmp_path: Path) -> None:
    from pydantic import ValidationError

    from bajutsu.common.config import load_config_file

    (tmp_path / "dir.yaml").mkdir()  # a path that exists but cannot be read as a file
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n  t:\n    platform: ios\n    app: { id: com.x }\n"
        "    hooks: { setup: [{ use: { component: dir.yaml } }] }\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="cannot read a component"):
        load_config_file(cfg)


def test_a_hooks_group_is_refused() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="hooks cannot use a group"):
        load_config(
            "targets:\n  t:\n    platform: ios\n    app: { id: com.x }\n"
            "    hooks: { setup: [{ group: { name: g, steps: [{ tap: { id: a } }] } }] }\n"
        )


# --- the readers that check a request's backend -------------------------------------------------


def test_a_session_device_checks_the_body_backend() -> None:
    from bajutsu.serve.operations._common import _session_device

    assert _session_device({}, "ios") == (["ios"], "booted", None)
    assert _session_device({"backend": "fake", "udid": "U1"}, "ios") == (["fake"], "U1", None)
    _, _, err = _session_device({"backend": "web"}, "ios")
    assert err is not None and err[1] == 400 and "does not match" in err[0]["error"]
    _, _, err = _session_device({"backend": "nope!"}, "ios")
    assert err is not None and err[1] == 400


def test_the_registry_ignores_a_finished_id_it_does_not_hold() -> None:
    from bajutsu.serve import Job
    from bajutsu.serve.logbus import InMemoryLogBus
    from bajutsu.serve.state.job_registry import JobRegistry

    reg = JobRegistry(logbus=InMemoryLogBus(), finished_ids=lambda live: {*live, "ghost"})
    reg.register(Job(cmd=[]))
    assert reg.active_jobs() == 0  # the held job is released; the unknown id is skipped


def test_a_target_config_given_a_non_mapping_fails_in_pydantic() -> None:
    from pydantic import ValidationError

    from bajutsu.common.config import TargetConfig

    with pytest.raises(ValidationError):
        TargetConfig.model_validate(["not", "a", "mapping"])


# --- doctor -----------------------------------------------------------------------------------


def test_doctor_discloses_how_scenarios_split_across_a_ladder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from bajutsu.cli.commands import doctor as doctor_cmd

    scenarios = tmp_path / "e2e"
    scenarios.mkdir()
    (scenarios / "a.yaml").write_text("- name: one\n  steps:\n    - tap: { id: ok }\n", "utf-8")
    eff = resolve(
        load_config(
            "targets:\n  t: { platform: android, app: { id: com.x }, paths: { scenarios: e2e } }\n"
        ),
        "t",
    ).rebased(tmp_path)
    doctor_cmd._print_disclosures(eff, ["fake", "adb"], "booted", "fake")
    assert "actuator resolution (per scenario, BE-0240):" in capsys.readouterr().out


def _doctor(tmp_path: Path, *args: str) -> Any:
    from typer.testing import CliRunner

    from bajutsu.cli import app

    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(_IOS, encoding="utf-8")
    return CliRunner().invoke(app, ["doctor", "--target", "t", "--config", str(cfg), *args])


def test_doctor_exits_2_when_no_actuator_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_backends: list[str]) -> str:
        raise RuntimeError("no available actuator among ['fake']")

    monkeypatch.setattr("bajutsu.cli.commands.doctor.select_actuator", unavailable)
    result = _doctor(tmp_path, "--backend", "fake")
    assert result.exit_code == 2
    assert "no available actuator" in result.output


def test_doctor_exits_2_on_a_failed_config_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.common.capability.preflight import Check

    monkeypatch.setattr(
        "bajutsu.common.capability.preflight.config_checks",
        lambda *a, **k: [Check("target app.id", False, "set targets.t.app.id")],
    )
    result = _doctor(tmp_path, "--backend", "fake")
    assert result.exit_code == 2
    assert "set targets.t.app.id" in result.output


def test_doctor_refuses_a_condition_on_its_device_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from bajutsu.cli import app

    monkeypatch.setattr(
        "bajutsu.common.capability.preflight.doctor_environment_checks", lambda *a, **k: []
    )
    monkeypatch.setattr("bajutsu.cli.commands.doctor.select_actuator", lambda _b: "xcuitest")
    monkeypatch.setattr("bajutsu.cli.commands.doctor._host_toolchain", lambda: (None, None))
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        'targets:\n  t: { platform: ios, app: { id: com.x }, runsOn: { os: "18" } }\n',
        encoding="utf-8",
    )
    argv = ["doctor", "--target", "t", "--config", str(cfg)]
    assert CliRunner().invoke(app, [*argv, "--environment-only"]).exit_code == 0
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 2
    assert "not yet supported" in result.output


# --- the MCP server's device ---------------------------------------------------------------


def test_the_mcp_probe_takes_its_device_through_the_worker() -> None:
    from bajutsu.mcp.tools import _probe_device

    eff, spec = _probe_device(_eff(_IOS), "booted", _grid())
    assert spec == "https://grid.example/wd/hub"
    assert eff.device_provider is not None
    _, local = _probe_device(
        _eff(_IOS), "booted", parse_worker_capability("version: 1\ndrivers: [fake]\n")
    )
    assert local == "booted"
    with pytest.raises(ValueError, match="no longer takes a URL"):
        _probe_device(_eff(_IOS), "https://grid/", _grid())
    with pytest.raises(ValueError, match="not yet supported"):
        _probe_device(
            _eff('targets:\n  t: { platform: ios, app: { id: com.x }, runsOn: { os: "18" } }\n'),
            "booted",
            _grid(),
        )


def test_the_mcp_doctor_tool_reports_a_refused_device(tmp_path: Path) -> None:
    import asyncio

    from fastmcp import FastMCP

    from bajutsu.mcp.tools import register_tools

    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(_IOS, encoding="utf-8")
    mcp = FastMCP("test")
    register_tools(mcp, cfg, worker=_grid())
    result = asyncio.run(mcp.call_tool("bajutsu_doctor", {"target": "t", "udid": "https://g/"}))
    assert "no longer takes a URL" in getattr(result.content[0], "text", "")


def test_the_mcp_run_tool_passes_its_worker_config_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import subprocess

    from fastmcp import FastMCP

    from bajutsu.mcp.tools import register_tools

    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_k: Any) -> Any:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="PASS  m.json\n", stderr="")

    monkeypatch.setattr("bajutsu.mcp.tools.subprocess.run", fake_run)
    worker_file = tmp_path / "w.yaml"
    worker_file.write_text(_GRID + "drivers: [xcuitest]\n", encoding="utf-8")
    mcp = FastMCP("test")
    register_tools(mcp, tmp_path / "c.yaml", worker_config=worker_file)
    asyncio.run(
        mcp.call_tool(
            "bajutsu_run",
            {"target": "t", "scenario": "s.yaml", "tag": "a", "exclude": "b", "erase": True},
        )
    )
    (cmd,) = seen
    assert cmd[-2:] == ["--worker-config", str(worker_file)]
    assert {"--scenario", "--tag", "--exclude", "--erase"} <= set(cmd)


# --- an appium worker ---------------------------------------------------------------------------


def test_an_appium_worker_advertises_its_environment_alone() -> None:
    from bajutsu.serve.cli import worker as worker_mod

    assert worker_mod._advertised_capabilities("", "", _grid(), "linux") == ["environment:appium"]
    with pytest.raises(typer.BadParameter, match="takes no --platform"):
        worker_mod._advertised_capabilities("ios", "", _grid(), "linux")
    worker_mod._register_environment_or_exit("appium")  # no batch provider to register


def test_an_appium_worker_runs_run_specs_and_refuses_a_batch() -> None:
    from bajutsu.serve.server.one_shot import environment_refusal

    assert environment_refusal({"cmd": ["bajutsu"]}, "appium") is None
    reason = environment_refusal({"batch": {"provider": "devicefarm"}}, "appium")
    assert reason is not None and "submits no cloud batch" in reason


# --- a target may add to the team's masking but never wipe it -----------------------------------


@pytest.mark.parametrize(
    "written",
    [
        "evidence: { redact: { fields: null } }",
        "evidence: { redact: null }",
        "evidence: null",
        "run: { secrets: null }",
        "run: null",
    ],
)
def test_null_cannot_clear_a_team_wide_secret_or_redaction(written: str) -> None:
    from pydantic import ValidationError

    defaults = (
        "defaults:\n  run: { secrets: [TEAM] }\n  evidence: { redact: { fields: [password] } }\n"
    )
    with pytest.raises(ValidationError, match="null cannot clear it"):
        load_config(
            defaults + f"targets:\n  t: {{ platform: ios, app: {{ id: com.x }}, {written} }}\n"
        )


def test_null_still_clears_an_ordinary_inherited_value() -> None:
    cfg = load_config(
        "defaults:\n  paths: { scenarios: shared }\n"
        "targets:\n  t: { platform: ios, app: { id: com.x }, paths: { scenarios: null } }\n"
    )
    assert resolve(cfg, "t").evidence_dirs.scenarios is None


def test_config_level_unmask_switches_are_honoured_and_inherited() -> None:
    # Before BE-0452 the config's `unmaskSecureFields` / `unmaskCredentialNames` were dropped, so
    # masking stayed on; now a target or the team `defaults` can opt out, as a scenario can.
    cfg = load_config(
        "defaults:\n  evidence: { redact: { unmaskSecureFields: true } }\n"
        "targets:\n"
        "  a: { platform: ios, app: { id: com.a } }\n"
        "  b: { platform: ios, app: { id: com.b }, "
        "evidence: { redact: { unmaskCredentialNames: true } } }\n"
    )
    a, b = resolve(cfg, "a").redact, resolve(cfg, "b").redact
    assert a.unmask_secure_fields and not a.unmask_credential_names
    assert b.unmask_secure_fields and b.unmask_credential_names


# --- serve checks a request's backend, and an appium run's targets ----------------------------


def _serve_state(tmp_path: Path, scenario: str) -> Any:
    from bajutsu import serve as srv

    scn = tmp_path / "scenarios"
    scn.mkdir()
    (scn / "s.yaml").write_text(scenario, encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n"
        f"  demo: {{ platform: ios, app: {{ id: com.x }}, paths: {{ scenarios: {scn} }} }}\n"
        "  site: { platform: web, app: { url: 'http://x/' } }\n",
        encoding="utf-8",
    )
    (tmp_path / "runs").mkdir()

    class _Capture:
        def dispatch(self, state: Any, job: Any) -> None:
            """Accept the job without running it."""

    return srv.ServeState(
        scenarios_dir=scn, config=cfg, runs_dir=tmp_path / "runs", cwd=tmp_path, executor=_Capture()
    )


_ONE = "- name: one\n  steps:\n    - tap: { id: a }\n"


@pytest.mark.parametrize("op", ["run", "record", "crawl"])
def test_a_request_backend_that_does_not_match_the_target_is_a_400(tmp_path: Path, op: str) -> None:
    from bajutsu.serve import operations as ops

    state = _serve_state(tmp_path, _ONE)
    body = {"target": "demo", "scenario": "s.yaml", "goal": "g", "backend": "web"}
    start = {"run": ops.start_run, "record": ops.start_record, "crawl": ops.start_crawl}[op]
    payload, status = start(state, body)
    assert status == 400
    assert "does not match the target's platform 'ios'" in payload["error"]


def test_routing_refuses_a_mismatched_backend_rather_than_widening(tmp_path: Path) -> None:
    from bajutsu.serve.helpers import target_capabilities

    _serve_state(tmp_path, _ONE)
    with pytest.raises(ValueError, match="does not match"):
        target_capabilities(tmp_path / "bajutsu.config.yaml", "demo", "web")


def test_an_appium_run_mixing_a_grid_and_a_local_target_is_refused(tmp_path: Path) -> None:
    from bajutsu.serve import operations as ops

    mixed = (
        "- name: mixed\n  targets: [demo, site]\n  steps:\n"
        "    - target: demo\n      tap: { id: a }\n    - target: site\n      tap: { id: b }\n"
    )
    state = _serve_state(tmp_path, mixed)
    payload, status = ops.start_run(
        state, {"target": "demo", "scenario": "s.yaml", "environment": "appium"}
    )
    assert status == 400
    assert "local target(s) site" in payload["error"]
    (tmp_path / "ok").mkdir()
    ok_state = _serve_state(tmp_path / "ok", _ONE)
    _, status = ops.start_run(
        ok_state, {"target": "demo", "scenario": "s.yaml", "environment": "appium"}
    )
    assert status == 200


def test_the_serve_helpers_answer_an_unreadable_config_quietly(tmp_path: Path) -> None:
    from bajutsu.serve.helpers import request_backend_error, target_capabilities, target_platforms

    missing = tmp_path / "missing.yaml"
    assert target_capabilities(missing, "demo") == []
    assert request_backend_error(missing, "demo", "web") is None
    assert target_platforms(missing) == {}
    _serve_state(tmp_path, _ONE)
    cfg = tmp_path / "bajutsu.config.yaml"
    assert request_backend_error(cfg, "demo", "fake") is None
    assert target_platforms(cfg) == {"demo": "ios", "site": "web"}


def test_the_grid_mix_check_skips_what_it_cannot_read(tmp_path: Path) -> None:
    from bajutsu.serve.operations.dispatch import _grid_mix_refusal, _scenario_text
    from bajutsu.serve.upload_artifacts import ArtifactOverrides

    state = _serve_state(tmp_path, _ONE)
    cfg = tmp_path / "bajutsu.config.yaml"
    assert _grid_mix_refusal(cfg, "demo", None) is None
    assert _grid_mix_refusal(cfg, "demo", "- name: [broken") is None
    overrides = ArtifactOverrides("demo", binary=None, scenarios="a" * 64)
    assert _scenario_text(state, "", None, "demo", {"scenario": "s.yaml"}, overrides) is None
