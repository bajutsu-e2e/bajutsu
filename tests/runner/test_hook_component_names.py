"""Names a `hooks` component brings: per-target scopes and `as` aliases (BE-0512 unit 3).

A direct entry's name stays unique across the run. A name from a target's component is scoped to
that target, so two targets can share a component; a token resolves by the target of the step that
reads it, and an alias qualifies the names of one application.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from _runner import _eff

from bajutsu.common.config import Effective, HookCommand, HookPhases, load_config_file, resolve
from bajutsu.common.runner import hook_commands as hc
from bajutsu.common.runner import scenario_hooks as sh
from bajutsu.common.scenario import Scenario, load_expanded_scenarios
from bajutsu.common.scenario.models.hook_command import HookOrigin, env_name, split_hook_token


def _from(name: str | None, alias: str | None = None, cmd: str = "x") -> HookCommand:
    origin = HookOrigin(component="/c/user.hook.yaml", alias=alias)
    return HookCommand.model_validate({"name": name, "cmd": cmd, "_origin": origin})


def _effs(**phases: HookPhases) -> dict[str, Effective]:
    return {name: replace(_eff(), target=name, hooks=p) for name, p in phases.items()}


def _scenario(**kw: Any) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": [{"tap": {"id": "ok"}}], **kw})


def _typing(token: str, **kw: Any) -> Scenario:
    return _scenario(steps=[{"type": {"text": token, "into": {"id": "ok"}}}], **kw)


def _texts(s: Scenario) -> list[str]:
    return [st.type.text for st in s.steps if st.type is not None]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- the token and variable forms ---


@pytest.mark.parametrize(
    ("token", "parts"),
    [
        ("hooks.user.id", ("user", "id")),
        ("hooks.premium.user.id", ("premium.user", "id")),
        ("hooks.web:premium.user.id", ("web:premium.user", "id")),
        ("hooks.user", ("user", "")),
    ],
)
def test_a_token_splits_at_its_last_dot(token: str, parts: tuple[str, str]) -> None:
    assert split_hook_token(token) == parts


def test_a_variable_drops_the_scope_and_spells_the_alias_with_underscores() -> None:
    assert env_name("web:premium.user") == "premium__user"
    assert env_name("user") == "user"


def test_an_alias_qualifies_the_name_a_token_reads() -> None:
    assert _from("user", "premium").qualified_name == "premium.user"
    assert _from("user").qualified_name == "user"
    assert _from("user", "premium").label == "premium.user"


# --- where outputs are stored and who receives them ---


def test_a_target_components_outputs_are_stored_under_the_target() -> None:
    target = hc.HookContext(run_id="r", target="web")
    scenario_level = hc.HookContext(run_id="r", target="web", level="_scenario")
    assert hc.stored_name(_from("user"), target) == "web:user"
    assert hc.stored_name(_from("user", "premium"), target) == "web:premium.user"
    # A scenario's own component scopes to the scenario, which its names already are.
    assert hc.stored_name(_from("user"), scenario_level) == "user"
    assert hc.stored_name(HookCommand(name="user", cmd="x"), target) == "user"


def test_a_command_reads_its_own_targets_component_outputs_alone() -> None:
    outputs = hc.HookOutputs(
        values={"web:user": {"id": "w"}, "ios:user": {"id": "i"}, "server": {"port": "1"}}
    )
    assert outputs.env(["web"]) == {
        "BAJUTSU_HOOK__user__id": "w",
        "BAJUTSU_HOOK__server__port": "1",
    }
    assert outputs.bindings() == {
        "hooks.web:user.id": "w",
        "hooks.ios:user.id": "i",
        "hooks.server.port": "1",
    }


def test_an_applications_commands_also_read_its_names_locally() -> None:
    mine = HookOrigin(component="/c/user.hook.yaml", alias="premium")
    outputs = hc.HookOutputs(
        values={"web:premium.user": {"id": "p"}, "user": {"id": "outer"}},
        origins={"web:premium.user": mine},
    )
    # The local name wins over an outer entry of the same name, for this application's commands.
    assert outputs.env(["web"], mine) == {
        "BAJUTSU_HOOK__premium__user__id": "p",
        "BAJUTSU_HOOK__user__id": "p",
    }
    assert outputs.env(["web"])["BAJUTSU_HOOK__user__id"] == "outer"


def test_another_component_under_the_same_alias_stays_apart() -> None:
    a = HookOrigin(component="/c/a.hook.yaml", alias="x")
    b = HookOrigin(component="/c/b.hook.yaml", alias="x")
    outputs = hc.HookOutputs(
        values={"x.a": {"k": "A"}, "x.b": {"k": "fromB"}, "b": {"k": "direct"}},
        origins={"x.a": a, "x.b": b},
    )
    seen_by_a = outputs.env(None, a)
    assert seen_by_a["BAJUTSU_HOOK__a__k"] == "A"
    assert seen_by_a["BAJUTSU_HOOK__b__k"] == "direct"
    assert outputs.env(None, b)["BAJUTSU_HOOK__b__k"] == "fromB"


def test_an_aliased_teardown_reads_its_preparation_by_the_local_name(tmp_path: Path) -> None:
    seen = tmp_path / "seen.txt"
    write = tmp_path / "w.sh"
    write.write_text('#!/bin/sh\necho id=42 > "$BAJUTSU_OUTPUT"\n', encoding="utf-8")
    read = tmp_path / "r.sh"
    read.write_text(f'#!/bin/sh\necho "$BAJUTSU_HOOK__user__id" > {seen}\n', encoding="utf-8")
    write.chmod(0o755)
    read.chmod(0o755)
    runner = hc.HookRunner(raw_dir=hc.raw_root(tmp_path / "runs", "r"), base_dir=tmp_path)
    context = hc.HookContext(run_id="r", target="web", scenario_index=0, verdict="success")
    before = runner.run_list("beforeScenario", [_from("user", "premium", str(write))], context)
    assert before.outputs.values == {"web:premium.user": {"id": "42"}}
    runner.run_list(
        "afterScenario", [_from(None, "premium", str(read))], context, inherited=before.outputs
    )
    assert seen.read_text().strip() == "42"


# --- names across targets ---


def test_two_targets_may_share_a_component_name() -> None:
    phases = {
        "web": HookPhases(before_run=[_from("user")]),
        "ios": HookPhases(before_run=[_from("user")]),
    }
    assert hc.duplicate_hook_name(phases) is None


def test_a_component_name_may_not_shadow_a_direct_name_of_another_target() -> None:
    phases = {
        "web": HookPhases(before_run=[HookCommand(name="user", cmd="x")]),
        "ios": HookPhases(before_run=[_from("user")]),
    }
    assert "also declares directly" in (hc.duplicate_hook_name(phases) or "")


def test_direct_names_stay_unique_across_the_run() -> None:
    phases = {
        "web": HookPhases(before_run=[HookCommand(name="user", cmd="x")]),
        "ios": HookPhases(before_run=[HookCommand(name="user", cmd="y")]),
    }
    assert "both declare" in (hc.duplicate_hook_name(phases) or "")


# --- a scenario's view ---


def test_a_step_reads_the_value_of_its_own_target() -> None:
    effs = _effs(
        web=HookPhases(before_scenario=[_from("user")]),
        ios=HookPhases(before_scenario=[_from("user")]),
    )
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["web", "ios"],
            "steps": [
                {"type": {"text": "${hooks.user.id}", "into": {"id": "f"}}, "target": "web"},
                {"type": {"text": "${hooks.user.id}", "into": {"id": "f"}}, "target": "ios"},
            ],
        }
    )
    assert sh.scenario_hook_error(s, effs, "web") is None
    sh.scope_hook_tokens(s, effs, "web")
    assert _texts(s) == ["${hooks.web:user.id}", "${hooks.ios:user.id}"]


def test_a_step_cannot_read_a_component_name_of_another_target() -> None:
    effs = _effs(web=HookPhases(), ios=HookPhases(before_scenario=[_from("user")]))
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["web", "ios"],
            "steps": [{"type": {"text": "${hooks.user.id}", "into": {"id": "f"}}, "target": "web"}],
        }
    )
    assert "of target 'ios'" in (sh.scenario_hook_error(s, effs, "web") or "")


def test_a_scenarios_own_commands_cannot_meet_a_name_two_targets_hold() -> None:
    effs = _effs(
        web=HookPhases(before_scenario=[_from("user")]),
        ios=HookPhases(before_scenario=[_from("user")]),
    )
    s = _scenario(
        targets=["web", "ios"],
        steps=[{"tap": {"id": "ok"}, "target": "web"}],
        hooks={"afterScenario": [{"cmd": "./x.sh"}]},
    )
    assert "both apply a hooks component naming 'user'" in (
        sh.scenario_hook_error(s, effs, "web") or ""
    )


def test_an_alias_may_not_equal_a_name_the_scenario_sees() -> None:
    effs = _effs(
        web=HookPhases(
            before_scenario=[HookCommand(name="premium", cmd="x"), _from("user", "premium")]
        )
    )
    assert "alias 'premium'" in (sh.scenario_hook_error(_scenario(), effs, "web") or "")


def test_a_scenario_reads_an_aliased_name() -> None:
    effs = _effs(web=HookPhases(before_scenario=[_from("user"), _from("user", "premium")]))
    s = _typing("${hooks.premium.user.id}")
    assert sh.scenario_hook_error(s, effs, "web") is None
    sh.scope_hook_tokens(s, effs, "web")
    assert _texts(s) == ["${hooks.web:premium.user.id}"]


def test_a_direct_token_is_left_as_written() -> None:
    effs = _effs(web=HookPhases(before_run=[HookCommand(name="server", cmd="x")]))
    s = _typing("${hooks.server.port}")
    sh.scope_hook_tokens(s, effs, "web")
    assert _texts(s) == ["${hooks.server.port}"]


# --- loading ---

_USER = """
beforeScenario:
  - name: user
    cmd: ./create.sh
