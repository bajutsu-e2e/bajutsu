"""Declarative hook conditions on targets, platforms, routes, and the environment (BE-0513).

Covers the four new `when` fields and their load errors, the narrowed run-level check, the check on
a target's own condition that can never hold, the conditional setup block and the `when` of a
cleanup rule, the decision before the first step, the two-stage `${hooks.*}` check, `serve`'s view
with an unknown environment, `codegen`'s refusal, and the report's skipped-block rows.
"""

from __future__ import annotations

import typing
from typing import Any

import pytest
from pydantic import ValidationError

from bajutsu.codegen.common import CodegenError
from bajutsu.codegen.playwright import to_playwright
from bajutsu.common.capability.command_gate import CommandGate
from bajutsu.common.capability.settings_support import Route
from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.config.effective.hook_phases import HookPhases
from bajutsu.common.config.schema.hooks import Hooks
from bajutsu.common.runner import hook_commands as hc
from bajutsu.common.runner.conditions import ConditionContext, condition_context
from bajutsu.common.runner.hook_session import HookSession
from bajutsu.common.runner.pipeline import with_lifecycle_phases
from bajutsu.common.scenario import Scenario, load_scenarios
from bajutsu.common.scenario.models.hook_command import (
    HOOK_ROUTES,
    HookCommand,
    HookWhen,
    JudgedTarget,
    WhenFacts,
)

_IOS = JudgedTarget("app", "ios", "ios-simulator")
_DEVICE = JudgedTarget("app", "ios", "ios-device")
_ANDROID = JudgedTarget("droid", "android", "android")


def _when(**fields: Any) -> HookWhen:
    return HookWhen.model_validate(fields)


# --- the fields of `when` ---


def test_the_route_names_match_the_settings_routes() -> None:
    assert typing.get_args(Route) == HOOK_ROUTES


@pytest.mark.parametrize(
    ("when", "targets", "holds"),
    [
        ({"targets": ["app"]}, [_IOS], True),
        ({"targets": ["web"]}, [_IOS], False),
        ({"platforms": ["ios"]}, [_ANDROID, _IOS], True),
        ({"platforms": ["web"]}, [_ANDROID, _IOS], False),
        ({"routes": ["ios-device"]}, [_DEVICE], True),
        ({"routes": ["ios-device"]}, [_IOS], False),
        # The three fields describe one judged target, never one field per target.
        ({"targets": ["droid"], "platforms": ["ios"]}, [_ANDROID, _IOS], False),
        ({"platforms": ["ios"], "routes": ["ios-device"]}, [_IOS, _DEVICE], True),
        # A target with no route (`fake`) never meets `routes`.
        ({"routes": ["web"]}, [JudgedTarget("f", "fake")], False),
    ],
)
def test_target_fields_judge_one_and_the_same_target(
    when: dict[str, Any], targets: list[JudgedTarget], holds: bool
) -> None:
    assert _when(**when).holds(WhenFacts(targets=targets)) is holds


@pytest.mark.parametrize(
    ("env", "process", "holds"),
    [
        ({"CI": "true"}, {"CI": "true"}, True),
        ({"CI": "true"}, {"CI": "TRUE"}, False),
        ({"CI": None}, {}, True),
        ({"CI": None}, {"CI": ""}, False),
        ({"CI": "", "MODE": "x"}, {"CI": "", "MODE": "x"}, True),
        ({"CI": "", "MODE": "x"}, {"CI": ""}, False),
    ],
)
def test_env_matches_exactly_and_null_asks_for_unset(
    env: dict[str, str | None], process: dict[str, str], holds: bool
) -> None:
    assert _when(env=env).holds(WhenFacts(env=process)) is holds


def test_fields_combine_with_and() -> None:
    when = _when(tags=["smoke"], platforms=["ios"], env={"CI": None})
    assert when.holds(WhenFacts(tags=[["smoke"]], targets=[_IOS]))
    assert not when.holds(WhenFacts(tags=[["smoke"]], targets=[_IOS], env={"CI": "1"}))
    assert not when.holds(WhenFacts(tags=[["other"]], targets=[_IOS]))


