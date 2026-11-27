"""`hooks` components: one preparation and its teardown applied with `hooks.use` (BE-0512).

The component expands while the files load, so these tests pin what the loaders hand the run: the
command entries and steps in their order, the substituted values, the working directory, and the
loads that must stop.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from bajutsu.common.config import load_config_file, resolve
from bajutsu.common.runner import hook_commands as hc
from bajutsu.common.scenario import Step, load_expanded_scenarios
from bajutsu.common.scenario.models.hook_command import HookCommand

_SEEDED_USER = """
params: [plan]
beforeScenario:
  - name: user
    cmd: "./create-user.sh --plan ${params.plan}"
setup:
  - tap: { id: "login.${params.plan}" }
cleanup:
  - on: always
    steps:
      - tap: { id: session.logout }
afterScenario:
  - cmd: "./delete-user.sh"
"""

_PLATFORM = "platform: web\n    subject: { url: 'http://127.0.0.1:1/' }"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _config(tmp_path: Path, target_body: str, extra: str = "") -> Path:
    return _write(
        tmp_path / "bajutsu.config.yaml",
        f"{extra}targets:\n  web:\n    {_PLATFORM}\n{target_body}",
    )


def _cmds(entries: list[HookCommand]) -> list[str | None]:
    return [e.cmd for e in entries]


def _ids(steps: Sequence[Step]) -> list[str]:
    return [str(s.tap.id) if s.tap is not None else "" for s in steps]


# --- applying a component to a target ---


def test_a_target_component_wraps_the_direct_entries(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    config = _config(
        tmp_path,
        """    hooks:
      use:
        - hook: ./hooks/user.hook.yaml
          with: { plan: free }
      beforeScenario: [{ cmd: ./direct-before.sh }]
      setup: [{ tap: { id: direct.setup } }]
      cleanup: [{ on: always, steps: [{ tap: { id: direct.cleanup } }] }]
      afterScenario: [{ cmd: ./direct-after.sh }]
""",
    )
    eff = resolve(load_config_file(config), "web")
    # Preparation: the component first, then the direct entry; teardown: the reverse.
    assert _cmds(eff.hooks.before_scenario) == [
        "./create-user.sh --plan free",
        "./direct-before.sh",
    ]
    assert _cmds(eff.hooks.after_scenario) == ["./direct-after.sh", "./delete-user.sh"]
    assert _ids(eff.run_defaults.setup) == ["login.free", "direct.setup"]
    assert [_ids(r.steps) for r in eff.run_defaults.cleanup] == [
        ["direct.cleanup"],
        ["session.logout"],
    ]


def test_teardown_runs_in_the_reverse_order_of_use(tmp_path: Path) -> None:
    for name in ("a", "b"):
        _write(
            tmp_path / "hooks" / f"{name}.hook.yaml",
            f"beforeRun: [{{ cmd: ./{name}-up.sh }}]\nafterRun: [{{ cmd: ./{name}-down.sh }}]\n",
        )
    config = _config(
        tmp_path,
        "    hooks:\n      use: [{ hook: ./hooks/a.hook.yaml }, { hook: ./hooks/b.hook.yaml }]\n",
    )
    hooks = resolve(load_config_file(config), "web").hooks
    assert _cmds(hooks.before_run) == ["./a-up.sh", "./b-up.sh"]
    assert _cmds(hooks.after_run) == ["./b-down.sh", "./a-down.sh"]


def test_a_value_with_a_space_stays_one_argument(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    config = _config(
        tmp_path,
        """    hooks:
      use: [{ hook: ./hooks/user.hook.yaml, with: { plan: "pro 'plus'" } }]