setup:
  - type: { text: "${hooks.user.id}", into: { id: login } }
"""


def _config(tmp_path: Path, uses: str) -> Path:
    _write(tmp_path / "hooks" / "user.hook.yaml", _USER)
    return _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  web:\n    platform: web\n    subject: { url: 'http://127.0.0.1:1/' }\n"
        f"    hooks:\n      use: {uses}\n",
    )


def test_an_aliased_application_reads_its_own_names_in_its_steps(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        "[{ hook: ./hooks/user.hook.yaml }, { hook: ./hooks/user.hook.yaml, as: premium }]",
    )
    eff = resolve(load_config_file(config), "web")
    assert [e.qualified_name for e in eff.hooks.before_scenario] == ["user", "premium.user"]
    # The target's setup reads under the target's scope, and the aliased copy under its alias.
    assert [s.type.text for s in eff.run_defaults.setup if s.type is not None] == [
        "${hooks.web:user.id}",
        "${hooks.web:premium.user.id}",
    ]


def test_the_same_names_twice_without_an_alias_ask_for_one(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "other.hook.yaml", _USER)
    config = _config(
        tmp_path, "[{ hook: ./hooks/user.hook.yaml }, { hook: ./hooks/other.hook.yaml }]"
    )
    with pytest.raises(ValueError, match=r"give one hooks\.use application an `as`"):
        load_config_file(config)


def test_a_scenario_and_its_target_apply_one_component_apart(tmp_path: Path) -> None:
    config = _config(tmp_path, "[{ hook: ./hooks/user.hook.yaml }]")
    effs = {"web": resolve(load_config_file(config), "web")}
    path = _write(
        tmp_path / "scenarios" / "s.yaml",
        """
