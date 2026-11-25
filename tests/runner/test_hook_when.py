"""Tests for the `when` condition on an external hook command (BE-0497)."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from _runner import _eff, _lease
from pydantic import ValidationError

from bajutsu.common.capability.command_gate import CommandGate
from bajutsu.common.capability.command_policy import (
    CommandExecution,
    CommandPolicy,
    command_notices,
)
from bajutsu.common.capability.route_tokens import report_hook_phases
from bajutsu.common.config.effective.hook_phases import HookPhases
from bajutsu.common.config.schema.hook_command import HookCommand, HookWhen
from bajutsu.common.config.schema.hooks import Hooks
from bajutsu.common.report.html import html_report
from bajutsu.common.runner import hook_commands as hc
from bajutsu.common.runner.hook_session import HookSession, run_tags
from bajutsu.common.runner.pipeline import run_all
from bajutsu.common.scenario import Scenario, expand_data


class _Spawn:
    """A process seam that records each argv instead of spawning it."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    def __call__(
        self,
        argv: list[str],
        env: dict[str, str],
        cwd: Path | None,
        timeout: float,
        log: Path | None,
    ) -> int:
        self.ran.append(argv[0])
        return 0


def _entry(cmd: str, **kw: Any) -> HookCommand:
    return HookCommand.model_validate({"cmd": cmd, **kw})


def _runner(spawn: _Spawn, gate: CommandGate | None = None) -> hc.HookRunner:
    return hc.HookRunner(raw_dir=None, gate=gate or CommandGate(), runner=spawn)


def _ctx(*tags: list[str], verdict: str | None = None) -> hc.HookContext:
    return hc.HookContext(run_id="r", target="web", verdict=verdict, scenario_tags=tags)


def _scn(name: str, tags: list[str], targets: list[str] | None = None) -> Scenario:
    return Scenario.model_validate(
        {"name": name, "tags": tags, "targets": targets or [], "steps": []}
    )


# --- schema ---


@pytest.mark.parametrize("when", [{}, {"tags": []}, {"tags": [], "exclude": []}])
def test_a_when_that_constrains_nothing_fails_to_load(when: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="non-empty `tags` or `exclude`"):
        HookWhen.model_validate(when)


@pytest.mark.parametrize("key", ["beforeRun", "afterRun"])
def test_a_run_level_when_needs_tags(key: str) -> None:
    with pytest.raises(ValidationError, match=rf"hooks\.{key}\[0\]\.when needs a non-empty `tags`"):
        Hooks.model_validate({key: [{"cmd": "x", "when": {"exclude": ["wip"]}}]})


@pytest.mark.parametrize("key", ["beforeScenario", "afterScenario"])
def test_a_scenario_level_when_may_only_exclude(key: str) -> None:
    Hooks.model_validate({key: [{"cmd": "x", "when": {"exclude": ["wip"]}}]})