""",
    )
    entry = resolve(load_config_file(config), "web").hooks.before_scenario[0]
    assert entry.cmd is not None
    assert shlex.split(entry.cmd) == ["./create-user.sh", "--plan", "pro 'plus'"]


def test_env_values_take_parameters_too(tmp_path: Path) -> None:
    _write(
        tmp_path / "hooks" / "env.hook.yaml",
        "params: [plan]\nbeforeScenario: [{ cmd: ./x.sh, env: { PLAN: '${params.plan}' } }]\n",
    )
    config = _config(
        tmp_path, "    hooks:\n      use: [{ hook: ./hooks/env.hook.yaml, with: { plan: p } }]\n"
    )
    assert resolve(load_config_file(config), "web").hooks.before_scenario[0].env == {"PLAN": "p"}


def test_a_command_runs_in_the_component_directory(tmp_path: Path) -> None:
    _write(
        tmp_path / "hooks" / "cwd.hook.yaml",
        "beforeRun: [{ cmd: ./a.sh }, { cmd: ./b.sh, cwd: sub }, { cmd: ./c.sh, cwd: /abs }]\n",
    )
    config = _config(tmp_path, "    hooks:\n      use: [{ hook: ./hooks/cwd.hook.yaml }]\n")
    here = (tmp_path / "hooks").resolve()
    entries = resolve(load_config_file(config), "web").hooks.before_run
    assert [e.cwd for e in entries] == [str(here), str(here / "sub"), "/abs"]


def test_the_runner_starts_the_command_in_the_component_directory(tmp_path: Path) -> None:
    seen = tmp_path / "pwd.txt"
    script = _write(tmp_path / "hooks" / "pwd.sh", f"#!/bin/sh\npwd > {seen}\n")
    script.chmod(0o755)
    _write(tmp_path / "hooks" / "pwd.hook.yaml", "beforeRun: [{ cmd: ./pwd.sh }]\n")
    config = _config(tmp_path, "    hooks:\n      use: [{ hook: ./hooks/pwd.hook.yaml }]\n")
    entries = resolve(load_config_file(config), "web").hooks.before_run
    runner = hc.HookRunner(raw_dir=hc.raw_root(tmp_path / "runs", "r"), base_dir=tmp_path)
    runner.run_list(
        "beforeRun", entries, hc.HookContext(run_id="r", target="web", scenario_index=None)
    )
    assert seen.read_text().strip() == str((tmp_path / "hooks").resolve())


def test_a_step_component_resolves_from_the_hooks_component_directory(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "steps" / "sign-in.yaml", "steps: [{ tap: { id: signed.in } }]\n")
    _write(
        tmp_path / "hooks" / "user.hook.yaml",
        "setup: [{ use: { component: ./steps/sign-in.yaml } }]\n",
    )
    config = _config(tmp_path, "    hooks:\n      use: [{ hook: ./hooks/user.hook.yaml }]\n")
    assert _ids(resolve(load_config_file(config), "web").run_defaults.setup) == ["signed.in"]


# --- the loads that stop ---


@pytest.mark.parametrize(
    ("with_", "message"),
    [
        ("{}", r"missing required params: \['plan'\]"),
        ("{ plan: a, extra: b }", r"unknown params: \['extra'\]"),
    ],
)
def test_a_missing_or_unknown_parameter_stops_the_load(
    tmp_path: Path, with_: str, message: str
) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    config = _config(
        tmp_path, f"    hooks:\n      use: [{{ hook: ./hooks/user.hook.yaml, with: {with_} }}]\n"
    )
    with pytest.raises(ValidationError, match=message):
        load_config_file(config)


def test_an_undeclared_parameter_stops_the_load(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "x.hook.yaml", "beforeRun: [{ cmd: './x.sh ${params.who}' }]\n")
    config = _config(tmp_path, "    hooks:\n      use: [{ hook: ./hooks/x.hook.yaml }]\n")
    with pytest.raises(ValidationError, match=r"undeclared params: \['params.who'\]"):
        load_config_file(config)


def test_a_component_outside_the_suite_root_stops_the_load(tmp_path: Path) -> None:
    _write(tmp_path / "outside.hook.yaml", "beforeRun: [{ cmd: ./x.sh }]\n")
    config = _config(
        tmp_path / "suite", "    hooks:\n      use: [{ hook: ../outside.hook.yaml }]\n"
    )
    with pytest.raises(ValidationError, match="outside the suite root"):
        load_config_file(config)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("{}", "needs an entry in one of"),
        ("use: []\nbeforeRun: [{ cmd: x }]", "cannot hold `use`"),
        ("interrupts: []\nbeforeRun: [{ cmd: x }]", "cannot hold `interrupts`"),
        ("sandbox: { dockerImage: x }\nbeforeRun: [{ cmd: x }]", "cannot hold `sandbox`"),
        ("setup: [{ setMocks: [] }]", "cannot use setMocks"),
        ("beforeScenario: [{ cmd: x, on: success }]", "cannot set `on`"),
        ("beforeRun: [{ name: a, cmd: x }]\nafterRun: [{ name: a, cmd: y }]", "named 'a'"),
    ],
)
def test_an_invalid_component_stops_the_load(tmp_path: Path, body: str, message: str) -> None:
    _write(tmp_path / "hooks" / "bad.hook.yaml", body + "\n")
    config = _config(tmp_path, "    hooks:\n      use: [{ hook: ./hooks/bad.hook.yaml }]\n")
    with pytest.raises(ValidationError, match=message):
        load_config_file(config)


def test_the_same_application_twice_needs_an_alias(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "x.hook.yaml", "beforeRun: [{ cmd: ./x.sh }]\n")
    twice = "    hooks:\n      use: [{ hook: ./hooks/x.hook.yaml }, { hook: hooks/x.hook.yaml }]\n"
    with pytest.raises(ValidationError, match="applied twice"):
        load_config_file(_config(tmp_path, twice))
    aliased = (
        "    hooks:\n      use: [{ hook: ./hooks/x.hook.yaml }, "
        "{ hook: ./hooks/x.hook.yaml, as: again }]\n"
    )
    hooks = resolve(load_config_file(_config(tmp_path, aliased)), "web").hooks
    assert _cmds(hooks.before_run) == ["./x.sh", "./x.sh"]


def test_a_name_a_component_brings_meets_the_direct_names(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    config = _config(
        tmp_path,
        """    hooks:
      use: [{ hook: ./hooks/user.hook.yaml, with: { plan: free } }]
      afterRun: [{ name: user, cmd: ./x.sh }]