@pytest.mark.parametrize("value", [True, 1, 1.5])
def test_an_unquoted_env_value_fails_to_load(value: object) -> None:
    with pytest.raises(ValidationError, match="quote the value"):
        HookWhen.model_validate({"env": {"CI": value}})


@pytest.mark.parametrize("when", [{"platforms": ["windows"]}, {"routes": ["ios"]}])
def test_an_unknown_platform_or_route_fails_to_load(when: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        HookWhen.model_validate(when)


def test_a_when_of_empty_fields_fails_to_load() -> None:
    with pytest.raises(ValidationError, match="at least one non-empty field"):
        HookWhen.model_validate({"targets": [], "env": {}})


def test_the_condition_prints_as_written_never_the_actual_value() -> None:
    when = _when(platforms=["ios"], routes=["ios-device"], env={"TOKEN": None, "MODE": "ci"})
    assert str(when) == "platforms: [ios], routes: [ios-device], env: {TOKEN: null, MODE: 'ci'}"


# --- the run-level check ---


@pytest.mark.parametrize("key", ["beforeRun", "afterRun"])
def test_a_run_level_condition_needs_no_tags_now(key: str) -> None:
    Hooks.model_validate({key: [{"cmd": "x", "when": {"platforms": ["ios"]}}]})
    Hooks.model_validate({key: [{"cmd": "x", "when": {"env": {"CI": None}}}]})


# --- the config's load checks ---


def _config(target_hooks: str, defaults: str = "", extra: str = "") -> str:
    return (
        defaults + "targets:\n"
        "  app:\n"
        "    platform: ios\n"
        "    subject: { id: com.x }\n" + target_hooks + extra
    )


@pytest.mark.parametrize(
    ("when", "message"),
    [
        ("{ targets: [web] }", "`targets` leaves out 'app'"),
        ("{ platforms: [android] }", "`platforms` leaves out 'ios'"),
        ("{ routes: [web] }", "`routes` names no route a ios target runs on"),
    ],
)
def test_a_targets_own_condition_that_never_holds_fails_to_load(when: str, message: str) -> None:
    text = _config(
        f"    hooks:\n      beforeScenario: [{{ cmd: x, when: {when} }}]\n",
        extra="  web: { platform: web, subject: { url: 'http://x' } }\n",
    )
    with pytest.raises(ValidationError, match=message):
        load_config(text)


def test_a_setup_block_and_cleanup_rule_are_checked_too() -> None:
    block = (
        "    hooks:\n      setup: [{ when: { platforms: [web] }, steps: [{ tap: { id: a } }] }]\n"
    )
    with pytest.raises(ValidationError, match=r"hooks\.setup\[0\]\.when"):
        load_config(_config(block))
    rule = "    hooks:\n      cleanup: [{ 'on': always, when: { routes: [android] }, steps: [] }]\n"
    with pytest.raises(ValidationError, match=r"hooks\.cleanup\[0\]\.when"):
        load_config(_config(rule))


def test_a_merged_copy_that_never_runs_stays_valid() -> None:
    # A shared `defaults` entry the target cannot meet is the point of sharing it.
    defaults = "defaults:\n  hooks:\n    beforeRun: [{ cmd: x, when: { platforms: [android] } }]\n"
    load_config(_config("", defaults=defaults))


def test_a_condition_naming_an_undeclared_target_fails_to_load() -> None:
    text = _config("    hooks:\n      beforeRun: [{ cmd: x, when: { targets: [app, ghost] } }]\n")
    with pytest.raises(ValidationError, match="'ghost', which the config does not declare"):
        load_config(text)


# --- the conditional setup block and the cleanup rule's `when` ---


def _scenario(body: dict[str, Any]) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": [], **body})


def test_a_block_loads_with_or_without_a_target() -> None:
    s = _scenario(
        {
            "targets": ["app", "droid"],
            "setup": [
                {"when": {"platforms": ["ios"]}, "target": "app", "steps": [{"tap": {"id": "a"}}]},
                {"when": {"env": {"CI": None}}, "steps": [{"tap": {"id": "b"}, "target": "droid"}]},
            ],
        }
    )
    assert [step.target for step in s.setup[0].steps or []] == ["app"]
    assert [step.target for step in s.setup[1].steps or []] == ["droid"]


@pytest.mark.parametrize(
    "body",
    [
        {"steps": [{"when": {"tags": ["a"]}, "steps": [{"tap": {"id": "a"}}]}]},
        {
            "setup": [
                {
                    "if": {
                        "condition": {"exists": {"id": "x"}},
                        "then": [{"when": {"tags": ["a"]}, "steps": [{"tap": {"id": "a"}}]}],
                    }
                }
            ]
        },
        {
            "setup": [
                {
                    "when": {"tags": ["a"]},
                    "steps": [{"when": {"tags": ["b"]}, "steps": [{"tap": {"id": "a"}}]}],
                }
            ]
        },
    ],
)
def test_a_block_outside_the_top_of_setup_fails_to_load(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _scenario(body)


def test_a_block_needs_steps_and_when_needs_a_block() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        _scenario({"setup": [{"when": {"tags": ["a"]}, "steps": []}]})
    with pytest.raises(ValidationError, match="only as a conditional block"):
        _scenario({"setup": [{"tap": {"id": "a"}, "when": {"tags": ["a"]}}]})


def test_a_target_block_outside_the_top_of_setup_fails_to_load() -> None:
    with pytest.raises(ValidationError, match=r"top level of hooks\.setup"):
        Hooks.model_validate(
            {
                "cleanup": [
                    {
                        "on": "always",
                        "steps": [{"when": {"tags": ["a"]}, "steps": [{"tap": {"id": "a"}}]}],
                    }
                ]
            }
        )


def test_a_cleanup_rule_takes_when() -> None:
    s = _scenario({"cleanup": [{"on": "always", "when": {"platforms": ["ios"]}, "steps": []}]})
    assert s.cleanup[0].when == _when(platforms=["ios"])
    with pytest.raises(ValidationError, match="cannot be null"):
        _scenario({"cleanup": [{"on": "always", "when": None, "steps": []}]})


def test_a_null_block_when_fails_to_load() -> None:
    with pytest.raises(ValidationError, match="conditional block's `when` cannot be null"):
        _scenario({"setup": [{"target": "a", "when": None, "steps": [{"tap": {"id": "x"}}]}]})


def test_a_pre_run_check_keeps_every_block() -> None:
    every = ConditionContext.every_block()
    assert every.holds(_when(routes=["ios-device"], env={"CI": "1"}), ["app"], [()])


# --- the decision before the first step ---


_TWO = (
    "targets:\n"
    "  app:\n"
    "    platform: ios\n"
    "    subject: { id: com.x }\n"
    "    hooks:\n"
    "      setup:\n"
    "        - when: { platforms: [ios] }\n"
    "          steps: [{ tap: { id: ios.only } }]\n"
    "        - when: { platforms: [android] }\n"
    "          target: droid\n"
    "          steps: [{ tap: { id: never } }]\n"
    "      cleanup:\n"
    "        - { 'on': always, when: { env: { CI: null } }, steps: [{ tap: { id: local } }] }\n"
    "  droid:\n"
    "    platform: android\n"
    "    subject: { id: com.x }\n"
)


def _effs() -> dict[str, Effective]:
    config = load_config(_TWO)
    return {name: resolve(config, name) for name in config.targets}


def _ids(steps: list[Any]) -> list[str]:
    return [step.tap.id for step in steps]


def test_a_targets_block_is_judged_against_its_owner() -> None:
    effs = _effs()
    s = _scenario(
        {
            "targets": ["app", "droid"],
            "setup": [{"tap": {"id": "own"}, "target": "app"}],
        }
    )
    folded = with_lifecycle_phases(
        effs["app"], [s], effs, conditions=condition_context(effs, env={})
    )[0]
    # The first block runs for its owner; the second names `droid`, an android target, so it holds
    # too even though the owner is iOS.
    assert _ids(folded.setup) == ["ios.only", "never", "own"]
    assert [step.target for step in folded.setup] == ["app", "droid", "app"]
    assert _ids([step for rule in folded.cleanup for step in rule.steps]) == ["local"]
    assert folded.skipped_blocks == []


def test_a_skipped_block_and_rule_are_recorded() -> None:
    effs = _effs()
    s = _scenario({"targets": ["app", "droid"]})
    folded = with_lifecycle_phases(
        effs["app"], [s], effs, conditions=condition_context(effs, env={"CI": "1"})
    )[0]
    assert folded.cleanup == []
    assert folded.skipped_blocks == [
        {
            "phase": "cleanup",
            "label": "targets.app.hooks.cleanup[0] (on: always)",
            "when": "env: {CI: null}",
        }
    ]


def test_a_scenarios_block_judges_every_driven_target() -> None:
    effs = _effs()
    s = _scenario(
        {
            "targets": ["app", "droid"],
            "setup": [
                {
                    "when": {"platforms": ["android"]},
                    "steps": [{"tap": {"id": "droid.step"}, "target": "droid"}],
                },
                {
                    "when": {"routes": ["web"]},
                    "steps": [{"tap": {"id": "web.step"}, "target": "app"}],
                },
            ],
            "preconditions": {"inheritSetup": False},
        }
    )
    folded = with_lifecycle_phases(
        effs["app"], [s], effs, conditions=condition_context(effs, env={})
    )[0]
    assert _ids(folded.setup) == ["droid.step"]
    assert [b["label"] for b in folded.skipped_blocks] == ["setup[1]"]


def test_an_unknown_environment_keeps_an_env_block() -> None:
    effs = _effs()
    unknown = condition_context(effs, known_env=False)
    assert unknown.env is None
    assert unknown.holds(_when(env={"CI": "1"}), ["app"], [()])
    assert unknown.holds(_when(env={"CI": "1"}, platforms=["ios"]), ["app"], [()])
    assert not unknown.holds(_when(env={"CI": "1"}, platforms=["web"]), ["app"], [()])


def test_the_route_follows_the_actuator_and_the_device_spec() -> None:
    effs = _effs()
    assert condition_context(effs).targets["droid"].route == "android"
    assert condition_context(effs, actuators={"app": "fake"}).targets["app"].route is None
    live = condition_context(effs, udids={"app": "http://127.0.0.1:4723"})
    assert live.targets["app"].route == "ios-live"


# --- command hooks ---


class _Spawn:
    def __init__(self) -> None:
        self.ran: list[str] = []

    def __call__(self, argv: list[str], *_: object) -> int:
        self.ran.append(argv[0])
        return 0


def _session(spawn: _Spawn, conditions: ConditionContext, **lists: Any) -> HookSession:
    runner = hc.HookRunner(raw_dir=None, gate=CommandGate(), runner=spawn)
    return HookSession(
        runner=runner,
        phases={"app": HookPhases(**lists)},
        run_id="r",
        conditions=conditions,
    )


def _entry(cmd: str, when: dict[str, Any]) -> HookCommand:
    return HookCommand.model_validate({"cmd": cmd, "when": when})


def test_a_run_level_entry_reads_its_owner_and_the_environment() -> None:
    spawn = _Spawn()
    context = ConditionContext({"app": _DEVICE}, {"CI": "1"})
    session = _session(
        spawn,
        context,
        before_run=[
            _entry("device", {"routes": ["ios-device"]}),
            _entry("sim", {"routes": ["ios-simulator"]}),
            _entry("local", {"env": {"CI": None}}),
            _entry("ci", {"env": {"CI": "1"}}),
        ],
    )
    result = session.before_run()
    assert spawn.ran == ["device", "ci"]
    skipped = [r.when for r in result.records if r.skipped]
    assert skipped == ["routes: [ios-simulator]", "env: {CI: null}"]


def test_a_scenarios_own_entry_judges_every_driven_target() -> None:
    spawn = _Spawn()
    context = ConditionContext({"app": _IOS, "droid": _ANDROID}, {})
    session = _session(spawn, context)
    own = [_entry("droid", {"platforms": ["android"]}), _entry("web", {"platforms": ["web"]})]
    hook_context = session.context(
        "app", judged_targets=context.judged(["app", "droid"]), scenario_tags=((),)
    )
    session.runner.run_list("beforeScenario", own, hook_context)
    assert spawn.ran == ["droid"]


# --- the two-stage token check ---


def _phases(when: dict[str, Any]) -> dict[str, HookPhases]:
    entry = HookCommand.model_validate({"cmd": "x", "name": "api", "when": when})
    return {"app": HookPhases(before_scenario=[entry])}


def test_the_load_stage_decides_static_fields_and_defers_the_rest() -> None:
    static = ConditionContext({"app": JudgedTarget("app", "ios")}, {})

    def load(when: HookWhen, names: Any) -> bool | None:
        return None if when.needs_run else static.holds(when, names, [()])

    assert hc.unmet_hook_condition(
        {"hooks.api.url"}, _phases({"platforms": ["android"]}), ["app"], [], judge=load
    )
    assert (
        hc.unmet_hook_condition(
            {"hooks.api.url"}, _phases({"routes": ["web"]}), ["app"], [], judge=load
        )
        is None
    )


def test_the_run_stage_decides_env_and_routes() -> None:
    run = ConditionContext({"app": _IOS}, {"CI": "1"})

    def judge(when: HookWhen, names: Any) -> bool:
        return run.holds(when, names, [()])

    why = hc.unmet_hook_condition(
        {"hooks.api.url"}, _phases({"env": {"CI": None}}), ["app"], [], judge=judge
    )
    assert why is not None and "env: {CI: null}" in why


# --- codegen ---


def test_codegen_refuses_a_conditional_block_and_rule() -> None:
    block = """
- name: s
  setup:
    - when: { platforms: [web] }
      steps: [{ tap: { id: a } }]
  steps: [{ tap: { id: b } }]
"""
    with pytest.raises(CodegenError, match="conditional setup block"):
        to_playwright(load_scenarios(block), "demo", "http://localhost")
    rule = """
- name: s
  steps: [{ tap: { id: b } }]
  cleanup:
    - { on: always, when: { env: { CI: null } }, steps: [{ tap: { id: c } }] }
"""
    with pytest.raises(CodegenError, match="cleanup rule with `when`"):
        to_playwright(load_scenarios(rule), "demo", "http://localhost")


# --- what the review pass found uncovered ---


def test_a_skipped_target_block_is_recorded_when_nothing_else_folds() -> None:
    config = load_config(
        "targets:\n"
        "  app:\n"
        "    platform: ios\n"
        "    subject: { id: com.x }\n"
        "    hooks:\n"
        "      setup: [{ when: { env: { CI: '1' } }, steps: [{ tap: { id: ci } }] }]\n"
    )
    eff = resolve(config, "app")
    folded = with_lifecycle_phases(
        eff, [_scenario({})], conditions=condition_context({"app": eff}, env={})
    )[0]
    assert folded.setup == []
    assert [b["label"] for b in folded.skipped_blocks] == ["targets.app.hooks.setup[0]"]


def test_the_pre_run_walkers_look_inside_a_block() -> None:
    from bajutsu.run.cli import _scenario_mock_swaps, _walk_visual_steps

    s = _scenario(
        {
            "setup": [
                {
                    "when": {"platforms": ["ios"]},
                    "steps": [{"setMocks": []}, {"tap": {"id": "a"}}],
                }
            ]
        }
    )
    assert len(_scenario_mock_swaps(s)) == 1
    assert sum(1 for _ in _walk_visual_steps(s.setup)) == 3


def test_lint_and_serve_keep_an_env_block_and_drop_a_static_mismatch() -> None:
    from bajutsu.common.target_lint import _plans

    effs = _effs()
    s = _scenario(
        {
            "setup": [
                {"when": {"env": {"CI": "1"}}, "steps": [{"tap": {"id": "env"}}]},
                {"when": {"platforms": ["android"]}, "steps": [{"tap": {"id": "android"}}]},
            ],
            "preconditions": {"inheritSetup": False},
        }
    )
    plan = _plans([s], effs.__getitem__, "app")[0]
    assert _ids(plan.folded.setup) == ["env"]


def test_the_report_lists_a_skipped_block(tmp_path: Any) -> None:
    from _runner import _eff, _lease

    from bajutsu.common.runner.pipeline import run_and_report

    s = _scenario(
        {
            "setup": [{"when": {"env": {"NEVER_SET": "x"}}, "steps": [{"tap": {"id": "a"}}]}],
            "steps": [],
        }
    )
    _, manifest = run_and_report(
        _eff(), [s], _lease, tmp_path, "r", conditions=ConditionContext({}, {})
    )
    page = (manifest.parent / "report.html").read_text()
    assert "conditional blocks" in page
    assert "skipped (when)" in page
    assert "NEVER_SET" in page


# --- the command-line checks around a run ---


def _loaded() -> Any:
    from pathlib import Path

    from bajutsu.cli._shared import LoadedConfig

    return LoadedConfig(config=load_config(_TWO), path=Path("c.yaml"), source=None, root=None)


def _own(when: dict[str, Any], **body: Any) -> Scenario:
    entry = {"cmd": "x", "name": "api", "when": when}
    return _scenario(
        {
            "targets": ["app"],
            "hooks": {"beforeScenario": [entry], "afterScenario": [{"cmd": "y", "when": when}]},
            "steps": [{"tap": {"id": "${hooks.api.url}"}, "target": "app"}],
            **body,
        }
    )


def test_a_scenarios_condition_naming_an_undeclared_target_exits_2() -> None:
    import typer

    from bajutsu.cli._shared import _reject_unmet_hook_conditions

    s = _scenario(
        {
            "targets": ["app"],
            "setup": [{"when": {"targets": ["app"]}, "steps": [{"tap": {"id": "a"}}]}],
            "cleanup": [{"on": "always", "when": {"targets": ["ghost"]}, "steps": []}],
        }
    )
    with pytest.raises(typer.Exit):
        _reject_unmet_hook_conditions(_loaded(), [s], "app", selected=[s])


def test_the_load_stage_defers_env_and_the_run_stage_decides_it() -> None:
    import typer

    from bajutsu.cli._shared import _reject_unmet_hook_conditions, _reject_unmet_run_conditions

    s = _own({"env": {"CI": None}})
    _reject_unmet_hook_conditions(_loaded(), [s], "app", selected=[s])
    effs = _effs()
    _reject_unmet_run_conditions([s], effs, "app", condition_context(effs, env={}))
    with pytest.raises(typer.Exit):
        _reject_unmet_run_conditions([s], effs, "app", condition_context(effs, env={"CI": "1"}))


def test_component_expansion_keeps_a_block_whole() -> None:
    from bajutsu.common.scenario import Step, expand_steps

    block = Step.model_validate({"when": {"platforms": ["ios"]}, "steps": [{"tap": {"id": "a"}}]})
    (out,) = expand_steps([block], lambda ref: pytest.fail(f"no component expected: {ref}"))
    assert out.when == block.when
    assert _ids(out.steps or []) == ["a"]


def test_a_component_block_takes_its_callers_target() -> None:
    from bajutsu.common.scenario import Step
    from bajutsu.common.scenario.expand import _step_expander

    block = Step.model_validate({"when": {"platforms": ["ios"]}, "steps": [{"tap": {"id": "a"}}]})
    (out,) = _step_expander(25)([block], [], pytest.fail, level_target="app")
    assert out.target == "app"
    assert [step.target for step in out.steps or []] == ["app"]


def test_lint_keeps_a_block_gated_on_any_route() -> None:
    from bajutsu.common.target_lint import _plans

    s = _scenario(
        {
            "setup": [{"when": {"routes": ["ios-device"]}, "steps": [{"tap": {"id": "device"}}]}],
            "preconditions": {"inheritSetup": False},
        }
    )
    effs = _effs()
    assert _ids(_plans([s], effs.__getitem__, "app")[0].folded.setup) == []
    assert _ids(_plans([s], effs.__getitem__, "app", keep_all=True)[0].folded.setup) == ["device"]