@pytest.mark.parametrize("when", [{"tags": "a"}, {"tags": [1]}, {"tags": ["a"], "any": ["b"]}])
def test_a_malformed_when_fails_to_load(when: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        HookWhen.model_validate(when)


def test_when_words_only_the_fields_that_constrain() -> None:
    assert str(HookWhen(tags=["a", "b"])) == "tags: [a, b]"
    assert str(HookWhen(tags=["a"], exclude=["c"])) == "tags: [a], exclude: [c]"


# --- per-entry decision ---


@pytest.mark.parametrize(
    ("tags", "when", "runs"),
    [
        (["payments"], {"tags": ["payments"]}, True),
        (["smoke"], {"tags": ["payments"]}, False),
        (["payments", "readonly"], {"tags": ["payments"], "exclude": ["readonly"]}, False),
        (["smoke"], {"exclude": ["readonly"]}, True),
    ],
)
def test_when_gates_an_entry_on_the_scenario_tags(
    tags: list[str], when: dict[str, Any], runs: bool
) -> None:
    spawn = _Spawn()
    result = _runner(spawn).run_list("beforeScenario", [_entry("seed", when=when)], _ctx(tags))
    assert result.ok
    assert spawn.ran == (["seed"] if runs else [])
    assert result.records[0].skipped is not runs
    assert (result.records[0].when is None) is runs


def test_a_skipped_entry_writes_no_output_and_does_not_stop_the_list() -> None:
    spawn = _Spawn()
    result = _runner(spawn).run_list(
        "beforeScenario",
        [_entry("seed", name="seed", when={"tags": ["db"]}), _entry("always")],
        _ctx(["smoke"]),
    )
    assert spawn.ran == ["always"]
    assert result.outputs.values == {}
    assert result.records[0].log is None


@pytest.mark.parametrize(
    ("tags", "verdict", "runs"),
    [
        (["db"], "failure", True),
        (["db"], "success", False),
        (["smoke"], "failure", False),
    ],
)
def test_when_and_on_must_both_hold(tags: list[str], verdict: str, runs: bool) -> None:
    spawn = _Spawn()
    _runner(spawn).run_list(
        "afterScenario",
        [_entry("clean", on="failure", when={"tags": ["db"]})],
        _ctx(tags, verdict=verdict),
    )
    assert spawn.ran == (["clean"] if runs else [])


def test_no_scenario_matches_no_condition() -> None:
    # What `record`, `crawl`, and `repl` pass: they run no scenario file.
    spawn = _Spawn()
    _runner(spawn).run_list(
        "beforeRun", [_entry("mock", when={"tags": ["offline"]}), _entry("base")], _ctx()
    )
    assert spawn.ran == ["base"]


# --- run level ---


def test_run_tags_hold_only_the_scenarios_that_drive_each_target() -> None:
    tags = run_tags(
        [_scn("a", ["x"]), _scn("b", ["y"], ["admin"]), _scn("c", ["z"], ["web", "admin"])], "web"
    )
    assert tags == {"web": (("x",), ("z",)), "admin": (("y",), ("z",))}


def _session(
    spawn: _Spawn, tags: dict[str, tuple[tuple[str, ...], ...]], **lists: Any
) -> HookSession:
    return HookSession(
        runner=_runner(spawn), phases={"web": HookPhases(**lists)}, run_id="r", tags=tags
    )


@pytest.mark.parametrize(
    ("driven", "ran"),
    [
        ((("smoke",), ("offline",)), ["start", "stop"]),
        ((("smoke",),), []),
        ((), []),
    ],
)
def test_run_level_entries_run_when_any_scenario_matches(
    driven: tuple[tuple[str, ...], ...], ran: list[str]
) -> None:
    spawn = _Spawn()
    when = {"tags": ["offline"]}
    session = _session(
        spawn,
        {"web": driven},
        before_run=[_entry("start", when=when)],
        after_run=[_entry("stop", when=when)],
    )
    session.before_run()
    session.after_run("failure")
    assert spawn.ran == ran


def test_a_scenario_of_another_target_never_makes_a_run_level_entry_run() -> None:
    spawn = _Spawn()
    session = _session(
        spawn,
        {"admin": (("offline",),)},
        before_run=[_entry("start", when={"tags": ["offline"]})],
    )
    session.before_run()
    assert spawn.ran == []


def test_a_run_level_match_needs_one_scenario_to_satisfy_the_whole_condition() -> None:
    # The tags of two scenarios are never pooled: here one is excluded and the other lacks the tag.
    spawn = _Spawn()
    session = _session(
        spawn,
        {"web": (("offline", "wip"), ("smoke",))},
        before_run=[_entry("start", when={"tags": ["offline"], "exclude": ["wip"]})],
    )
    session.before_run()
    assert spawn.ran == []


# --- evidence and policy ---


def test_a_skip_is_recorded_in_command_executions() -> None:
    gate = CommandGate()
    _runner(_Spawn(), gate).run_list(
        "beforeRun", [_entry("mock", name="mock", when={"tags": ["offline"]})], _ctx(["smoke"])
    )
    (record,) = gate.records
    assert record["field"] == "hooks.beforeRun[mock].cmd"
    assert record["decision"] == "skipped"
    assert "exitCode" not in record
    assert "skipped (when)" in command_notices(gate.records)[0]


def test_an_entry_skipped_by_on_is_not_a_when_skip() -> None:
    gate = CommandGate()
    result = _runner(_Spawn(), gate).run_list(
        "afterRun", [_entry("dump", on="failure")], _ctx(["smoke"], verdict="success")
    )
    assert result.records[0].skipped and result.records[0].when is None
    assert gate.records == []


def test_the_policy_checks_an_entry_whose_when_never_matches() -> None:
    gate = CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="deny"))
    )
    phases = {"web": HookPhases(before_run=[_entry("mock", when={"tags": ["nobody"]})])}
    assert hc.hook_refusal(phases, gate) is not None