""",
    )
    with pytest.raises(ValidationError, match="two entries are named 'user'"):
        load_config_file(config)


def test_a_when_on_an_application_is_refused_until_it_has_a_meaning(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "x.hook.yaml", "beforeRun: [{ cmd: ./x.sh }]\n")
    config = _config(
        tmp_path, "    hooks:\n      use: [{ hook: ./hooks/x.hook.yaml, when: { tags: [a] } }]\n"
    )
    with pytest.raises(ValidationError, match="cannot set `when` yet"):
        load_config_file(config)


# --- layers ---


def test_use_lists_join_across_defaults_templates_and_the_target(tmp_path: Path) -> None:
    for name in ("team", "tmpl", "own"):
        _write(tmp_path / "hooks" / f"{name}.hook.yaml", f"beforeRun: [{{ cmd: ./{name}.sh }}]\n")
    _write(
        tmp_path / "templates" / "file.yaml",
        "hooks:\n  use: [{ hook: ../hooks/tmpl.hook.yaml }]\n",
    )
    config = _config(
        tmp_path,
        """    extends: ./templates/file.yaml
    hooks:
      use: [{ hook: ./hooks/own.hook.yaml }]
""",
        extra="defaults:\n  hooks:\n    use: [{ hook: ./hooks/team.hook.yaml }]\n",
    )
    hooks = resolve(load_config_file(config), "web").hooks
    assert _cmds(hooks.before_run) == ["./team.sh", "./tmpl.sh", "./own.sh"]


def test_a_later_layer_replaces_the_same_application(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    config = _config(
        tmp_path,
        """    extends: base
    hooks:
      use: [{ hook: hooks/user.hook.yaml, with: { plan: premium } }]
