"""Lane resolution for `bajutsu run` — how --udid / --workers map to a device pool,
plus the run command's directory-resolution and file-expansion helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.orchestrator import DEFAULT_ALERT_POLL_INTERVAL
from bajutsu.common.orchestrator.types import match_alert_rule
from bajutsu.common.scenario import (
    Scenario,
    SystemAlertHandling,
    SystemAlertRule,
    load_scenarios,
)
from bajutsu.common.scenario.system_alerts import UncoveredSystemAlertLocale, covered_languages
from bajutsu.run.cli import (
    _alert_guard_factory,
    _apply_system_alert_handling,
    _expand_file,
    _filter_scenarios,
    _flag_alert_policy,
    _load_scenarios,
    _resolve_dir,
    _resolve_evidence_dirs,
    _resolve_lanes,
    _resolve_network,
    _resolve_rules,
    _resolve_secrets,
)


def _resolve(udid: str) -> str:
    # Stand-in for env.resolve_udid: echo back a concrete udid per token.
    return f"resolved:{udid}"


def test_web_workers_become_parallel_lanes() -> None:
    # Web has no simctl udid; --workers N alone is N near-free BrowserContext lanes.
    udids, workers = _resolve_lanes("playwright", udid="booted", workers=3, resolve_udid=_resolve)
    assert udids == ["web-0", "web-1", "web-2"]
    assert workers == 3


def test_web_defaults_to_a_single_lane() -> None:
    udids, workers = _resolve_lanes("playwright", udid="booted", workers=1, resolve_udid=_resolve)
    assert udids == ["web-0"]
    assert workers == 1


def test_web_workers_floored_at_one() -> None:
    udids, workers = _resolve_lanes("playwright", udid="booted", workers=0, resolve_udid=_resolve)
    assert udids == ["web-0"]
    assert workers == 1


def test_xcuitest_resolves_each_udid_in_the_comma_list() -> None:
    udids, workers = _resolve_lanes("xcuitest", udid="A, B ,C", workers=5, resolve_udid=_resolve)
    assert udids == ["resolved:A", "resolved:B", "resolved:C"]
    # workers is capped to the pool size (3 devices), even though 5 were requested.
    assert workers == 3


def test_xcuitest_single_device_is_serial() -> None:
    udids, workers = _resolve_lanes("xcuitest", udid="only", workers=4, resolve_udid=_resolve)
    assert udids == ["resolved:only"]
    assert workers == 1


def test_an_unpinned_device_run_is_always_a_serial_pool_of_one() -> None:
    # Load-bearing for BE-0354's replacement rung, which `device_replacement_supported` scopes to
    # `udid_spec == "booted"`: the request it defers is bound to *one device's* environment and is
    # served by the next lease, so it is only sound while that next lease cannot be a different
    # device or a concurrent worker. A `booted` run carries no comma list, so the pool is one device
    # and workers is capped to 1 however many were asked for — the property that makes the deferral
    # safe, pinned here because it lives in a different module from the rung that leans on it.
    udids, workers = _resolve_lanes("xcuitest", udid="booted", workers=8, resolve_udid=_resolve)
    assert udids == ["resolved:booted"]
    assert workers == 1


def _eff(**target: str) -> Effective:
    # A minimal iOS-shaped target; pass baselines/schemas/goldens/setup to fill those config fields.
    fields = "".join(f"    {k}: {v}\n" for k, v in target.items())
    cfg = load_config(f"targets:\n  x:\n    bundleId: com.x\n{fields}")
    return resolve(cfg, "x")


# --- directory resolution: --flag > config > default beside the scenario (BE-0006 / visual / schema)


def test_baselines_dir_flag_wins() -> None:
    got = _resolve_dir(
        "cli/dir",
        _eff(baselines="cfg/dir").evidence_dirs.baselines,
        Path("e2e/s.yaml"),
        "baselines",
    )
    assert got == Path("cli/dir")


def test_baselines_dir_config_when_no_flag() -> None:
    got = _resolve_dir(
        "", _eff(baselines="cfg/dir").evidence_dirs.baselines, Path("e2e/s.yaml"), "baselines"
    )
    assert got == Path("cfg/dir")


def test_baselines_dir_defaults_beside_the_scenario() -> None:
    got = _resolve_dir("", _eff().evidence_dirs.baselines, Path("e2e/s.yaml"), "baselines")
    assert got == Path("e2e/baselines")


def test_schemas_dir_flag_config_default() -> None:
    scn = Path("e2e/s.yaml")
    assert _resolve_dir(
        "cli/dir", _eff(schemas="cfg/dir").evidence_dirs.schemas, scn, "schemas"
    ) == Path("cli/dir")
    assert _resolve_dir("", _eff(schemas="cfg/dir").evidence_dirs.schemas, scn, "schemas") == Path(
        "cfg/dir"
    )
    assert _resolve_dir("", _eff().evidence_dirs.schemas, scn, "schemas") == Path("e2e/schemas")


def test_goldens_dir_flag_config_default() -> None:
    scn = Path("e2e/s.yaml")
    assert _resolve_dir(
        "cli/dir", _eff(goldens="cfg/dir").evidence_dirs.goldens, scn, "goldens"
    ) == Path("cli/dir")
    assert _resolve_dir("", _eff(goldens="cfg/dir").evidence_dirs.goldens, scn, "goldens") == Path(
        "cfg/dir"
    )
    assert _resolve_dir("", _eff().evidence_dirs.goldens, scn, "goldens") == Path("e2e/goldens")


# --- _expand_file: loads one file and resolves its setup/component/data refs relative to its dir


def test_expand_file_returns_scenarios_and_file_description(tmp_path: Path) -> None:
    # The `{description, scenarios}` mapping form: the file-level description rides back alongside
    # the expanded scenarios, and a ref-free file passes through unchanged.
    path = tmp_path / "s.yaml"
    path.write_text(
        "description: a suite\nscenarios:\n  - name: demo\n    steps:\n      - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    scenarios, description = _expand_file(path, _eff(), root=tmp_path)
    assert description == "a suite"
    assert [s.name for s in scenarios] == ["demo"]


def test_expand_file_missing_setup_ref_exits_2(tmp_path: Path) -> None:
    # A setup prelude resolved relative to the file's dir; a missing file is a usage error (exit 2).
    path = tmp_path / "s.yaml"
    path.write_text("- name: demo\n  steps:\n    - tap: { id: home.title }\n", encoding="utf-8")
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, _eff(setup="missing.yaml"), root=tmp_path)
    assert exc.value.exit_code == 2


def test_expand_file_missing_component_ref_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo\n  steps:\n    - use: { component: missing.yaml }\n", encoding="utf-8"
    )
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, _eff(), root=tmp_path)
    assert exc.value.exit_code == 2


def test_expand_file_missing_data_file_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo\n  dataFile: missing.csv\n  steps:\n    - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, _eff(), root=tmp_path)
    assert exc.value.exit_code == 2


# --- _resolve_secrets: bind declared secrets present in the environment (never the absent ones)


def test_resolve_secrets_binds_only_present_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN", "s3cr3t")
    monkeypatch.delenv("ABSENT", raising=False)
    bindings, values = _resolve_secrets(_eff(secrets="[TOKEN, ABSENT]"))
    assert bindings == {"secrets.TOKEN": "s3cr3t"}  # ABSENT is unbound, not an empty string
    assert values == ["s3cr3t"]


# --- _load_scenarios: the --scenario file, or the target's configured dir


def test_load_scenarios_single_file(tmp_path: Path) -> None:
    scn = tmp_path / "s.yaml"
    scn.write_text(
        "description: suite\nscenarios:\n  - name: demo\n    steps:\n      - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    scenarios, description, source_name, files = _load_scenarios(_eff(), [str(scn)], "x")
    assert [s.name for s in scenarios] == ["demo"]
    assert description == "suite"  # the single file's description rides back
    assert source_name == "s.yaml"
    assert files == [scn]


def test_load_scenarios_multiple_files_share_one_run(tmp_path: Path) -> None:
    # Repeating --scenario runs several files in one process (one warm runner): the scenarios
    # concatenate in order, it is not a lone-file run (no single-file description), and the report's
    # source label is the files' common parent, not any one file.
    for name, demo in (("a.yaml", "one"), ("b.yaml", "two")):
        (tmp_path / name).write_text(
            f"scenarios:\n  - name: {demo}\n    steps:\n      - tap: {{ id: home.title }}\n",
            encoding="utf-8",
        )
    scenarios, description, source_name, files = _load_scenarios(
        _eff(), [str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")], "x"
    )
    assert [s.name for s in scenarios] == ["one", "two"]  # concatenated, order preserved
    assert description is None  # a multi-file run carries no single-file description
    assert source_name == tmp_path.name  # labelled by the common parent, not a lone file
    assert files == [tmp_path / "a.yaml", tmp_path / "b.yaml"]


# --- _filter_scenarios: --tag/--exclude selection plus the --erase override


def _one_scenario(name: str, *, tags: str = "") -> str:
    tag_line = f"  tags: {tags}\n" if tags else ""
    return f"- name: {name}\n{tag_line}  steps:\n    - tap: {{ id: home.title }}\n"


def test_filter_scenarios_selects_by_tag() -> None:
    scenarios = load_scenarios(
        _one_scenario("a", tags="[smoke]") + _one_scenario("b", tags="[slow]")
    )
    got = _filter_scenarios(scenarios, "smoke", "", None, False)
    assert [s.name for s in got] == ["a"]


def test_filter_scenarios_no_match_exits_2() -> None:
    scenarios = load_scenarios(_one_scenario("a", tags="[smoke]"))
    with pytest.raises(typer.Exit) as exc:
        _filter_scenarios(scenarios, "nightly", "", None, False)
    assert exc.value.exit_code == 2


def test_filter_scenarios_erase_override() -> None:
    scenarios = load_scenarios(_one_scenario("a"))
    _filter_scenarios(scenarios, "", "", True, False)  # --erase forces every scenario on
    assert scenarios[0].preconditions.erase is True


def test_filter_scenarios_erase_inherits_target_default() -> None:
    # BE-0177: an unset scenario (erase None) inherits the target config default when no flag is given.
    scenarios = load_scenarios(_one_scenario("a"))
    assert scenarios[0].preconditions.erase is None
    _filter_scenarios(scenarios, "", "", None, True)
    assert scenarios[0].preconditions.erase is True


def test_filter_scenarios_erase_scenario_beats_target() -> None:
    # BE-0177: a scenario's explicit erase wins over the target default; the flag is unset here.
    scenarios = load_scenarios(_one_scenario("a") + "  preconditions:\n    erase: false\n")
    _filter_scenarios(scenarios, "", "", None, True)
    assert scenarios[0].preconditions.erase is False


def test_filter_scenarios_erase_flag_beats_scenario_and_target() -> None:
    # BE-0177: `--no-erase` overrides even a scenario that explicitly set erase: true.
    scenarios = load_scenarios(_one_scenario("a") + "  preconditions:\n    erase: true\n")
    _filter_scenarios(scenarios, "", "", False, True)
    assert scenarios[0].preconditions.erase is False


# `iosTipKitHandling` rides the same BE-0177 precedence as `erase` above, so it gets the same three
# cases. `--no-ios-tipkit-handling` is what the item's on-device verification uses to prove the guard
# is load-bearing, so a silent regression in this override would invalidate that evidence while the
# gate stayed green.


def test_filter_scenarios_ios_tipkit_inherits_target_default() -> None:
    scenarios = load_scenarios(_one_scenario("a"))
    assert scenarios[0].ios_tip_kit_handling is None
    _filter_scenarios(scenarios, "", "", None, False, None, True)
    assert scenarios[0].ios_tip_kit_handling is True


def test_filter_scenarios_ios_tipkit_scenario_beats_target() -> None:
    scenarios = load_scenarios(_one_scenario("a") + "  iosTipKitHandling: false\n")
    _filter_scenarios(scenarios, "", "", None, False, None, True)
    assert scenarios[0].ios_tip_kit_handling is False


def test_filter_scenarios_ios_tipkit_flag_beats_scenario_and_target() -> None:
    scenarios = load_scenarios(_one_scenario("a") + "  iosTipKitHandling: true\n")
    _filter_scenarios(scenarios, "", "", None, False, False, True)
    assert scenarios[0].ios_tip_kit_handling is False


# --- _alert_guard_factory: build the guard factory, or None when no scenario wants one


def test_alert_guard_factory_none_when_all_disabled() -> None:
    scenarios = load_scenarios(
        "- name: a\n  systemAlertHandling: false\n" + "  steps:\n    - tap: { id: home.title }\n"
    )
    assert _alert_guard_factory(scenarios, _eff(), None) is None


def test_alert_guard_factory_none_when_target_disables() -> None:
    # BE-0177: a scenario with no systemAlertHandling inherits the target config's
    # `systemAlertHandling: false`, so the factory builds no guard at all (the enabled bit resolves
    # scenario > target > built-in on).
    scenarios = load_scenarios(_one_scenario("a"))
    assert _alert_guard_factory(scenarios, _eff(systemAlertHandling="false"), None) is None


def test_alert_guard_factory_scenario_reenables_over_target() -> None:
    # BE-0177: a scenario's explicit `systemAlertHandling: true` wins over the target's `false`, so a
    # guard is still built (it no-ops here only because the test env has no AI credential).
    scenarios = load_scenarios(
        "- name: a\n  systemAlertHandling: true\n  steps:\n    - tap: { id: home.title }\n"
    )
    assert _alert_guard_factory(scenarios, _eff(systemAlertHandling="false"), None) is not None


# --- _resolve_network: --network/--no-network flag > target `network` config > built-in on (BE-0177)


def test_resolve_network_flag_wins() -> None:
    assert _resolve_network(False, True) is False  # --no-network overrides a target `network: true`
    assert _resolve_network(True, False) is True  # --network overrides a target `network: false`


def test_resolve_network_falls_back_to_target_then_builtin() -> None:
    assert _resolve_network(None, False) is False  # no flag → the target's `network` config
    assert (
        _resolve_network(None, True) is True
    )  # no flag, target on (the resolve() built-in default)


def test_alert_guard_factory_needs_no_credential_and_reaches_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BE-0402: `run`'s guard is deterministic throughout, with the key present or absent.

    The key is *set* here on purpose: before BE-0402 that is exactly what built the vision locator
    and put a model on `run`'s path. Now the factory consults no credential, prints no
    credential note, and hands back a guard whose every path is native — so the deterministic gate is
    Claude-free by construction rather than by the shell happening to lack a variable.
    """
    from bajutsu.common.analytics import ledger as usage_ledger
    from bajutsu.common.drivers.fake import FakeDriver
    from bajutsu.common.orchestrator import AlertGuardConfig

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    ledger_path = tmp_path / "usage.jsonl"
    eff = _eff(ai=f"{{ usageLedger: {ledger_path} }}")
    usage_ledger.configure_from_ai_config(eff.ai)
    try:
        scenarios = load_scenarios(_one_scenario("a"))
        factory = _alert_guard_factory(scenarios, eff, None)
        assert factory is not None
        guard = factory(scenarios[0])
        assert isinstance(guard, AlertGuardConfig)
        assert not hasattr(guard, "vision")  # the fallback is gone from the type, not merely unused

        # A backend with no native capability: nothing left to try, so the guard reports nothing and
        # leaves no note — an absent alert is not a blocked screen.
        assert guard(FakeDriver([])) is None
        assert guard.blocked_note == ""

        # The native path is untouched — it needs no credential, so it still taps the prompt.
        driver = FakeDriver([])
        driver.system_alert_buttons = [
            {
                "identifier": None,
                "label": label,
                "traits": ["button"],
                "value": None,
                "frame": (0, 0, 10, 10),
                "nativeZ": None,
            }
            for label in ("Don't Allow", "Allow")
        ]
        state, event, _ = guard.probe_native(driver)
        assert state == "dismissed" and event is not None

        assert not ledger_path.exists()  # no AI call, so no attributed event
        # No credential note either way. Named substrings rather than an empty stream, so an
        # unrelated notice the factory may print later does not break a test about the AI boundary.
        out = capsys.readouterr().out
        for said in ("ANTHROPIC_API_KEY", "vision", "credential", "Bedrock", "ai.provider"):
            assert said not in out
    finally:
        usage_ledger.reset()