# --- scenario level, through the run loop ---


def test_a_scenario_level_when_reads_its_own_scenario_and_never_the_run_set(
    tmp_path: Path,
) -> None:
    spawn = _Spawn()
    when = {"tags": ["db"]}
    phases = HookPhases(
        before_scenario=[_entry("reset", name="reset", when=when)],
        after_scenario=[_entry("clean", when=when)],
    )
    eff = replace(_eff(), hooks=phases)
    # The run set holds a matching scenario, which must not leak into the other one's decision.
    session = HookSession(
        runner=_runner(spawn),
        phases={"demo": phases},
        run_id="r",
        tags={"demo": (("db",), ("smoke",))},
    )
    ran_before: list[list[str]] = []

    def lease(e: Any, s: Any) -> Any:
        ran_before.append(list(spawn.ran))
        return _lease(e, s)

    run_all(eff, [_scn("plain", ["smoke"]), _scn("data", ["db"])], lease, hooks=session)
    assert ran_before == [[], ["reset"]]
    assert spawn.ran == ["reset", "clean"]


def test_inherit_setup_false_skips_a_matching_entry() -> None:
    spawn = _Spawn()
    phases = HookPhases(before_scenario=[_entry("reset", when={"tags": ["db"]})])
    session = HookSession(runner=_runner(spawn), phases={"demo": phases}, run_id="r")
    scenario = Scenario.model_validate(
        {"name": "x", "tags": ["db"], "steps": [], "preconditions": {"inheritSetup": False}}
    )
    run_all(replace(_eff(), hooks=phases), [scenario], _lease, hooks=session)
    assert spawn.ran == []


# --- token check ---

_PHASES = {
    "web": HookPhases(
        before_run=[_entry("m", name="mock", when={"tags": ["offline"]})],
        before_scenario=[
            _entry("a", name="account", when={"tags": ["pay"], "exclude": ["ro"]}),
            _entry("p", name="plain"),
        ],
    ),
    "admin": HookPhases(before_run=[_entry("s", name="seed", when={"tags": ["pay"]})]),
}


@pytest.mark.parametrize(
    ("token", "driven", "tags", "match"),
    [
        ("hooks.account.id", ["web"], ["smoke"], r"tags: \[pay\], exclude: \[ro\].*are: smoke"),
        ("hooks.account.id", ["web"], ["pay", "ro"], "beforeScenario hook 'account'"),
        ("hooks.mock.port", ["web"], [], "beforeRun hook 'mock'.*are: none"),
        ("hooks.seed.id", ["web"], ["pay"], "target 'admin', which this scenario does not drive"),
    ],
)
def test_a_reader_that_does_not_match_is_rejected(
    token: str, driven: list[str], tags: list[str], match: str
) -> None:
    why = hc.unmet_hook_condition({token}, _PHASES, driven, tags)
    assert why is not None and re.search(match, why)