""",
        extra=(
            "templates:\n  base:\n    hooks:\n"
            "      use: [{ hook: ./hooks/user.hook.yaml, with: { plan: free } }]\n"
        ),
    )
    hooks = resolve(load_config_file(config), "web").hooks
    assert _cmds(hooks.before_scenario) == ["./create-user.sh --plan premium"]


def test_a_null_clears_the_inherited_use_list(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "x.hook.yaml", "beforeRun: [{ cmd: ./x.sh }]\n")
    config = _config(
        tmp_path,
        "    extends: base\n    hooks:\n      use: null\n",
        extra="templates:\n  base:\n    hooks:\n      use: [{ hook: ./hooks/x.hook.yaml }]\n",
    )
    assert resolve(load_config_file(config), "web").hooks.before_run == []


# --- applying a component to a scenario ---


def test_a_scenario_component_wraps_the_scenarios_own_lists(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    path = _write(
        tmp_path / "scenarios" / "s.yaml",
        """
- name: upgrade
  hooks:
    use: [{ hook: ../hooks/user.hook.yaml, with: { plan: premium } }]
    beforeScenario: [{ cmd: ./own-before.sh }]
    afterScenario: [{ cmd: ./own-after.sh }]
  setup: [{ tap: { id: own.setup } }]
  steps: [{ type: { text: "${hooks.user.userId}", into: { id: field } } }]
  cleanup: [{ on: always, steps: [{ tap: { id: own.cleanup } }] }]
""",
    )
    [s] = load_expanded_scenarios(path, root=tmp_path)
    assert s.hooks is not None
    assert s.hooks.use == []
    assert _cmds(s.hooks.before_scenario) == ["./create-user.sh --plan premium", "./own-before.sh"]
    assert _cmds(s.hooks.after_scenario) == ["./own-after.sh", "./delete-user.sh"]
    assert _ids(s.setup) == ["login.premium", "own.setup"]
    assert [_ids(r.steps) for r in s.cleanup] == [["own.cleanup"], ["session.logout"]]


@pytest.mark.parametrize("list_name", ["beforeRun", "afterRun"])
def test_a_scenario_cannot_apply_a_run_level_component(tmp_path: Path, list_name: str) -> None:
    _write(tmp_path / "hooks" / "run.hook.yaml", f"{list_name}: [{{ cmd: ./x.sh }}]\n")
    path = _write(
        tmp_path / "scenarios" / "s.yaml",
        """
- name: s
  hooks: { use: [{ hook: ../hooks/run.hook.yaml }] }
  steps: [{ tap: { id: ok } }]
""",
    )
    with pytest.raises(ValueError, match=f"holds `{list_name}`"):
        load_expanded_scenarios(path, root=tmp_path)


def test_a_data_driven_scenario_keeps_its_expanded_hooks(tmp_path: Path) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    path = _write(
        tmp_path / "scenarios" / "s.yaml",
        """
- name: s
  data: [{ plan: a }, { plan: b }]
  hooks: { use: [{ hook: ../hooks/user.hook.yaml, with: { plan: "${row.plan}" } }] }
  steps: [{ tap: { id: ok } }]
""",
    )
    rows = load_expanded_scenarios(path, root=tmp_path)
    # The `${row.*}` token sits quoted in its argument, so a row value stays one argument too.
    assert [
        shlex.split(s.hooks.before_scenario[0].cmd or "") for s in rows if s.hooks is not None
    ] == [["./create-user.sh", "--plan", "a"], ["./create-user.sh", "--plan", "b"]]


def test_the_run_loader_expands_a_scenarios_components(tmp_path: Path) -> None:
    from bajutsu.run.cli import _expand_file

    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    path = _write(
        tmp_path / "scenarios" / "s.yaml",
        """
- name: s
  hooks: { use: [{ hook: ../hooks/user.hook.yaml, with: { plan: free } }] }
  steps: [{ tap: { id: ok } }]