def test_alert_guard_factory_rejects_a_scenario_vision_instruction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # BE-0402: the key steers only the fallback `run` no longer has. Silently ignoring it would turn
    # a scenario written to *grant* a permission into one that denies it, so the run is refused
    # outright, with the working forms named.
    s = _tap_scenario("a", {"visionInstruction": "tap Allow"})
    with pytest.raises(typer.Exit) as excinfo:
        _alert_guard_factory([s], _eff(), None)
    assert excinfo.value.exit_code == 2
    out = capsys.readouterr().out
    assert "scenario 'a'" in out and "labels" in out and "rules" in out


def test_alert_guard_factory_rejects_a_target_vision_instruction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The same refusal from the layer an author may not have written themselves, named as such.
    eff = _eff(systemAlertHandling='{ visionInstruction: "tap Allow" }')
    with pytest.raises(typer.Exit) as excinfo:
        _alert_guard_factory([_tap_scenario("a")], eff, None)
    assert excinfo.value.exit_code == 2
    assert "target config" in capsys.readouterr().out


def test_alert_guard_factory_rejects_before_any_scenario_gets_a_guard() -> None:
    # The check is eager over the whole suite, not per scenario inside the returned closure: the
    # closure runs in a worker mid-run, so a check there would fail scenario 3 with 1 and 2 already
    # executed. A suite whose *last* scenario carries the key is refused whole, before any of it runs.
    scenarios = [
        _tap_scenario("a"),
        _tap_scenario("b"),
        _tap_scenario("c", {"visionInstruction": "tap Allow"}),
    ]
    with pytest.raises(typer.Exit):
        _alert_guard_factory(scenarios, _eff(), None)


