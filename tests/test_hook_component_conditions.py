"""A `when` on a `hooks.use` application, and `inheritSetup: false` (BE-0512 unit 4).

The application's condition joins every command and step the component adds: a command and a
cleanup rule hold it beside their own, and the setup steps become conditional blocks (BE-0513).
These tests pin what the loaders hand the run, what the fold keeps per scenario, and the loads that
must stop.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from bajutsu.common.config import HookPhases, load_config_file, resolve
from bajutsu.common.runner import hook_commands as hc
from bajutsu.common.runner.conditions import ConditionContext, inherited_cleanup
from bajutsu.common.runner.pipeline import with_lifecycle_phases
from bajutsu.common.scenario import CleanupRule, Scenario, Step, load_expanded_scenarios
from bajutsu.common.scenario.models.hook_command import HookWhen, JudgedTarget, WhenFacts

_PREMIUM = """
beforeScenario:
  - name: user
    cmd: ./create-user.sh
  - cmd: ./warm-cache.sh
    when: { platforms: [web] }
setup:
  - tap: { id: login }
  - tap: { id: plan }
  - when: { platforms: [ios] }
    steps: [{ tap: { id: ios.only } }]
  - tap: { id: home }
cleanup:
  - on: always
    steps: [{ tap: { id: logout } }]
afterScenario:
  - cmd: ./delete-user.sh
"""

_PLATFORM = "platform: web\n    subject: { url: 'http://127.0.0.1:1/' }"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _config(tmp_path: Path, target_body: str) -> Path:
    return _write(
        tmp_path / "bajutsu.config.yaml", f"targets:\n  web:\n    {_PLATFORM}\n{target_body}"
    )


def _premium_config(tmp_path: Path, when: str = "{ tags: [premium] }") -> Path:
    _write(tmp_path / "hooks" / "premium.hook.yaml", _PREMIUM)
    return _config(
        tmp_path,
        f"""    hooks:
      use: [{{ hook: ./hooks/premium.hook.yaml, when: {when} }}]
      beforeScenario: [{{ cmd: ./direct.sh }}]
      setup: [{{ tap: {{ id: direct.setup }} }}]
      cleanup: [{{ on: always, steps: [{{ tap: {{ id: direct.cleanup }} }}] }}]