@pytest.mark.parametrize(
    ("token", "driven", "tags"),
    [
        ("hooks.account.id", ["web"], ["pay"]),
        ("hooks.mock.port", ["web"], ["offline"]),
        ("hooks.seed.id", ["web", "admin"], ["pay"]),
        ("hooks.plain.id", ["web"], []),
        ("secrets.X", ["web"], []),
    ],
)
def test_a_reader_that_matches_is_accepted(token: str, driven: list[str], tags: list[str]) -> None:
    assert hc.unmet_hook_condition({token}, _PHASES, driven, tags) is None


def test_a_scenario_level_reader_of_its_own_entry_is_checked_too() -> None:
    own = HookPhases(before_scenario=[_entry("s", name="sub", when={"tags": ["premium"]})])
    assert hc.unmet_hook_condition({"hooks.sub.id"}, {}, ["web"], ["premium"], own) is None
    why = hc.unmet_hook_condition({"hooks.sub.id"}, {}, ["web"], ["free"], own)
    assert why is not None and "this scenario's tags are: free" in why


# --- a scenario's own hooks (BE-0501) ---


def test_a_scenario_s_own_hook_picks_the_rows_it_runs_for() -> None:
    (free, premium) = expand_data(
        [
            Scenario.model_validate(
                {
                    "name": "checkout as ${row.plan}",
                    "tags": ["checkout", "${row.plan}"],
                    "data": [{"plan": "free"}, {"plan": "premium"}],
                    "steps": [],
                    "hooks": {
                        "beforeScenario": [{"cmd": "grant", "when": {"tags": ["premium"]}}],
                        "afterScenario": [{"cmd": "revoke", "when": {"tags": ["premium"]}}],
                    },
                }
            )
        ],
        lambda _ref: [],
    )
    spawn = _Spawn()
    session = HookSession(
        runner=_runner(spawn), phases={}, run_id="r", tags={"demo": (("premium",),)}
    )
    run_all(_eff(), [free], _lease, hooks=session)
    assert spawn.ran == []
    run_all(_eff(), [premium], _lease, hooks=session)
    assert spawn.ran == ["grant", "revoke"]


def test_a_scenario_s_own_hook_may_not_have_an_empty_when() -> None:
    with pytest.raises(ValidationError, match="non-empty `tags` or `exclude`"):
        Scenario.model_validate(
            {"name": "x", "steps": [], "hooks": {"beforeScenario": [{"cmd": "c", "when": {}}]}}
        )


# --- report ---


def test_the_report_labels_a_when_skip_with_its_condition() -> None:
    result = _runner(_Spawn()).run_list(
        "afterScenario",
        [_entry("w", when={"tags": ["db"]}), _entry("o", on="failure")],
        _ctx(["smoke"], verdict="success"),
    )
    (block,) = report_hook_phases(hc.hook_record_dicts(result.records))
    page = html_report("r", [], hook_phases=[block])
    assert "skipped (when) — tags: [db]" in page
    assert page.count("skipped (when)") == 1  # the `on` skip keeps its own label


def test_a_bare_when_fails_to_load_rather_than_running_everywhere() -> None:
    with pytest.raises(ValidationError, match="non-empty `tags` or `exclude`"):
        _entry("x", when=None)


def test_a_scenario_s_own_skip_is_recorded_under_the_scenario_s_source() -> None:
    gate = CommandGate(scenario_source="uploaded")
    _runner(_Spawn(), gate).run_list(
        "beforeScenario",
        [_entry("c", when={"tags": ["premium"]})],
        replace(_ctx(["free"]), level="_scenario"),
    )
    (record,) = gate.records
    assert record["field"] == "scenario.hooks.beforeScenario[0].cmd"
    assert record["source"] == "uploaded"


def test_a_when_skip_carries_the_scenario_index_the_report_sorts_on() -> None:
    result = _runner(_Spawn()).run_list(
        "beforeScenario",
        [_entry("c", when={"tags": ["db"]})],
        replace(_ctx(["smoke"]), scenario_index=3),
    )
    assert result.records[0].scenario == 3