- name: s
  hooks: { use: [{ hook: ../hooks/user.hook.yaml, as: premium }] }
  steps: [{ type: { text: "${hooks.premium.user.id}", into: { id: f } } }]
""",
    )
    [s] = load_expanded_scenarios(path, root=tmp_path)
    assert sh.scenario_hook_error(s, effs, "web") is None
    # A scenario's own component stores under the scenario's name, so its token stays as written.
    sh.scope_hook_tokens(s, effs, "web")
    assert _texts(s) == ["${hooks.premium.user.id}"]
    assert [st.type.text for st in s.setup if st.type is not None] == ["${hooks.premium.user.id}"]

    unaliased = _write(
        tmp_path / "scenarios" / "t.yaml",
        "- name: t\n  hooks: { use: [{ hook: ../hooks/user.hook.yaml }] }\n"
        "  steps: [{ tap: { id: ok } }]\n",
    )
    [t] = load_expanded_scenarios(unaliased, root=tmp_path)
    assert "also declared by target 'web'" in (sh.scenario_hook_error(t, effs, "web") or "")


def test_a_target_components_value_reaches_the_step_on_the_run_path(tmp_path: Path) -> None:
    from _runner import _fake_driver, _lease

    from bajutsu.common.runner.hook_session import start_session
    from bajutsu.common.runner.pipeline import run_all

    script = _write(tmp_path / "mk.sh", '#!/bin/sh\necho "id=c-1" > "$BAJUTSU_OUTPUT"\n')
    script.chmod(0o755)
    eff = replace(
        _eff(),
        hooks=HookPhases(before_scenario=[_from("user", "premium", str(script))]),
        base_dir=tmp_path,
    )
    s = _typing("${hooks.premium.user.id}")
    assert sh.scenario_hook_error(s, {eff.target: eff}, eff.target) is None
    sh.scope_hook_tokens(s, {eff.target: eff}, eff.target)
    session = start_session(
        {eff.target: eff},
        run_id="r1",
        runs_dir=tmp_path / "runs",
        base_dir=tmp_path,
        scenario_targets={eff.target},
    )
    driver = _fake_driver()
    [result] = run_all(eff, [s], lambda e, sc: replace(_lease(e, sc), driver=driver), hooks=session)
    assert result.ok, result.failure
    assert ("type", "c-1") in driver.actions  # type: ignore[attr-defined]


# --- review follow-ups ---


def test_a_scenario_level_context_reads_every_driven_target() -> None:
    from bajutsu.common.scenario.models.hook_command import SCENARIO_LEVEL

    level = hc.HookContext(run_id="r", target="web", level=SCENARIO_LEVEL, targets=("web", "ios"))
    assert level.output_targets == ("web", "ios")
    assert hc.HookContext(run_id="r", target="web", targets=("ios",)).output_targets == ("web",)


def test_an_origin_survives_a_dump_and_a_reload() -> None:
    entry = _from("user", "premium")
    again = HookCommand.model_validate(entry.model_dump(by_alias=True, exclude_unset=True))
    assert again.qualified_name == "premium.user"
    assert again.origin == entry.origin


def test_an_aliased_data_row_keeps_apart_from_its_own_entry_of_the_same_name(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _USER)
    path = _write(
        tmp_path / "scenarios" / "s.yaml",
        """