""",
    )


def _ids(steps: list[Step]) -> list[str]:
    return [str(s.tap.id) if s.tap is not None else "" for s in steps]


def _when(**fields: object) -> HookWhen:
    return HookWhen.model_validate(fields)


_WEB = JudgedTarget("web", "web", "web")


# --- the joined condition ---


def test_a_joined_condition_holds_only_where_both_hold() -> None:
    joined = _when(tags=["a"]).with_application(_when(tags=["b"]))
    # Two `tags` lists read as "both", which no single condition can spell.
    assert joined.holds(WhenFacts(tags=[("a", "b")], targets=[_WEB]))
    assert not joined.holds(WhenFacts(tags=[("a",)], targets=[_WEB]))
    assert not joined.holds(WhenFacts(tags=[("b",)], targets=[_WEB]))
    assert joined.matches(["a", "b"])
    assert not joined.matches(["a"])
    assert str(joined) == "tags: [a] and tags: [b]"


def test_a_joined_condition_reads_what_each_part_reads() -> None:
    joined = _when(platforms=["web"]).with_application(_when(env={"CI": "1"}))
    assert joined.reads_target and not joined.reads_tags and joined.needs_run
    assert _when(tags=["a"]).with_application(_when(routes=["web"])).needs_run


def test_an_unknown_environment_drops_only_the_env_parts() -> None:
    unknown = ConditionContext({"web": _WEB}, None)
    joined = _when(env={"CI": "1"}).with_application(_when(tags=["premium"]))
    assert unknown.holds(joined, ["web"], [("premium",)])
    assert not unknown.holds(joined, ["web"], [("free",)])
    assert unknown.holds(_when(env={"CI": "1"}).with_application(_when(env={"X": None})), [], [])
    known = ConditionContext({"web": _WEB}, {})
    assert not known.holds(joined, ["web"], [("premium",)])


def test_a_joined_condition_survives_a_dump_and_reload() -> None:
    joined = _when(tags=["a"]).with_application(_when(platforms=["web"]))
    again = HookWhen.model_validate(joined.model_dump(by_alias=True, exclude_unset=True))
    assert again == joined
    assert "_also" not in HookWhen.model_json_schema().get("properties", {})


# --- what a target's application expands to ---


def test_an_applications_when_joins_every_command(tmp_path: Path) -> None:
    eff = resolve(load_config_file(_premium_config(tmp_path)), "web")
    user, warm, direct = eff.hooks.before_scenario
    assert user.when == _when(tags=["premium"])
    assert warm.when is not None and str(warm.when) == "platforms: [web] and tags: [premium]"
    assert direct.when is None
    assert [str(e.when) for e in eff.hooks.after_scenario] == ["tags: [premium]"]


def test_an_applications_setup_becomes_conditional_blocks(tmp_path: Path) -> None:
    eff = resolve(load_config_file(_premium_config(tmp_path)), "web")
    first, own_block, last, direct = eff.run_defaults.setup
    # Each run of plain steps is one block; the component's own block keeps its steps and joins
    # the application's condition, so `setup` keeps one layer of blocks.
    assert first.when == _when(tags=["premium"]) and _ids(first.steps or []) == ["login", "plan"]
    assert str(own_block.when) == "platforms: [ios] and tags: [premium]"
    assert _ids(own_block.steps or []) == ["ios.only"]
    assert last.when == _when(tags=["premium"]) and _ids(last.steps or []) == ["home"]
    assert direct.when is None and _ids([direct]) == ["direct.setup"]
    direct_rule, component_rule = eff.run_defaults.cleanup
    assert direct_rule.when is None
    assert component_rule.when == _when(tags=["premium"])


@pytest.mark.parametrize(("tags", "kept"), [(["premium"], True), (["free"], False)])
def test_the_fold_keeps_the_component_only_where_its_when_holds(
    tmp_path: Path, tags: list[str], kept: bool
) -> None:
    eff = resolve(load_config_file(_premium_config(tmp_path)), "web")
    scenario = Scenario.model_validate({"name": "s", "tags": tags, "steps": [{"tap": {"id": "x"}}]})
    [merged] = with_lifecycle_phases(eff, [scenario])
    component = ["login", "plan", "home"] if kept else []
    assert _ids(merged.setup) == [*component, "direct.setup"]
    cleanup = [_ids(r.steps) for r in merged.cleanup]
    assert cleanup == ([["direct.cleanup"], ["logout"]] if kept else [["direct.cleanup"]])


def test_a_condition_the_application_can_never_meet_on_its_target_fails(tmp_path: Path) -> None:
    config = _premium_config(tmp_path, "{ targets: [admin] }")
    with pytest.raises(ValidationError, match=r"hooks\.use\[0\]\.when: `targets` leaves out 'web'"):
        load_config_file(config)


def test_an_application_naming_an_undeclared_target_fails(tmp_path: Path) -> None:
    config = _premium_config(tmp_path, "{ targets: [web, nope] }")
    with pytest.raises(ValidationError, match="names target\\(s\\) 'nope'"):
        load_config_file(config)


def test_a_null_when_on_an_application_fails(tmp_path: Path) -> None:
    config = _premium_config(tmp_path, "null")
    with pytest.raises(ValidationError, match="`when` cannot be null"):
        load_config_file(config)


def test_a_run_level_application_cannot_exclude_without_tags(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "mock.hook.yaml", "beforeRun: [{ cmd: ./mock.sh }]\n")
    config = _config(
        tmp_path,
        "    hooks:\n      use: [{ hook: ./hooks/mock.hook.yaml, when: { exclude: [offline] } }]\n",
    )
    with pytest.raises(ValidationError, match="names `exclude` without `tags`"):
        load_config_file(config)


def test_a_run_level_application_with_tags_loads(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "mock.hook.yaml", "beforeRun: [{ cmd: ./mock.sh }]\n")
    config = _config(
        tmp_path,
        "    hooks:\n      use: [{ hook: ./hooks/mock.hook.yaml, when: { tags: [api] } }]\n",
    )
    [entry] = resolve(load_config_file(config), "web").hooks.before_run
    assert entry.when == _when(tags=["api"])


# --- reading an output the application's condition gates ---


def test_a_reader_the_applications_when_leaves_out_is_refused(tmp_path: Path) -> None:
    eff = resolve(load_config_file(_premium_config(tmp_path)), "web")
    phases = {"web": HookPhases(before_scenario=eff.hooks.before_scenario)}
    why = hc.unmet_hook_condition({"hooks.user.id"}, phases, ["web"], ["free"])
    assert why is not None and "tags: [premium]" in why
    assert hc.unmet_hook_condition({"hooks.user.id"}, phases, ["web"], ["premium"]) is None


# --- a scenario's own application ---


def test_a_scenarios_application_gates_its_own_parts(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _PREMIUM.replace("platforms: [ios]", "tags: [x]"))
    path = _write(
        tmp_path / "scenarios" / "s.yaml",
        """