def _tap_scenario(name: str, dismiss: dict[str, object] | None = None) -> Scenario:
    body: dict[str, object] = {"name": name, "steps": [{"tap": {"id": "x"}}]}
    if dismiss is not None:
        body["systemAlertHandling"] = dismiss
    return Scenario.model_validate(body)


def test_alert_guard_factory_resolves_native_labels_and_poll_interval() -> None:
    # BE-0315: a scenario's candidate-label list and pollInterval reach the AlertGuardConfig — the
    # wiring that makes the deterministic native path actually fire with the author's button
    # policy. `labels` is the key that carries it since BE-0401.
    s = _tap_scenario("a", {"labels": ["Allow", "OK"], "pollInterval": 2})
    guard = _alert_guard_factory([s], _eff(), None)(s)  # type: ignore[misc]
    assert guard is not None
    assert guard.labels == ["Allow", "OK"]
    assert guard.poll_interval == 2.0


def test_alert_guard_factory_poll_interval_precedence() -> None:
    # scenario > target > built-in default (BE-0177 / BE-0315).
    eff = _eff(systemAlertHandling="{ pollInterval: 3 }")
    s_override = _tap_scenario("a", {"pollInterval": 5})
    assert _alert_guard_factory([s_override], eff, None)(s_override).poll_interval == 5.0  # type: ignore[misc,union-attr]
    s_plain = _tap_scenario("b")
    assert _alert_guard_factory([s_plain], eff, None)(s_plain).poll_interval == 3.0  # type: ignore[misc,union-attr]
    assert (
        _alert_guard_factory([s_plain], _eff(), None)(s_plain).poll_interval  # type: ignore[misc,union-attr]
        == DEFAULT_ALERT_POLL_INTERVAL
    )


