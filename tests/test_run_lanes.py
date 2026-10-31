"""Lane resolution for `bajutsu run` — how --udid / --workers map to a device pool,
plus the run command's directory-resolution and file-expansion helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bajutsu.cli._shared import _with_erase_flag
from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.orchestrator import (
    DEFAULT_ALERT_POLL_INTERVAL,
    DEFAULT_FROZEN_SCREEN_TIMEOUT,
    AlertGuardConfig,
)
from bajutsu.common.orchestrator.types import ResolvedAlertRule
from bajutsu.common.scenario import (
    Scenario,
    SystemAlertInterrupt,
    SystemAlertRule,
    TipKitInterrupt,
    load_scenarios,
    system_alert_entries,
    tip_kit_entry,
)
from bajutsu.common.scenario.system_alerts import UncoveredSystemAlertLocale, covered_languages
from bajutsu.run.cli import (
    _alert_guard_factory,
    _expand_file,
    _filter_scenarios,
    _frozen_screen_timeout,
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


def _match_tap_label(rules: list[ResolvedAlertRule], buttons: list[str]) -> str | None:
    """The tap label of the first rule that identifies *buttons*, or None.

    The runner's accept test (`InterruptionRule.matches`, BajutsuKit), restated: every identifying
    label on the alert exactly once, and no excluded label present at all.
    """
    for rule in rules:
        if all(buttons.count(label) == 1 for label in rule.identifying_labels) and not any(
            label in buttons for label in rule.excluded_labels
        ):
            return rule.tap_label
    return None


# The group each config field these tests set sits under (BE-0452); `ai` stays at the target's top.
_GROUP = {
    "baselines": "paths",
    "schemas": "paths",
    "goldens": "paths",
    "secrets": "evidence",
    "interrupts": "hooks",
}


def _eff(**target: str) -> Effective:
    # A minimal iOS target; pass baselines/schemas/goldens/secrets/ai to fill those config fields.
    groups: dict[str, str] = {}
    top = ""
    for k, v in target.items():
        if k in _GROUP:
            groups[_GROUP[k]] = groups.get(_GROUP[k], "") + f"      {k}: {v}\n"
        else:
            top += f"    {k}: {v}\n"
    fields = top + "".join(f"    {g}:\n{body}" for g, body in groups.items())
    cfg = load_config(f"targets:\n  x:\n    platform: ios\n    subject: {{id: com.x}}\n{fields}")
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
    scenarios, description, plan_sources = _expand_file(path, root=tmp_path)
    assert description == "a suite"
    assert [s.name for s in scenarios] == ["demo"]
    assert plan_sources["demo"].file_name == "s.yaml"
    assert plan_sources["demo"].step_lines == [5]


def test_expand_file_component_expansion_drops_step_lines(tmp_path: Path) -> None:
    # A component's steps replace the one `use:` step, so the pre-expansion step count no longer
    # matches the executed one — line numbers would misattribute, so they're dropped; the verbatim
    # text (with its comments) is kept regardless, since it is still accurate as authored.
    (tmp_path / "login.yaml").write_text(
        "steps:\n  - tap: { id: login }\n  - tap: { id: ok }\n", encoding="utf-8"
    )
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo  # the scenario under test\n"
        "  steps:\n"
        "    - use: { component: ./login.yaml }\n",
        encoding="utf-8",
    )
    scenarios, _description, plan_sources = _expand_file(path, root=tmp_path)
    assert len(scenarios[0].steps) == 2  # the component's two steps
    plan = plan_sources["demo"]
    assert plan.step_lines == []
    assert plan.text is not None
    assert "the scenario under test" in plan.text  # the comment survives


def test_expand_file_target_group_drops_step_lines(tmp_path: Path) -> None:
    # BE-0437: a target group expands one raw `steps:` item into several parsed steps, so the raw
    # per-item line numbers no longer line up with the executed steps either — the same guard that
    # catches a component's step-count change must catch this one too.
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo\n"
        "  targets: [x]\n"
        "  steps:\n"
        "    - target: x\n"
        "      steps:\n"
        "        - tap: { id: a }\n"
        "        - tap: { id: b }\n",
        encoding="utf-8",
    )
    scenarios, _description, plan_sources = _expand_file(path, root=tmp_path)
    assert len(scenarios[0].steps) == 2  # the group's two children, flattened
    plan = plan_sources["demo"]
    assert plan.step_lines == []
    assert plan.text is not None


def test_expand_file_data_driven_scenario_drops_verbatim_text(tmp_path: Path) -> None:
    # A `data:`-driven scenario expands into one Scenario per row with `${row.*}` substituted, but
    # `expand_data` never changes the step count — the guard above cannot catch this case. Every
    # row shares one `plan_sources` entry (keyed by the declared, row-suffix-stripped name), so a
    # verbatim pre-expansion slice would show the same unsubstituted `${row.email}` template for
    # every row. The plan drops the text entirely instead, so the report falls back to a per-row
    # structured re-dump that shows each row's own substituted value.
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: checkout\n"
        "  data:\n"
        "    - email: a@example.com\n"
        "    - email: b@example.com\n"
        "  steps:\n"
        '    - type: { into: { id: email }, text: "${row.email}" }\n',
        encoding="utf-8",
    )
    scenarios, _description, plan_sources = _expand_file(path, root=tmp_path)
    assert len(scenarios) == 2  # one per data row
    assert all("checkout" in s.name for s in scenarios)
    plan = plan_sources["checkout"]
    assert plan.text is None
    assert plan.step_lines == []
    assert plan.file_name == "s.yaml"  # the file name is still attributed


def test_expand_file_sets_source_stem_from_the_file_name(tmp_path: Path) -> None:
    # BE-0417: every expanded scenario carries the loaded file's own stem, independent of its name:.
    path = tmp_path / "login_flow.yaml"
    path.write_text(
        "- name: Login succeeds with a valid password\n  steps:\n    - tap: { id: ok }\n",
        encoding="utf-8",
    )
    scenarios, _description, _plan_sources = _expand_file(path, root=tmp_path)
    assert [s.source_stem for s in scenarios] == ["login_flow"]


def test_expand_file_data_driven_rows_all_share_the_source_stem(tmp_path: Path) -> None:
    # Every row a data-driven scenario expands into is set *after* expand_data, so it carries the
    # same stem as the file it came from, not just the pre-expansion source scenario.
    path = tmp_path / "login_flow.yaml"
    path.write_text(
        '- name: login\n  data: [{target: "a"}, {target: "b"}]\n'
        '  steps:\n    - tap: { id: "${row.target}" }\n',
        encoding="utf-8",
    )
    scenarios, _description, _plan_sources = _expand_file(path, root=tmp_path)
    assert len(scenarios) == 2
    assert [s.source_stem for s in scenarios] == ["login_flow", "login_flow"]


def test_expand_file_missing_component_ref_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo\n  steps:\n    - use: { component: missing.yaml }\n", encoding="utf-8"
    )
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, root=tmp_path)
    assert exc.value.exit_code == 2


def test_expand_file_missing_data_file_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo\n  dataFile: missing.csv\n  steps:\n    - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    with pytest.raises(typer.Exit) as exc:
        _expand_file(path, root=tmp_path)
    assert exc.value.exit_code == 2


# --- _resolve_secrets: bind declared secrets present in the environment (never the absent ones)


def test_resolve_secrets_binds_only_present_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN", "s3cr3t")
    monkeypatch.delenv("ABSENT", raising=False)
    bindings, values = _resolve_secrets([_eff(secrets="[TOKEN, ABSENT]")])
    assert bindings == {"secrets.TOKEN": "s3cr3t"}  # ABSENT is unbound, not an empty string
    assert values == ["s3cr3t"]


def test_resolve_secrets_unions_across_declared_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    # A step routed to a non-primary target may use a `${secrets.X}` declared only on that
    # target's own config, not the primary's (BE-0428) — every declared target's names are read.
    monkeypatch.setenv("APP_TOKEN", "app-secret")
    monkeypatch.setenv("WEB_TOKEN", "web-secret")
    bindings, values = _resolve_secrets([_eff(secrets="[APP_TOKEN]"), _eff(secrets="[WEB_TOKEN]")])
    assert bindings == {"secrets.APP_TOKEN": "app-secret", "secrets.WEB_TOKEN": "web-secret"}
    assert sorted(values) == ["app-secret", "web-secret"]


# --- _load_scenarios: the --scenario file, or the target's configured dir


def test_load_scenarios_single_file(tmp_path: Path) -> None:
    scn = tmp_path / "s.yaml"
    scn.write_text(
        "description: suite\nscenarios:\n  - name: demo\n    steps:\n      - tap: { id: home.title }\n",
        encoding="utf-8",
    )
    scenarios, description, source_name, files, plan_sources = _load_scenarios(
        _eff(), [str(scn)], "x"
    )
    assert [s.name for s in scenarios] == ["demo"]
    assert description == "suite"  # the single file's description rides back
    assert source_name == "s.yaml"
    assert files == [scn]
    assert plan_sources["demo"].file_name == "s.yaml"


def test_load_scenarios_multiple_files_share_one_run(tmp_path: Path) -> None:
    # Repeating --scenario runs several files in one process (one warm runner): the scenarios
    # concatenate in order, it is not a lone-file run (no single-file description), and the report's
    # source label is the files' common parent, not any one file.
    for name, demo in (("a.yaml", "one"), ("b.yaml", "two")):
        (tmp_path / name).write_text(
            f"scenarios:\n  - name: {demo}\n    steps:\n      - tap: {{ id: home.title }}\n",
            encoding="utf-8",
        )
    scenarios, description, source_name, files, plan_sources = _load_scenarios(
        _eff(), [str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")], "x"
    )
    assert [s.name for s in scenarios] == ["one", "two"]  # concatenated, order preserved
    assert description is None  # a multi-file run carries no single-file description
    assert source_name == tmp_path.name  # labelled by the common parent, not a lone file
    assert files == [tmp_path / "a.yaml", tmp_path / "b.yaml"]
    assert plan_sources["one"].file_name == "a.yaml"
    assert plan_sources["two"].file_name == "b.yaml"


def test_load_scenarios_drops_plan_source_for_a_name_collision_across_files(
    tmp_path: Path,
) -> None:
    # Two files can declare the same scenario name — nothing enforces uniqueness across files, only
    # within one. A name-keyed plan_sources lookup cannot tell the two scenarios' panels apart, so
    # the colliding name is dropped rather than let one file's source attach to the other's scenario.
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(
            "- name: login\n  steps:\n    - tap: { id: home.title }\n", encoding="utf-8"
        )
    scenarios, _description, _source_name, _files, plan_sources = _load_scenarios(
        _eff(), [str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")], "x"
    )
    assert len(scenarios) == 2  # both scenarios still ran
    assert "login" not in plan_sources  # neither gets the other's source


def test_expand_file_skips_a_name_that_only_looks_like_a_data_row(tmp_path: Path) -> None:
    # `declared_name` strips a `[row N]`-shaped suffix unconditionally to recover a data-driven
    # scenario's base name. A scenario authored with that exact shape in its own name (not one
    # `expand_data` added) strips to a name `plan_by_name` never had — a valid file, not a bug here,
    # so it gets no recovered plan rather than crashing the run with a KeyError.
    path = tmp_path / "s.yaml"
    path.write_text(
        "- name: demo [row 1]\n  steps:\n    - tap: { id: home.title }\n", encoding="utf-8"
    )
    scenarios, _description, plan_sources = _expand_file(path, root=tmp_path)
    assert [s.name for s in scenarios] == ["demo [row 1]"]
    assert plan_sources == {}


# --- _filter_scenarios: --tag/--exclude selection plus the --erase override


def _one_scenario(name: str, *, tags: str = "") -> str:
    tag_line = f"  tags: {tags}\n" if tags else ""
    return f"- name: {name}\n{tag_line}  steps:\n    - tap: {{ id: home.title }}\n"


def test_filter_scenarios_selects_by_tag() -> None:
    scenarios = load_scenarios(
        _one_scenario("a", tags="[smoke]") + _one_scenario("b", tags="[slow]")
    )
    got = _filter_scenarios(scenarios, "smoke", "", None)
    assert [s.name for s in got] == ["a"]


def test_filter_scenarios_no_match_exits_2() -> None:
    scenarios = load_scenarios(_one_scenario("a", tags="[smoke]"))
    with pytest.raises(typer.Exit) as exc:
        _filter_scenarios(scenarios, "nightly", "", None)
    assert exc.value.exit_code == 2


def test_filter_scenarios_erase_override() -> None:
    scenarios = load_scenarios(_one_scenario("a"))
    _filter_scenarios(scenarios, "", "", True)  # --erase raises every scenario to the top rung
    assert scenarios[0].preconditions.subject.reinstall == "erase"


def test_filter_scenarios_without_a_flag_leaves_each_scenario_as_written() -> None:
    # Each target the scenario drives merges its own `subject.reinstall` (BE-0473), so none is stamped.
    scenarios = load_scenarios(_one_scenario("a"))
    _filter_scenarios(scenarios, "", "", None)
    assert scenarios[0].preconditions.subject.reinstall is None


@pytest.mark.parametrize(
    ("written", "want"),
    [
        ("  preconditions:\n    subject: { reinstall: erase }\n", "clean"),
        ("  preconditions:\n    subject: { reinstall: overwrite }\n", "overwrite"),
        ("", None),  # a target's own erase is lowered on its `Effective` instead
    ],
)
def test_filter_scenarios_no_erase_lowers_only_the_scenarios_erase(
    written: str, want: str | None
) -> None:
    scenarios = load_scenarios(_one_scenario("a") + written)
    _filter_scenarios(scenarios, "", "", False)
    assert scenarios[0].preconditions.subject.reinstall == want


@pytest.mark.parametrize(
    ("target", "flag", "want"),
    [
        ("erase", False, "clean"),
        ("overwrite", False, "overwrite"),
        ("erase", None, "erase"),
        ("clean", True, "clean"),  # `--erase` raises the scenario's value, which wins anyway
    ],
)
def test_no_erase_lowers_every_targets_own_erase(target: str, flag: bool | None, want: str) -> None:
    eff = resolve(
        load_config(
            f"targets:\n  t:\n    platform: ios\n    subject: {{id: com.x, reinstall: {target}}}\n"
        ),
        "t",
    )
    assert _with_erase_flag(eff, flag).reinstall == want


# The `tipKit` interrupts entry rides the BE-0177 precedence (flag, scenario, target), so it gets
# three cases. `--no-ios-tipkit-handling` is what the item's on-device verification uses to prove the
# guard is load-bearing, so a silent regression in this override would invalidate that evidence while
# the gate stayed green.


def _tip_kit(s: Scenario) -> str | None:
    entry = tip_kit_entry(s.interrupts)
    return entry.tip_kit if entry is not None else None


def test_filter_scenarios_ios_tipkit_inherits_target_default() -> None:
    scenarios = load_scenarios(_one_scenario("a"))
    assert _tip_kit(scenarios[0]) is None
    _filter_scenarios(scenarios, "", "", None, None, True)
    assert _tip_kit(scenarios[0]) == "dismiss"
    assert scenarios[0].dismisses_tips


def test_filter_scenarios_ios_tipkit_target_off_writes_nothing() -> None:
    # A scenario that drives no iOS target never gains the iOS-only entry from an off target.
    scenarios = load_scenarios(_one_scenario("a"))
    _filter_scenarios(scenarios, "", "", None, None, False)
    assert _tip_kit(scenarios[0]) is None
    assert not scenarios[0].dismisses_tips


def test_filter_scenarios_ios_tipkit_scenario_beats_target() -> None:
    scenarios = load_scenarios(_one_scenario("a") + "  interrupts:\n    - tipKit: leave\n")
    _filter_scenarios(scenarios, "", "", None, None, True)
    assert _tip_kit(scenarios[0]) == "leave"
    assert not scenarios[0].dismisses_tips


def test_filter_scenarios_ios_tipkit_flag_beats_scenario_and_target() -> None:
    scenarios = load_scenarios(_one_scenario("a") + "  interrupts:\n    - tipKit: dismiss\n")
    _filter_scenarios(scenarios, "", "", None, False, True)
    assert _tip_kit(scenarios[0]) == "leave"
    # One entry, not two: the flag replaces the scenario's own.
    assert sum(isinstance(e, TipKitInterrupt) for e in scenarios[0].interrupts) == 1


def test_filter_scenarios_ios_tipkit_flag_on_writes_dismiss_and_keeps_other_entries() -> None:
    scenarios = load_scenarios(
        _one_scenario("a")
        + "  interrupts:\n    - tipKit: leave\n    - systemAlert: notifications\n"
        + "      choice: grant\n"
    )
    _filter_scenarios(scenarios, "", "", None, True, False)
    assert _tip_kit(scenarios[0]) == "dismiss"
    assert [e.system_alert for e in system_alert_entries(scenarios[0].interrupts)] == [
        "notifications"
    ]


# --- _alert_guard_factory: build the guard factory, or None when the flag turns it off


def test_alert_guard_factory_none_when_the_flag_turns_it_off() -> None:
    assert _alert_guard_factory(_eff(), enabled=False, flag_poll_interval=None) is None


def test_alert_guard_factory_on_by_default_and_when_the_flag_turns_it_on() -> None:
    # No config key switches the whole guard off any more (BE-0473), so unset means on.
    s = _tap_scenario("a")
    for enabled in (None, True):
        factory = _alert_guard_factory(_eff(), enabled=enabled, flag_poll_interval=None)
        assert factory is not None
        guard = factory(s)
        assert guard is not None and guard.rules == []


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
    from bajutsu.common.orchestrator import AlertGuardConfig

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("BAJUTSU_AI_PROVIDER", raising=False)
    ledger_path = tmp_path / "usage.jsonl"
    eff = _eff(ai=f"{{ usageLedger: {ledger_path} }}")
    usage_ledger.configure_from_ai_config(eff.ai)
    try:
        scenarios = [_tap_scenario("a", [{"systemAlert": "notifications", "choice": "grant"}])]
        factory = _alert_guard_factory(eff, enabled=None, flag_poll_interval=None)
        assert factory is not None
        guard = factory(scenarios[0])
        assert isinstance(guard, AlertGuardConfig)
        assert not hasattr(guard, "vision")  # the fallback is gone from the type, not merely unused

        # The guard's rules reach the backend unchanged, which answers the prompt the scenario's own
        # rule names inside a read — no credential, no model (BE-0461). The labels are the ones
        # `system_alerts` resolves for `en`, since BE-0406 leaves an alert no rule identifies alone.
        from bajutsu.common.drivers.fake import ResolvingFakeDriver
        from bajutsu.common.orchestrator.types import push_interruption_policy

        driver = ResolvingFakeDriver([])
        driver.system_alert_buttons = [
            {
                "identifier": None,
                "label": label,
                "traits": ["button"],
                "value": None,
                "frame": (0, 0, 10, 10),
                "nativeZ": None,
            }
            for label in ("Don’t Allow", "Allow")
        ]
        push_interruption_policy(driver, guard, scenario_start=True)
        assert driver.query_resolving("true").drained.tapped == ["Allow"]

        assert not ledger_path.exists()  # no AI call, so no attributed event
        # No credential note either way. Named substrings rather than an empty stream, so an
        # unrelated notice the factory may print later does not break a test about the AI boundary.
        out = capsys.readouterr().out
        for said in ("ANTHROPIC_API_KEY", "vision", "credential", "Bedrock", "ai.provider"):
            assert said not in out
    finally:
        usage_ledger.reset()


def _tap_scenario(name: str, entries: list[dict[str, object]] | None = None) -> Scenario:
    body: dict[str, object] = {"name": name, "steps": [{"tap": {"id": "x"}}]}
    if entries is not None:
        body["interrupts"] = entries
    return Scenario.model_validate(body)


def _guard(
    s: Scenario, eff: Effective | None = None, flag: float | None = None
) -> AlertGuardConfig:
    factory = _alert_guard_factory(eff or _eff(), enabled=None, flag_poll_interval=flag)
    assert factory is not None
    guard = factory(s)
    assert guard is not None
    return guard


def test_alert_guard_factory_poll_interval_precedence() -> None:
    # scenario > flag > target > built-in default (BE-0177 / BE-0315), each layer by its entries.
    eff = _eff(interrupts="[{ systemAlert: tracking, choice: deny, pollInterval: 3 }]")
    s_override = _tap_scenario(
        "a", [{"systemAlert": "notifications", "choice": "grant", "pollInterval": 5}]
    )
    assert _guard(s_override, eff).poll_interval == 5.0
    assert _guard(s_override, eff, flag=4).poll_interval == 5.0
    s_plain = _tap_scenario("b")
    assert _guard(s_plain, eff, flag=4).poll_interval == 4.0  # the flag layer, below the scenario
    assert _guard(s_plain, eff).poll_interval == 3.0  # then the target config
    assert _guard(s_plain).poll_interval == DEFAULT_ALERT_POLL_INTERVAL


def test_alert_guard_factory_poll_interval_takes_the_smallest_within_a_layer() -> None:
    # A shorter interval satisfies every entry that asked for a longer one (BE-0473), and a layer
    # that sets one still wins over a lower layer's smaller value.
    eff = _eff(interrupts="[{ systemAlert: tracking, choice: deny, pollInterval: 0.5 }]")
    s = _tap_scenario(
        "a",
        [
            {"systemAlert": "notifications", "choice": "grant", "pollInterval": 5},
            {"systemAlert": "paste", "choice": "leave", "pollInterval": 2},
        ],
    )
    assert _guard(s, eff).poll_interval == 2.0


def test_alert_guard_factory_rejects_a_non_positive_flag_poll_interval() -> None:
    # Reported as a CLI error rather than a validation traceback out of the run.
    with pytest.raises(typer.Exit) as exc:
        _alert_guard_factory(_eff(), enabled=None, flag_poll_interval=0)
    assert exc.value.exit_code == 2


def test_alert_guard_factory_frozen_screen_timeout_layers_per_prompt() -> None:
    # The value describes the app, so a scenario entry for the same prompt that omits it inherits the
    # target's; one that sets it wins; the built-in default applies when neither does.
    eff = _eff(interrupts="[{ systemAlert: savePassword, choice: deny, frozenScreenTimeout: 30 }]")
    inherit = _tap_scenario("a", [{"systemAlert": "savePassword", "choice": "grant"}])
    assert _guard(inherit, eff).frozen_screen_timeout == 30.0
    override = _tap_scenario(
        "b", [{"systemAlert": "savePassword", "choice": "grant", "frozenScreenTimeout": 0}]
    )
    assert _guard(override, eff).frozen_screen_timeout == 0.0
    assert _guard(_tap_scenario("c"), eff).frozen_screen_timeout == 30.0
    assert _guard(_tap_scenario("d")).frozen_screen_timeout == DEFAULT_FROZEN_SCREEN_TIMEOUT


def test_alert_guard_factory_frozen_screen_timeout_ignores_a_left_prompt() -> None:
    # A `leave` answers nothing, so its tuning value arms nothing either.
    eff = _eff(interrupts="[{ systemAlert: savePassword, choice: deny, frozenScreenTimeout: 30 }]")
    s = _tap_scenario("a", [{"systemAlert": "savePassword", "choice": "leave"}])
    guard = _guard(s, eff)
    assert guard.rules == []
    assert guard.frozen_screen_timeout == DEFAULT_FROZEN_SCREEN_TIMEOUT


def test_frozen_screen_timeout_takes_the_most_tolerant_value() -> None:
    def entry(v: float) -> SystemAlertInterrupt:
        return SystemAlertInterrupt.model_validate(
            {"systemAlert": "savePassword", "choice": "deny", "frozenScreenTimeout": v}
        )

    assert _frozen_screen_timeout([entry(5), entry(20)]) == 20.0
    assert _frozen_screen_timeout([entry(5), entry(0)]) == 0.0
    assert _frozen_screen_timeout([]) == DEFAULT_FROZEN_SCREEN_TIMEOUT


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
    # two remedies that remain for it, not `handleSystemAlert`'s `sel.label`, which the scenario
    # need not use. BE-0406 removed `labels`, so it must not be offered as one of them either.
    message = str(exc.value)
    assert "interrupts systemAlert" in message
    assert "system_alerts.py" in message
    assert "`labels`" not in message
    assert "sel.label" not in message
    # The covered languages, like the step's own message reports.
    for language in covered_languages("notifications"):
        assert language in message


def test_resolve_rules_empty_list_stays_empty() -> None:
    assert _resolve_rules([], "en") == []


def test_resolve_rules_emits_one_rule_per_shape_of_a_multi_shape_prompt() -> None:
    # BE-0406: `savePassword` renders three ways — a web form, and the app's own fields before and
    # after iOS 26.5 moved the accepting button's text. One scenario rule has to cover all three, so
    # the resolution fans out rather than picking whichever shape came first.
    resolved = _resolve_rules([SystemAlertRule(prompt="savePassword", choice="deny")], "en")
    assert [sorted(r.identifying_labels) for r in resolved] == [
        ["Never for This Website", "Not Now", "Save Password"],
        ["Not Now", "Save Password"],
        ["Not Now", "Save"],
    ]
    # The refusing button is "Not Now" on every one of them; only the accepting button moves.
    assert {r.tap_label for r in resolved} == {"Not Now"}
    assert (
        _resolve_rules([SystemAlertRule(prompt="savePassword", choice="grant")], "en")[-1].tap_label
        == "Save"
    )


def test_resolve_rules_carries_the_prompts_surfaces_onto_each_rule() -> None:
    # The surface record is what later decides which paths may act: `savePassword` is raised into
    # the app's own process, so only the in-tree dismissal reaches it and the interruption monitor
    # never sees it; `notifications` is the mirror image.
    save = _resolve_rules([SystemAlertRule(prompt="savePassword", choice="deny")], "en")
    assert all(r.in_tree and not r.native for r in save)
    notif = _resolve_rules([SystemAlertRule(prompt="notifications", choice="grant")], "en")
    assert all(r.native and not r.in_tree for r in notif)


def test_resolve_rules_excludes_the_credit_card_sheet_from_the_in_app_save_shape() -> None:
    # iOS 26.5's in-app save sheet is "Save" and "Not Now", the same pair the credit-card update
    # sheet offers. Only "Never for This Card" tells them apart, so the shape carries it as an
    # exclusion — without which a `savePassword` rule would answer a card sheet nobody declared.
    resolved = _resolve_rules([SystemAlertRule(prompt="savePassword", choice="deny")], "en")
    in_app_26 = resolved[-1]
    assert in_app_26.excluded_labels == {"Never for This Card"}
    assert _match_tap_label([in_app_26], ["Save", "Not Now"]) == "Not Now"
    assert _match_tap_label([in_app_26], ["Save", "Never for This Card", "Not Now"]) is None
    # The other two shapes need none: their identifying labels are specific enough on their own.
    assert all(not r.excluded_labels for r in resolved[:-1])


# --- _alert_guard_factory: rules resolve and layer scenario-over-target


def test_alert_guard_factory_resolves_scenario_rules() -> None:
    s = _tap_scenario(
        "a",
        [
            {"systemAlert": "notifications", "choice": "grant"},
            {"systemAlert": "tracking", "choice": "deny"},
        ],
    )
    guard = _guard(s)
    assert [r.tap_label for r in guard.rules] == ["Allow", "Ask App Not to Track"]


def test_alert_guard_factory_scenario_rule_shadows_target_rule_for_the_same_prompt() -> None:
    eff = _eff(interrupts="[{ systemAlert: notifications, choice: deny }]")
    s = _tap_scenario("a", [{"systemAlert": "notifications", "choice": "grant"}])
    guard = _guard(s, eff)
    assert guard is not None
    # The scenario's entry replaces the target's choice for the same prompt (BE-0473), so the
    # target's answer is not even carried behind it.
    assert [r.tap_label for r in guard.rules] == ["Allow"]
    # Through `native_rules`, the list `probe_native` actually matches over (BE-0418).
    assert _match_tap_label(guard.native_rules, ["Allow", "Don’t Allow"]) == "Allow"


def test_alert_guard_factory_scenario_leave_drops_the_targets_answer() -> None:
    # `leave` replaces the target's answer for that prompt with none, and names only that prompt:
    # the target's answer for another prompt still applies.
    eff = _eff(
        interrupts="[{ systemAlert: notifications, choice: deny }, "
        "{ systemAlert: tracking, choice: deny }]"
    )
    s = _tap_scenario("a", [{"systemAlert": "notifications", "choice": "leave"}])
    guard = _guard(s, eff)
    assert [r.tap_label for r in guard.rules] == ["Ask App Not to Track"]
    assert _match_tap_label(guard.native_rules, ["Allow", "Don’t Allow"]) is None


def test_alert_guard_factory_target_leave_answers_nothing() -> None:
    eff = _eff(interrupts="[{ systemAlert: notifications, choice: leave }]")
    assert _guard(_tap_scenario("a"), eff).rules == []


def test_alert_guard_factory_target_rule_applies_when_scenario_has_none() -> None:
    eff = _eff(interrupts="[{ systemAlert: tracking, choice: deny }]")
    s = _tap_scenario("a")
    guard = _guard(s, eff)
    assert guard is not None
    assert [r.tap_label for r in guard.rules] == ["Ask App Not to Track"]


def test_alert_guard_factory_target_rule_answers_a_prompt_the_scenario_did_not_rule_on() -> None:
    # BE-0401 reversed BE-0382's all-or-nothing suppression: specificity, not the layer a
    # declaration came from, settles the conflict. A scenario ruling on `notifications` says nothing
    # about `tracking`, so the target's rule for it stays in effect — both layers concatenate,
    # innermost first.
    eff = _eff(interrupts="[{ systemAlert: tracking, choice: deny }]")
    s = _tap_scenario("a", [{"systemAlert": "notifications", "choice": "grant"}])
    guard = _guard(s, eff)
    assert guard is not None
    assert [r.tap_label for r in guard.rules] == ["Allow", "Ask App Not to Track"]


def test_alert_guard_factory_notices_a_target_rule_reaching_a_self_answering_scenario(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The composition above is correct but was *silent* under BE-0382 — a project-wide edit changing
    # a scenario that names no prompt. The notice removes the silence, and is keyed on the scenario
    # as well as the prompt, so a run of many scenarios warns once *per affected scenario* rather
    # than once for the first and silence for the rest.
    import logging

    from bajutsu.common import deprecations

    eff = _eff(interrupts="[{ systemAlert: tracking, choice: deny }]")
    own: list[dict[str, object]] = [{"systemAlert": "notifications", "choice": "grant"}]
    scenarios = [_tap_scenario("a", own), _tap_scenario("b", own)]
    for s in scenarios:
        deprecations._emitted.discard(f"interrupts.targetSystemAlert.{s.name}.tracking")
    factory = _alert_guard_factory(eff, enabled=None, flag_poll_interval=None)
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

    eff = _eff(interrupts="[{ systemAlert: tracking, choice: deny }]")
    s = _tap_scenario("a", [{"systemAlert": "tracking", "choice": "grant"}])
    deprecations._emitted.discard("interrupts.targetSystemAlert.a.tracking")
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        _guard(s, eff)
    assert not [r for r in caplog.records if "tracking" in r.message]


def test_alert_guard_factory_does_not_notice_when_only_the_target_declares_anything(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The notice is for a scenario that answers for itself. A scenario declaring nothing, under a
    # target that rules on the prompt, is not that case — reading the composed rule list instead of
    # the scenario's own would warn here wrongly.
    import logging

    from bajutsu.common import deprecations

    eff = _eff(interrupts="[{ systemAlert: tracking, choice: deny }]")
    s = _tap_scenario("a")
    deprecations._emitted.discard("interrupts.targetSystemAlert.a.tracking")
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        guard = _guard(s, eff)
    assert guard is not None and [r.tap_label for r in guard.rules] == ["Ask App Not to Track"]
    assert not [r for r in caplog.records if "tracking" in r.message]


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


def test_alert_guard_factory_does_not_notice_a_target_entry_that_leaves_its_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A target entry with `choice: leave` answers nothing, so it reaches no scenario to notice.
    import logging

    from bajutsu.common import deprecations

    eff = _eff(interrupts="[{ systemAlert: tracking, choice: leave }]")
    s = _tap_scenario("a", [{"systemAlert": "notifications", "choice": "grant"}])
    deprecations._emitted.discard("interrupts.targetSystemAlert.a.tracking")
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        assert [r.tap_label for r in _guard(s, eff).rules] == ["Allow"]
    assert not [r for r in caplog.records if "tracking" in r.message]