- name: upgrade
  tags: [free]
  hooks:
    use: [{ hook: ../hooks/user.hook.yaml, when: { tags: [premium] } }]
  steps: [{ tap: { id: x } }]
""",
    )
    [s] = load_expanded_scenarios(path, root=tmp_path)
    assert s.hooks is not None
    assert [str(e.when) for e in s.hooks.before_scenario] == [
        "tags: [premium]",
        "platforms: [web] and tags: [premium]",
    ]
    assert [str(b.when) for b in s.setup] == [
        "tags: [premium]",
        "tags: [x] and tags: [premium]",
        "tags: [premium]",
    ]
    eff = resolve(load_config_file(_config(tmp_path, "")), "web")
    [merged] = with_lifecycle_phases(eff, [s])
    # The scenario carries `free`, so nothing the component adds folds in.
    assert merged.setup == [] and merged.cleanup == []


# --- `preconditions.inheritSetup: false` ---


def _rule(prepares: bool | None) -> CleanupRule:
    data: dict[str, object] = {"on": "always", "steps": [{"tap": {"id": "z"}}]}
    if prepares is not None:
        data["_origin"] = {"component": "/c.hook.yaml", "prepares": prepares}
    return CleanupRule.model_validate(data)


def test_skipping_the_setup_skips_only_a_teardown_whose_preparation_it_skips() -> None:
    direct, paired, alone = _rule(None), _rule(True), _rule(False)
    assert inherited_cleanup([direct, paired, alone], True) == [direct, paired, alone]
    assert inherited_cleanup([direct, paired, alone], False) == [direct, alone]


def test_a_scenario_without_the_targets_setup_skips_the_components_paired_cleanup(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _PREMIUM)
    _write(
        tmp_path / "hooks" / "sweep.hook.yaml",
        "cleanup: [{ on: always, steps: [{ tap: { id: sweep } }] }]\n",
    )
    config = _config(
        tmp_path,
        """    hooks:
      use: [{ hook: ./hooks/user.hook.yaml }, { hook: ./hooks/sweep.hook.yaml }]
      cleanup: [{ on: always, steps: [{ tap: { id: direct.cleanup } }] }]
""",
    )
    eff = resolve(load_config_file(config), "web")
    scenario = Scenario.model_validate(
        {
            "name": "s",
            "preconditions": {"inheritSetup": False},
            "steps": [{"tap": {"id": "x"}}],
        }
    )
    [merged] = with_lifecycle_phases(eff, [scenario])
    assert merged.setup == []
    # The user component prepares, so its sign-out goes with its sign-in; the sweep prepares
    # nothing, so it runs as the direct rule it replaced would.
    assert [_ids(r.steps) for r in merged.cleanup] == [["direct.cleanup"], ["sweep"]]