def test_alert_guard_factory_labels_concatenate_across_layers() -> None:
    # The native path compares labels exactly, so it walks both layers' candidates in innermost-first
    # order (BE-0401). Since BE-0402 that list is the *only* thing a label can steer, so nothing
    # narrows it back to one layer.
    eff = _eff(systemAlertHandling='{ labels: ["Allow"] }')
    s = _tap_scenario("a", {"labels": ["Don’t Allow"]})
    guard = _alert_guard_factory([s], eff, None)(s)  # type: ignore[misc]
    assert guard is not None
    assert guard.labels == ["Don’t Allow", "Allow"]


def test_apply_system_alert_handling_off_replaces_the_whole_field() -> None:
    # BE-0401: off *is* `False` now the boolean carries on and off, and an off guard reads no
    # policy — so `--no-system-alert-handling` needs to keep nothing.
    s = _tap_scenario("a", {"labels": ["Allow"], "pollInterval": 2})
    _apply_system_alert_handling([s], False)
    assert s.system_alert_handling is False


def test_apply_system_alert_handling_on_preserves_the_policy_and_re_enables() -> None:
    s = _tap_scenario("a", {"labels": ["Allow"], "pollInterval": 2})
    _apply_system_alert_handling([s], True)
    assert isinstance(s.system_alert_handling, SystemAlertHandling)
    assert s.system_alert_handling.labels == ["Allow"]
    assert s.system_alert_handling.poll_interval == 2.0

    # A scenario that had switched itself off comes back on with the empty policy.
    off = Scenario.model_validate(
        {"name": "b", "systemAlertHandling": False, "steps": [{"tap": {"id": "x"}}]}
    )
    _apply_system_alert_handling([off], True)
    assert off.system_alert_handling == SystemAlertHandling()