- name: s
  data: [{ x: a }, { x: b }]
  hooks:
    use: [{ hook: ../hooks/user.hook.yaml, as: premium }]
    beforeScenario: [{ name: user, cmd: ./own.sh }]
  steps: [{ tap: { id: ok } }]
""",
    )
    rows = load_expanded_scenarios(path, root=tmp_path)
    assert [[e.qualified_name for e in r.hooks.before_scenario] for r in rows if r.hooks] == [
        ["premium.user", "user"],
        ["premium.user", "user"],
    ]


def test_scoping_reaches_setup_cleanup_screens_and_expect() -> None:
    effs = _effs(web=HookPhases(before_scenario=[_from("user")]))
    token = "${hooks.user.id}"
    s = _scenario(
        setup=[{"type": {"text": token, "into": {"id": "ok"}}}],
        cleanup=[{"on": "always", "steps": [{"type": {"text": token, "into": {"id": "ok"}}}]}],
        interrupts=[
            {
                "condition": {"exists": {"id": "x"}},
                "steps": [{"type": {"text": token, "into": {"id": "ok"}}}],
            }
        ],
        expect=[{"exists": {"label": token}}],
    )
    sh.scope_hook_tokens(s, effs, "web")
    dumped = str(s.model_dump(by_alias=True))
    assert token not in dumped
    assert dumped.count("${hooks.web:user.id}") == 4


def test_a_nested_target_reads_under_its_own_target() -> None:
    effs = _effs(
        web=HookPhases(before_scenario=[_from("user")]),
        ios=HookPhases(before_scenario=[_from("user")]),
    )
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["web", "ios"],
            "steps": [{"tap": {"id": "ok"}, "target": "web"}],
            "expect": [{"exists": {"label": "${hooks.user.id}"}, "target": "ios"}],
        }
    )
    sh.scope_hook_tokens(s, effs, "web")
    assert "${hooks.ios:user.id}" in str(s.expect[0].model_dump(by_alias=True))


def test_an_aliased_step_keeps_an_outer_token_and_rewrites_a_spaced_one(tmp_path: Path) -> None:
    _write(
        tmp_path / "hooks" / "u.hook.yaml",
        """