""",
    )
    [s], _, _ = _expand_file(path, root=tmp_path)
    assert s.hooks is not None
    assert s.hooks.use == []
    assert _cmds(s.hooks.before_scenario) == ["./create-user.sh --plan free"]
    assert _ids(s.setup) == ["login.free"]


def test_the_run_loader_stops_on_a_component_error(tmp_path: Path) -> None:
    import typer

    from bajutsu.run.cli import _expand_file

    path = _write(
        tmp_path / "s.yaml",
        "- name: s\n  hooks: { use: [{ hook: ./nope.hook.yaml }] }\n  steps: [{ tap: { id: ok } }]\n",
    )
    with pytest.raises(typer.Exit):
        _expand_file(path, root=tmp_path)


def test_a_template_file_application_is_replaced_by_the_targets(tmp_path: Path) -> None:
    # The template file's path is rebased to the config's directory, so both name one application.
    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    _write(
        tmp_path / "templates" / "user.yaml",
        "hooks:\n  use: [{ hook: ../hooks/user.hook.yaml, with: { plan: free } }]\n",
    )
    config = _config(
        tmp_path,
        """    extends: ./templates/user.yaml
    hooks:
      use: [{ hook: ./hooks/user.hook.yaml, with: { plan: premium } }]
""",
    )
    hooks = resolve(load_config_file(config), "web").hooks
    assert _cmds(hooks.before_scenario) == ["./create-user.sh --plan premium"]


@pytest.mark.parametrize(
    ("own", "message"),
    [
        ("beforeScenario: [{ name: user, cmd: ./x.sh }]", "two hook entries are named 'user'"),
        ("use: [{ hook: ../hooks/user.hook.yaml, with: { plan: b } }]", "applied twice"),
    ],
)
def test_a_scenario_application_meets_the_scenarios_names(
    tmp_path: Path, own: str, message: str
) -> None:
    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    first = "{ hook: ../hooks/user.hook.yaml, with: { plan: a } }"
    hooks = own.replace("use: [", f"use: [{first}, ") if own.startswith("use") else own
    if not own.startswith("use"):
        hooks = f"use: [{first}]\n    {own}"
    path = _write(
        tmp_path / "scenarios" / "s.yaml",
        f"- name: s\n  hooks:\n    {hooks}\n  steps: [{{ tap: {{ id: ok }} }}]\n",
    )
    with pytest.raises(ValueError, match=message):
        load_expanded_scenarios(path, root=tmp_path)


def test_parameters_reach_cleanup_and_an_undeclared_one_in_setup_stops(tmp_path: Path) -> None:
    _write(
        tmp_path / "hooks" / "c.hook.yaml",
        "params: [p]\ncleanup: [{ on: always, steps: [{ tap: { id: 'out.${params.p}' } }] }]\n",
    )
    _write(tmp_path / "hooks" / "s.hook.yaml", "setup: [{ tap: { id: '${params.x}' } }]\n")
    ok = _config(
        tmp_path, "    hooks:\n      use: [{ hook: ./hooks/c.hook.yaml, with: { p: v } }]\n"
    )
    cleanup = resolve(load_config_file(ok), "web").run_defaults.cleanup
    assert [_ids(r.steps) for r in cleanup] == [["out.v"]]
    bad = _config(tmp_path, "    hooks:\n      use: [{ hook: ./hooks/s.hook.yaml }]\n")
    with pytest.raises(ValidationError, match="undeclared params"):
        load_config_file(bad)


def test_a_missing_component_file_stops_the_load(tmp_path: Path) -> None:
    config = _config(tmp_path, "    hooks:\n      use: [{ hook: ./hooks/nope.hook.yaml }]\n")
    with pytest.raises(ValidationError, match="cannot read the component"):
        load_config_file(config)


def test_a_cmd_that_does_not_split_stops_the_load(tmp_path: Path) -> None:
    _write(
        tmp_path / "hooks" / "q.hook.yaml",
        'params: [p]\nbeforeRun: [{ cmd: "./x.sh \'${params.p}" }]\n',
    )
    config = _config(
        tmp_path, "    hooks:\n      use: [{ hook: ./hooks/q.hook.yaml, with: { p: v } }]\n"
    )
    with pytest.raises(ValidationError, match="does not split into arguments"):
        load_config_file(config)


def test_expanded_hooks_load_again_without_a_second_application(tmp_path: Path) -> None:
    from bajutsu.common.config.schema.hooks import Hooks

    _write(tmp_path / "hooks" / "user.hook.yaml", _SEEDED_USER)
    context = {"config_path": tmp_path / "bajutsu.config.yaml"}
    once = Hooks.model_validate(
        {"use": [{"hook": "./hooks/user.hook.yaml", "with": {"plan": "free"}}]}, context=context
    )
    again = Hooks.model_validate(
        once.model_dump(by_alias=True, exclude_unset=True), context=context
    )
    assert _cmds(again.before_scenario) == _cmds(once.before_scenario)
    assert len(again.setup) == len(once.setup) == 1


def test_a_hooks_block_loaded_without_its_path_cannot_apply_a_component() -> None:
    from bajutsu.common.config.schema.hooks import Hooks

    with pytest.raises(ValidationError, match="loaded without its path"):
        Hooks.model_validate({"use": [{"hook": "./x.hook.yaml"}]})


def test_two_equal_applications_in_one_layer_still_need_an_alias(tmp_path: Path) -> None:
    # A lower layer's list must not turn the same-layer repeat into a silent replacement.
    _write(tmp_path / "hooks" / "x.hook.yaml", "beforeRun: [{ cmd: ./x.sh }]\n")
    config = _config(
        tmp_path,
        "    hooks:\n      use: [{ hook: ./hooks/x.hook.yaml }, { hook: ./hooks/x.hook.yaml }]\n",
        extra="defaults:\n  hooks:\n    use: [{ hook: ./hooks/x.hook.yaml }]\n",
    )
    with pytest.raises(ValidationError, match="applied twice"):
        load_config_file(config)


def test_a_declared_parameter_in_a_field_that_takes_none_is_named(tmp_path: Path) -> None:
    _write(
        tmp_path / "hooks" / "c.hook.yaml",
        "params: [d]\nbeforeRun: [{ cmd: ./x.sh, cwd: '${params.d}' }]\n",
    )
    config = _config(
        tmp_path, "    hooks:\n      use: [{ hook: ./hooks/c.hook.yaml, with: { d: v } }]\n"
    )
    with pytest.raises(ValidationError, match="takes no parameter"):
        load_config_file(config)


def test_a_scenario_component_error_names_the_scenario(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.yaml",
        "- name: named one\n  hooks: { use: [{ hook: ./nope.hook.yaml }] }\n  steps: [{ tap: { id: ok } }]\n",
    )
    with pytest.raises(ValueError, match=r"scenario 'named one': hooks\.use"):
        load_expanded_scenarios(path, root=tmp_path)


def test_a_malformed_use_entry_passes_the_merge_to_the_models_error() -> None:
    from bajutsu.common.config.schema._merge import merge_layer

    base = {"hooks": {"use": [{"hook": "./a.yaml"}]}}
    over = {"hooks": {"use": ["./a.yaml", {"hook": "a.yaml", "as": None}]}}
    # The string keeps an identity of its own; the dict names the base's application and replaces it.
    assert merge_layer(base, over)["hooks"]["use"] == [{"hook": "a.yaml", "as": None}, "./a.yaml"]


def test_a_template_files_use_entry_without_a_hook_is_left_for_the_model(tmp_path: Path) -> None:
    from bajutsu.common.config.schema._templates import _rebased

    block = {"hooks": {"use": [{"with": {}}, {"hook": "../hooks/x.yaml"}]}}
    out = _rebased(block, tmp_path / "templates", tmp_path)
    assert out["hooks"]["use"] == [{"with": {}}, {"hook": "hooks/x.yaml"}]


def test_an_aliased_step_keeps_only_what_the_author_wrote(tmp_path: Path) -> None:
    # A visual check's defaults must not read as set once the alias rewrite re-validates the step.
    _write(
        tmp_path / "hooks" / "v.hook.yaml",
        """
beforeScenario: [{ name: user, cmd: ./c.sh }]
setup:
  - assert:
      - { visual: { baseline: b.png, compare: exact } }
      - { enabled: { label: "${hooks.user.name}" } }
""",
    )
    config = _config(tmp_path, "    hooks:\n      use: [{ hook: ./hooks/v.hook.yaml, as: prem }]\n")
    [step] = resolve(load_config_file(config), "web").run_defaults.setup
    assert "${hooks.web:prem.user.name}" in str(step.model_dump(by_alias=True))