def test_apply_system_alert_handling_unset_flag_is_a_no_op() -> None:
    s = _tap_scenario("a", {"rules": [{"prompt": "notifications", "choice": "grant"}]})
    _apply_system_alert_handling([s], None)
    assert isinstance(s.system_alert_handling, SystemAlertHandling)
    assert [(r.prompt, r.choice) for r in s.system_alert_handling.rules] == [
        ("notifications", "grant")
    ]


# --- _resolve_rules: a scenario's `rules` resolved to identifying/tap labels for its locale


def test_resolve_rules_resolves_labels_for_locale() -> None:
    rules = [SystemAlertRule(prompt="notifications", choice="grant")]
    resolved = _resolve_rules(rules, "en")
    assert len(resolved) == 1
    assert resolved[0].identifying_labels == {"Allow", "Don’t Allow"}
    assert resolved[0].tap_label == "Allow"


def test_resolve_rules_resolves_the_deny_choice() -> None:
    rules = [SystemAlertRule(prompt="tracking", choice="deny")]
    resolved = _resolve_rules(rules, "en")
    assert resolved[0].tap_label == "Ask App Not to Track"


def test_resolve_rules_raises_on_an_uncovered_locale() -> None:
    rules = [SystemAlertRule(prompt="notifications", choice="grant")]
    with pytest.raises(UncoveredSystemAlertLocale) as exc:
        _resolve_rules(rules, "fr")
    # The message must name the surface that actually failed — the guard's `rules` — and offer the
    # guard's own remedy, not `handleSystemAlert`'s `sel.label`, which the scenario need not use.
    message = str(exc.value)
    assert "systemAlertHandling.rules" in message
    assert "labels" in message
    assert "sel.label" not in message
    # The covered languages, like the step's own message reports.
    for language in covered_languages("notifications"):
        assert language in message