beforeScenario: [{ name: user, cmd: ./c.sh }]
setup:
  - type: { text: "${ hooks.user.id }-${hooks.server.port}", into: { id: login } }
""",
    )
    config = _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  web:\n    platform: web\n    subject: { url: 'http://127.0.0.1:1/' }\n"
        "    hooks:\n      use: [{ hook: ./hooks/u.hook.yaml, as: premium }]\n"
        "      beforeRun: [{ name: server, cmd: ./s.sh }]\n",
    )
    [step] = resolve(load_config_file(config), "web").run_defaults.setup
    assert step.type is not None
    assert step.type.text == "${hooks.web:premium.user.id}-${hooks.server.port}"


def test_a_target_components_cleanup_reads_under_the_target(tmp_path: Path) -> None:
    _write(
        tmp_path / "hooks" / "u.hook.yaml",
        "beforeScenario: [{ name: user, cmd: ./c.sh }]\n"
        "cleanup: [{ on: always, steps: [{ type: { text: '${hooks.user.id}', into: { id: x } } }] }]\n",
    )
    config = _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  web:\n    platform: web\n    subject: { url: 'http://127.0.0.1:1/' }\n"
        "    hooks:\n      use: [{ hook: ./hooks/u.hook.yaml }]\n",
    )
    [rule] = resolve(load_config_file(config), "web").run_defaults.cleanup
    assert [s.type.text for s in rule.steps if s.type is not None] == ["${hooks.web:user.id}"]


def test_an_alias_may_not_equal_the_scenarios_own_entry_name() -> None:
    effs = _effs(web=HookPhases(before_scenario=[_from("user", "premium")]))
    s = _scenario(hooks={"beforeScenario": [{"name": "premium", "cmd": "./x.sh"}]})
    assert "alias 'premium'" in (sh.scenario_hook_error(s, effs, "web") or "")


def test_a_missing_key_names_the_scoped_token() -> None:
    outputs = hc.HookOutputs(values={"web:user": {"other": "1"}})
    why = hc.missing_hook_key({"hooks.web:user.id"}, outputs)
    assert why is not None and "never wrote 'id'" in why


def test_a_condition_read_through_a_targets_scoped_setup_is_still_checked() -> None:
    gated = HookCommand.model_validate(
        {
            "name": "user",
            "cmd": "x",
            "when": {"tags": ["paid"]},
            "_origin": {"component": "/c/u.hook.yaml"},
        }
    )
    phases = {"web": HookPhases(before_scenario=[gated])}
    why = hc.unmet_hook_condition({"hooks.web:user.id"}, phases, ["web"], [])
    assert why is not None and "runs only when" in why


def test_the_run_load_scopes_a_scenarios_tokens() -> None:
    from bajutsu.run.cli import _reject_unknown_hook_tokens

    effs = _effs(web=HookPhases(before_scenario=[_from("user")]))
    s = _typing("${hooks.user.id}")
    _reject_unknown_hook_tokens(effs, [s], "web")
    assert _texts(s) == ["${hooks.web:user.id}"]


def _two_targets() -> dict[str, Effective]:
    return _effs(
        web=HookPhases(before_scenario=[_from("user")]),
        ios=HookPhases(before_scenario=[_from("user")]),
    )


def test_an_interrupt_reads_under_the_target_it_watches() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["web", "ios"],
            "steps": [{"tap": {"id": "ok"}, "target": "web"}],
            "interrupts": [
                {
                    "target": "ios",
                    "condition": {"exists": {"label": "${hooks.user.id}"}},
                    "steps": [{"type": {"text": "${hooks.user.id}", "into": {"id": "ok"}}}],
                }
            ],
        }
    )
    sh.scope_hook_tokens(s, _two_targets(), "web")
    dumped = str(s.screens[0].model_dump(by_alias=True))
    assert dumped.count("${hooks.ios:user.id}") == 2


def test_an_untargeted_interrupt_of_a_multi_target_scenario_cannot_read_a_component() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["web", "ios"],
            "steps": [{"tap": {"id": "ok"}, "target": "web"}],
            "interrupts": [{"condition": {"exists": {"label": "${hooks.user.id}"}}, "steps": []}],
        }
    )
    assert "no single target reads it" in (sh.scenario_hook_error(s, _two_targets(), "web") or "")


def test_expect_reads_under_the_primary_the_steps_end_on() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["web", "ios"],
            "primaryTarget": "web",
            "steps": [
                {"tap": {"id": "ok"}},
                {"setPrimaryTarget": {"target": "ios"}},
                {"tap": {"id": "ok"}},
            ],
            "expect": [{"exists": {"label": "${hooks.user.id}"}}],
        }
    )
    sh.scope_hook_tokens(s, _two_targets(), "web")
    assert "${hooks.ios:user.id}" in str(s.expect[0].model_dump(by_alias=True))


def test_scoping_keeps_the_routing_of_nested_steps() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["ios", "web"],
            "primaryTarget": "ios",
            "steps": [
                {
                    "if": {
                        "condition": {"exists": {"id": "x"}},
                        "then": [{"type": {"text": "${hooks.user.id}", "into": {"id": "ok"}}}],
                    }
                }
            ],
        }
    )
    [wrapper] = s.steps
    assert wrapper.if_ is not None
    nested = wrapper.if_.then[0]
    before = nested.resolved_target
    sh.scope_hook_tokens(s, _two_targets(), "web")
    assert s.steps[0].if_ is not None and s.steps[0].if_.then[0] is nested
    assert nested.resolved_target == before == "ios"
    assert nested.type is not None and nested.type.text == "${hooks.ios:user.id}"


def test_a_condition_is_checked_against_the_reading_targets_own_entry() -> None:
    def gated(tag: str) -> HookCommand:
        return HookCommand.model_validate(
            {"name": "user", "cmd": "x", "when": {"tags": [tag]}, "_origin": {"component": "/c"}}
        )

    phases = {
        "web": HookPhases(before_scenario=[gated("a")]),
        "ios": HookPhases(before_scenario=[gated("b")]),
    }
    assert hc.unmet_hook_condition({"hooks.ios:user.id"}, phases, ["web", "ios"], ["b"]) is None
    assert hc.unmet_hook_condition({"hooks.web:user.id"}, phases, ["web", "ios"], ["b"]) is not None


def test_an_audit_repeat_scopes_the_tokens_it_runs() -> None:
    from bajutsu.analysis.cli.audit import _refuse_bad_hooks

    effs = _effs(web=HookPhases(before_scenario=[_from("user")]))
    s = _typing("${hooks.user.id}")
    _refuse_bad_hooks([s], effs["web"])
    assert _texts(s) == ["${hooks.web:user.id}"]


def test_a_targets_own_interrupts_read_under_the_target_and_the_config_keeps_its_text(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "hooks" / "u.hook.yaml", "beforeScenario: [{ name: user, cmd: ./c.sh }]\n")
    config = _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  web:\n    platform: web\n    subject: { url: 'http://127.0.0.1:1/' }\n"
        "    hooks:\n      use: [{ hook: ./hooks/u.hook.yaml }]\n"
        "      setup: [{ type: { text: '${hooks.user.id}', into: { id: x } } }]\n"
        "      interrupts:\n"
        "        - condition: { exists: { label: '${hooks.user.id}' } }\n"
        "          steps: [{ type: { text: '${hooks.user.id}', into: { id: x } } }]\n",
    )
    loaded = load_config_file(config)
    eff = resolve(loaded, "web")
    assert (
        str(eff.run_defaults.interrupts[0].model_dump(by_alias=True)).count("${hooks.web:user.id}")
        == 2
    )
    # The loaded config is left as written, so resolving it again scopes from the same text.
    [own] = loaded.targets["web"].hooks.setup
    assert own.type is not None and own.type.text == "${hooks.user.id}"
    assert resolve(loaded, "web").run_defaults.setup[0].type.text == "${hooks.web:user.id}"  # type: ignore[union-attr]


def test_the_checks_still_pass_on_a_scenario_already_scoped() -> None:
    effs = _effs(web=HookPhases(before_scenario=[_from("user")]))
    s = _typing("${hooks.user.id}")
    sh.scope_hook_tokens(s, effs, "web")
    assert sh.scenario_hook_error(s, effs, "web") is None


@pytest.mark.parametrize("token", ["${hooks.zzz:user.id}", "${hooks.ios:user.id}"])
def test_a_hand_written_scope_stops_the_load(token: str) -> None:
    effs = _effs(web=HookPhases(before_scenario=[_from("user")]))
    assert "names a target scope" in (sh.scenario_hook_error(_typing(token), effs, "web") or "")


def test_an_origin_alias_meets_the_name_rule() -> None:
    with pytest.raises(ValueError, match=r"_origin\.alias"):
        HookOrigin(component="/c", alias="x:y")


def test_a_hand_written_scope_stops_the_load_with_no_component_in_reach() -> None:
    effs = _effs(web=HookPhases(before_scenario=[HookCommand(name="user", cmd="x")]))
    why = sh.scenario_hook_error(_typing("${hooks.web:user.id}"), effs, "web")
    assert "names a target scope" in (why or "")


def test_a_hand_written_scope_in_a_targets_own_steps_stops_the_resolve(tmp_path: Path) -> None:
    config = _write(
        tmp_path / "bajutsu.config.yaml",
        "targets:\n  web:\n    platform: web\n    subject: { url: 'http://127.0.0.1:1/' }\n"
        "    hooks:\n      setup: [{ type: { text: '${hooks.web:user.id}', into: { id: x } } }]\n",
    )
    with pytest.raises(ValueError, match="names a target scope"):
        resolve(load_config_file(config), "web")


def test_a_condition_check_reads_the_when_of_the_target_that_reads_it(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from bajutsu.cli._shared import _reject_unmet_hook_conditions

    _write(
        tmp_path / "hooks" / "paid.hook.yaml",
        "beforeScenario: [{ name: user, cmd: ./c.sh, when: { tags: [paid] } }]\n",
    )
    _write(tmp_path / "hooks" / "free.hook.yaml", "beforeScenario: [{ name: user, cmd: ./c.sh }]\n")
    url = "subject: { url: 'http://127.0.0.1:1/' }"
    config = _write(
        tmp_path / "bajutsu.config.yaml",
        f"targets:\n  web:\n    platform: web\n    {url}\n"
        "    hooks: { use: [{ hook: ./hooks/paid.hook.yaml }] }\n"
        f"  ios:\n    platform: web\n    {url}\n"
        "    hooks: { use: [{ hook: ./hooks/free.hook.yaml }] }\n",
    )
    loaded = SimpleNamespace(config=load_config_file(config))
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["web", "ios"],
            "steps": [
                {"tap": {"id": "ok"}, "target": "web"},
                {"type": {"text": "${hooks.user.id}", "into": {"id": "ok"}}, "target": "ios"},
            ],
        }
    )
    # The token is read on `ios`, whose entry has no condition; web's `paid` gate does not apply.
    _reject_unmet_hook_conditions(loaded, [s], "web", selected=[s])  # type: ignore[arg-type]


def test_a_condition_check_leaves_an_unscopable_token_to_the_token_check(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from bajutsu.cli._shared import _reject_unmet_hook_conditions

    _write(tmp_path / "hooks" / "u.hook.yaml", "beforeScenario: [{ name: user, cmd: ./c.sh }]\n")
    url = "subject: { url: 'http://127.0.0.1:1/' }"
    config = _write(
        tmp_path / "bajutsu.config.yaml",
        f"targets:\n  web:\n    platform: web\n    {url}\n"
        f"  ios:\n    platform: web\n    {url}\n"
        "    hooks: { use: [{ hook: ./hooks/u.hook.yaml }] }\n",
    )
    loaded = SimpleNamespace(config=load_config_file(config))
    # Read on `web`, which holds no such component name: scoping refuses it, and this check passes
    # it through rather than raising, since `scenario_hook_error` reports it with its own message.
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["web", "ios"],
            "steps": [
                {"type": {"text": "${hooks.user.id}", "into": {"id": "ok"}}, "target": "web"}
            ],
        }
    )
    _reject_unmet_hook_conditions(loaded, [s], "web", selected=[s])  # type: ignore[arg-type]