def test_resolve_rules_empty_list_stays_empty() -> None:
    assert _resolve_rules([], "en") == []


# --- _alert_guard_factory: rules resolve and layer scenario-over-target


def test_alert_guard_factory_resolves_scenario_rules() -> None:
    s = _tap_scenario(
        "a",
        {
            "rules": [
                {"prompt": "notifications", "choice": "grant"},
                {"prompt": "tracking", "choice": "deny"},
            ]
        },
    )
    guard = _alert_guard_factory([s], _eff(), None)(s)  # type: ignore[misc]
    assert guard is not None
    assert [r.tap_label for r in guard.rules] == ["Allow", "Ask App Not to Track"]


def test_alert_guard_factory_scenario_rule_shadows_target_rule_for_the_same_prompt() -> None:
    eff = _eff(systemAlertHandling="{ rules: [{ prompt: notifications, choice: deny }] }")
    s = _tap_scenario("a", {"rules": [{"prompt": "notifications", "choice": "grant"}]})
    guard = _alert_guard_factory([s], eff, None)(s)  # type: ignore[misc]
    assert guard is not None
    # The scenario's rule for `notifications` comes first, so it wins the first-match.
    assert [r.tap_label for r in guard.rules] == ["Allow", "Don’t Allow"]
    assert match_alert_rule(guard.rules, ["Allow", "Don’t Allow"]) == "Allow"


def test_alert_guard_factory_target_rule_applies_when_scenario_has_none() -> None:
    eff = _eff(systemAlertHandling="{ rules: [{ prompt: tracking, choice: deny }] }")
    s = _tap_scenario("a")
    guard = _alert_guard_factory([s], eff, None)(s)  # type: ignore[misc]
    assert guard is not None
    assert [r.tap_label for r in guard.rules] == ["Ask App Not to Track"]


def test_alert_guard_factory_target_rule_answers_a_scenario_with_its_own_labels() -> None:
    # BE-0401 reversed BE-0382's all-or-nothing suppression: specificity, not the layer a
    # declaration came from, settles the conflict. A target rule names a prompt and a scenario's
    # labels name no prompt at all, so both stay in effect — the rule answers tracking, the labels
    # answer everything else.
    eff = _eff(systemAlertHandling="{ rules: [{ prompt: tracking, choice: deny }] }")
    s = _tap_scenario("a", {"labels": ["Allow"]})
    guard = _alert_guard_factory([s], eff, None)(s)  # type: ignore[misc]
    assert guard is not None
    assert [r.tap_label for r in guard.rules] == ["Ask App Not to Track"]
    assert guard.labels == ["Allow"]


def test_alert_guard_factory_notices_a_target_rule_reaching_a_self_answering_scenario(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The composition above is correct but was *silent* under BE-0382 — a project-wide edit changing
    # a scenario that names no prompt. The notice removes the silence, and is keyed on the scenario
    # as well as the prompt, so a run of many scenarios warns once *per affected scenario* rather
    # than once for the first and silence for the rest.
    import logging

    from bajutsu.common import deprecations

    eff = _eff(systemAlertHandling="{ rules: [{ prompt: tracking, choice: deny }] }")
    scenarios = [_tap_scenario("a", {"labels": ["Allow"]}), _tap_scenario("b", {"labels": ["OK"]})]
    for s in scenarios:
        deprecations._emitted.discard(f"systemAlertHandling.targetRule.{s.name}.tracking")
    factory = _alert_guard_factory(scenarios, eff, None)
    assert factory is not None
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        for s in scenarios:
            factory(s)
    noticed = [r.message for r in caplog.records if "tracking" in r.message]
    assert len(noticed) == 2
    assert "'a'" in noticed[0] and "'b'" in noticed[1]

    # ...and once per scenario and prompt, not once per guard construction: building the same
    # scenario's guard again adds nothing.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        factory(scenarios[0])
    assert not [r for r in caplog.records if "tracking" in r.message]


def test_alert_guard_factory_does_not_notice_a_target_rule_the_scenario_shadows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The scenario's own rule for that prompt wins the first-match, so the target's rule never
    # answers for it — nothing to notice.
    import logging

    from bajutsu.common import deprecations

    eff = _eff(systemAlertHandling="{ rules: [{ prompt: tracking, choice: deny }] }")
    s = _tap_scenario(
        "a", {"rules": [{"prompt": "tracking", "choice": "grant"}], "labels": ["Allow"]}
    )
    deprecations._emitted.discard("systemAlertHandling.targetRule.a.tracking")
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        _alert_guard_factory([s], eff, None)(s)  # type: ignore[misc]
    assert not [r for r in caplog.records if "tracking" in r.message]


def test_alert_guard_factory_does_not_notice_when_only_the_target_declares_anything(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The notice is for a scenario that answers for itself. A scenario declaring nothing, under a
    # target that supplies both a rule and its own labels, is not that case — reading the composed
    # label list instead of the scenario's and the flag's own would warn here wrongly.
    import logging

    from bajutsu.common import deprecations

    eff = _eff(
        systemAlertHandling='{ rules: [{ prompt: tracking, choice: deny }], labels: ["Cancel"] }'
    )
    s = _tap_scenario("a")
    deprecations._emitted.discard("systemAlertHandling.targetRule.a.tracking")
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        guard = _alert_guard_factory([s], eff, None)(s)  # type: ignore[misc]
    assert guard is not None and guard.labels == ["Cancel"]
    assert not [r for r in caplog.records if "tracking" in r.message]


def test_alert_guard_factory_notices_when_only_the_flag_answers_for_the_scenario(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # `--alert-labels` sits above the target too, so a flag-supplied button policy makes the run one
    # that answers for itself just as a scenario's own labels would.
    import logging

    from bajutsu.common import deprecations

    eff = _eff(systemAlertHandling="{ rules: [{ prompt: tracking, choice: deny }] }")
    s = _tap_scenario("a")
    deprecations._emitted.discard("systemAlertHandling.targetRule.a.tracking")
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        _alert_guard_factory([s], eff, _flag_alert_policy("Allow", None))(s)  # type: ignore[misc]
    assert [r for r in caplog.records if "tracking" in r.message]


def test_alert_guard_factory_composes_labels_across_all_three_layers() -> None:
    # A list composes by concatenation, innermost layer first (BE-0401): the scenario's answers are
    # tried first, and the layers above stay reachable for whatever it did not answer.
    eff = _eff(systemAlertHandling='{ labels: ["Cancel"] }')
    s = _tap_scenario("a", {"labels": ["Allow"]})
    flag = _flag_alert_policy("OK", None)
    guard = _alert_guard_factory([s], eff, flag)(s)  # type: ignore[misc]
    assert guard is not None
    assert guard.labels == ["Allow", "OK", "Cancel"]


def test_alert_guard_factory_scalars_take_the_innermost_layer_that_supplies_one() -> None:
    # A scalar holds one value, so composition is not available and the innermost layer wins.
    # `pollInterval` is the only scalar left to `run` — BE-0402 took `visionInstruction` away.
    eff = _eff(systemAlertHandling="{ pollInterval: 3 }")
    flag = _flag_alert_policy("", 4)
    s_scenario = _tap_scenario("a", {"pollInterval": 5})
    guard = _alert_guard_factory([s_scenario], eff, flag)(s_scenario)  # type: ignore[misc]
    assert guard is not None and guard.poll_interval == 5.0

    s_plain = _tap_scenario("b")
    guard = _alert_guard_factory([s_plain], eff, flag)(s_plain)  # type: ignore[misc]
    assert guard is not None and guard.poll_interval == 4.0  # the flag layer, below the scenario

    guard = _alert_guard_factory([s_plain], eff, None)(s_plain)  # type: ignore[misc]
    assert guard is not None and guard.poll_interval == 3.0  # then the target config


def test_flag_alert_policy_builds_the_command_line_layer() -> None:
    # Two keys, not three: `run` retired `--alert-vision-instruction` with the fallback it steered
    # (BE-0402), so no flag value can reach a key the command can no longer act on.
    assert _flag_alert_policy("", None) is None
    policy = _flag_alert_policy("Allow,OK", 2)
    assert policy == SystemAlertHandling(labels=["Allow", "OK"], pollInterval=2)
    assert policy.vision_instruction is None


def test_flag_alert_policy_strips_each_label() -> None:
    # `--alert-labels "Allow, OK"` names two buttons. Without the strip the second would be " OK",
    # a label the native path compares exactly and so could never match.
    policy = _flag_alert_policy("Allow, OK", None)
    assert policy is not None and policy.labels == ["Allow", "OK"]


def test_flag_alert_policy_rejects_an_empty_label_as_a_cli_error() -> None:
    # Validated through the same model the file layers use, so a typo cannot resolve to the
    # dismissive default the flag was written to override — reported as a CLI error, not a traceback.
    with pytest.raises(typer.Exit) as exc:
        _flag_alert_policy(",", None)
    assert exc.value.exit_code == 2


# --- _resolve_evidence_dirs: baselines/schemas dirs plus the golden context (only when it exists)


def test_resolve_evidence_dirs_defaults_and_golden_context(tmp_path: Path) -> None:
    scn = tmp_path / "e2e" / "s.yaml"
    scn.parent.mkdir()
    baselines_dir, schemas_dir, gc = _resolve_evidence_dirs("", "", "", _eff(), scn)
    assert baselines_dir == scn.parent / "baselines"
    assert schemas_dir == scn.parent / "schemas"
    assert gc is None  # no goldens dir beside the scenario → no golden context
    (scn.parent / "goldens").mkdir()
    _, _, gc2 = _resolve_evidence_dirs("", "", "", _eff(), scn)
    assert gc2 is not None  # goldens dir exists → golden assertions can resolve within it
