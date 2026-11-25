"""A scenario's own hook commands against its targets' (BE-0501)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from _runner import _eff, _fake_driver, _lease

from bajutsu.common.capability.command_gate import CommandGate
from bajutsu.common.capability.command_policy import CommandExecution, CommandPolicy
from bajutsu.common.config import Effective, HookCommand, HookPhases
from bajutsu.common.config.schema.hook_command import HookSandbox
from bajutsu.common.orchestrator import RunResult
from bajutsu.common.runner import scenario_hooks as sh
from bajutsu.common.runner.hook_commands import HookContext, HookRunner, run_phase
from bajutsu.common.runner.hook_session import start_session
from bajutsu.common.runner.pipeline import run_all
from bajutsu.common.scenario import Scenario
from bajutsu.common.scenario.models.hook_command import SCENARIO_LEVEL

_OWN = {"hooks": {"beforeScenario": [{"name": "card", "cmd": "./seed.sh"}]}}


def _scenario(**kw: Any) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": [{"tap": {"id": "ok"}}], **kw})


def _typing(token: str, **kw: Any) -> Scenario:
    return _scenario(steps=[{"type": {"text": token, "into": {"id": "ok"}}}], **kw)


def _effs(**phases: HookPhases) -> dict[str, Effective]:
    return {name: replace(_eff(), target=name, hooks=p) for name, p in phases.items()}


def _named(name: str) -> list[HookCommand]:
    return [HookCommand(name=name, cmd="x")]


def _context(verdict: str | None, target: str) -> HookContext:
    return HookContext(run_id="r", target=target, scenario_index=0, verdict=verdict)


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_a_scenario_reads_its_own_before_entry() -> None:
    s = _typing("${hooks.card.id}", **_OWN)
    assert sh.scenario_hook_error(s, _effs(web=HookPhases()), "web") is None


def test_a_scenario_cannot_read_its_own_after_entry() -> None:
    s = _typing("${hooks.wipe.id}", hooks={"afterScenario": [{"name": "wipe", "cmd": "x"}]})
    why = sh.scenario_hook_error(s, _effs(web=HookPhases()), "web")
    assert why is not None and "names no hook" in why


@pytest.mark.parametrize("list_name", ["before_run", "after_run", "before_scenario"])
def test_a_name_shared_with_a_driven_target_is_refused(list_name: str) -> None:
    lists: dict[str, Any] = {list_name: _named("card")}
    effs = _effs(web=HookPhases(**lists))
    why = sh.scenario_hook_error(_scenario(**_OWN), effs, "web")
    assert why is not None and "'card'" in why and "'web'" in why


def test_a_target_the_scenario_does_not_drive_may_share_the_name() -> None:
    effs = _effs(web=HookPhases(), other=HookPhases(before_run=_named("card")))
    assert sh.scenario_hook_error(_scenario(**_OWN), effs, "web") is None


def test_inherit_setup_false_hides_only_the_targets_scenario_lists() -> None:
    off = {"preconditions": {"inheritSetup": False}}
    scenario_level = _effs(web=HookPhases(before_scenario=_named("card")))
    run_level = _effs(web=HookPhases(before_run=_named("card")))
    s = _typing("${hooks.card.id}", **_OWN, **off)
    # The hidden target entry neither collides nor shadows the scenario's own.
    assert sh.scenario_hook_error(s, scenario_level, "web") is None
    assert sh.scenario_hook_error(s, run_level, "web") is not None
    # Without an own entry the token names one the scenario skips.
    why = sh.scenario_hook_error(_typing("${hooks.card.id}", **off), scenario_level, "web")
    assert why is not None and "inheritSetup" in why


@pytest.mark.parametrize(
    ("mode", "entry", "refused"),
    [
        ("allow", {"cmd": "x"}, False),
        ("named", {"cmd": "x"}, True),
        ("named", {"use": "seed"}, False),
        ("deny", {"use": "seed"}, True),
    ],
)
def test_the_worker_policy_governs_a_scenario_entry(
    mode: Any, entry: dict[str, str], refused: bool
) -> None:
    gate = CommandGate(policy=CommandPolicy("uploaded", CommandExecution(uploaded=mode)))
    s = _scenario(hooks={"beforeScenario": [entry]})
    why = sh.scenario_hook_refusal([s], _effs(web=HookPhases()), "web", gate)
    assert (why is not None) is refused
    if why is not None:
        assert "scenario 's'" in why
        assert gate.records[0]["field"] == "scenario.hooks.beforeScenario[0].cmd"


def test_a_sandboxed_scenario_entry_needs_its_first_targets_container() -> None:
    gate = CommandGate(policy=CommandPolicy("uploaded", CommandExecution(uploaded="sandbox")))
    box = HookPhases(sandbox=HookSandbox(dockerImage="img"))
    steps = [{"target": "web", "tap": {"id": "ok"}}]
    s = _scenario(targets=["web", "admin"], steps=steps, **_OWN)
    assert sh.scenario_hook_refusal([s], _effs(web=box, admin=HookPhases()), "web", gate) is None
    why = sh.scenario_hook_refusal([s], _effs(web=HookPhases(), admin=box), "web", gate)
    assert why is not None and "target 'web'" in why


def test_a_container_starts_for_a_scenario_whose_target_declares_no_command(
    tmp_path: Path,
) -> None:
    gate = CommandGate(policy=CommandPolicy("uploaded", CommandExecution(uploaded="sandbox")))
    effs = _effs(web=HookPhases(sandbox=HookSandbox(dockerImage="img")), other=HookPhases())
    session = start_session(
        effs,
        run_id="r1",
        runs_dir=tmp_path,
        gate=gate,
        base_dir=tmp_path,
        scenario_targets=sh.sandbox_targets([_scenario(**_OWN), _scenario()], "web"),
    )
    assert session is not None
    assert list(session.sandboxes) == ["web"]
    assert start_session(effs, run_id="r2", runs_dir=tmp_path, gate=gate) is None


def test_the_levels_nest_and_each_resolves_its_own_cwd(tmp_path: Path) -> None:
    order = tmp_path / "order.txt"

    def entry(label: str) -> list[HookCommand]:
        return [HookCommand(cmd=_script(tmp_path, label, f'echo "{label} $(pwd)" >> {order}'))]

    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    own = HookPhases(before_scenario=entry("own-b"), after_scenario=entry("own-a"))
    phases = sh.nested(
        {
            "web": HookPhases(before_scenario=entry("web-b"), after_scenario=entry("web-a")),
            "admin": HookPhases(before_scenario=entry("admin-b"), after_scenario=entry("admin-a")),
        },
        own,
    )
    runner = HookRunner(raw_dir=tmp_path / "raw", base_dir=tmp_path)
    for list_name, verdict in (("beforeScenario", None), ("afterScenario", "success")):
        run_phase(
            runner,
            list_name,
            phases,
            partial(_context, verdict),
            cwd_bases={SCENARIO_LEVEL: scenario_dir},
        )
    here, there = tmp_path.resolve(), scenario_dir.resolve()
    assert order.read_text().splitlines() == [
        f"web-b {here}",
        f"admin-b {here}",
        f"own-b {there}",
        f"own-a {there}",
        f"admin-a {here}",
        f"web-a {here}",
    ]


def test_a_scenario_entry_logs_and_records_apart_from_a_targets(tmp_path: Path) -> None:
    gate = CommandGate()
    seen = tmp_path / "target.txt"
    runner = HookRunner(raw_dir=tmp_path / "raw", base_dir=tmp_path, gate=gate)
    runner.run_list(
        "beforeScenario",
        [HookCommand(name="card", cmd=_script(tmp_path, "t", f"echo $BAJUTSU_TARGET > {seen}"))],
        HookContext(run_id="r", target="web", scenario_index=3, level=SCENARIO_LEVEL),
    )
    assert seen.read_text().strip() == "web"
    assert (tmp_path / "raw/logs/beforeScenario/_scenario/03/card.log").is_file()
    assert gate.records[0]["field"] == "scenario.hooks.beforeScenario[card].cmd"


# --- on the run path ---


def _run(
    tmp_path: Path, scenario: Scenario, target: HookPhases | None = None
) -> tuple[RunResult, list[str]]:
    """Run *scenario* through `run_all` with *target*'s hooks; return its result and the log order."""
    eff = replace(_eff(), hooks=target or HookPhases(), base_dir=tmp_path)
    session = start_session(
        {eff.target: eff},
        run_id="r1",
        runs_dir=tmp_path / "runs",
        base_dir=tmp_path,
        scenario_targets=sh.sandbox_targets([scenario], eff.target),
    )
    order = tmp_path / "order.txt"

    def lease(e: Any, s: Any) -> Any:
        with order.open("a") as fh:
            fh.write("lease\n")
        return _lease(e, s)

    [result] = run_all(eff, [scenario], lease, hooks=session)
    return result, order.read_text().split() if order.exists() else []


def _mark(tmp_path: Path, label: str, code: int = 0) -> str:
    return _script(tmp_path, label, f"echo {label} >> {tmp_path / 'order.txt'}; exit {code}")


def _both(tmp_path: Path, level: str, before_code: int = 0, after_code: int = 0) -> dict[str, Any]:
    return {
        "before": [HookCommand(cmd=_mark(tmp_path, f"{level}-b", before_code))],
        "after": [HookCommand(cmd=_mark(tmp_path, f"{level}-a", after_code))],
    }


def _target(tmp_path: Path, **codes: int) -> HookPhases:
    lists = _both(tmp_path, "web", **codes)
    return HookPhases(before_scenario=lists["before"], after_scenario=lists["after"])


def _own(tmp_path: Path, **codes: int) -> dict[str, Any]:
    lists = _both(tmp_path, "own", **codes)
    return {"hooks": {"beforeScenario": lists["before"], "afterScenario": lists["after"]}}


def test_a_run_nests_the_scenarios_commands_around_the_lease(tmp_path: Path) -> None:
    result, order = _run(tmp_path, _scenario(**_own(tmp_path)), _target(tmp_path))
    assert result.ok, result.failure
    assert order == ["web-b", "own-b", "lease", "own-a", "web-a"]


def test_a_target_failure_skips_both_scenario_lists(tmp_path: Path) -> None:
    result, order = _run(tmp_path, _scenario(**_own(tmp_path)), _target(tmp_path, before_code=3))
    assert not result.ok
    assert order == ["web-b", "web-a"]


def test_a_scenario_failure_skips_the_launch_and_tears_both_levels_down(tmp_path: Path) -> None:
    result, order = _run(tmp_path, _scenario(**_own(tmp_path, before_code=4)), _target(tmp_path))
    assert result.failure is not None and result.failure.startswith("beforeScenario: ")
    assert "exited 4" in result.failure
    assert order == ["web-b", "own-b", "own-a", "web-a"]


def test_a_failed_scenario_teardown_becomes_the_verdict(tmp_path: Path) -> None:
    result, order = _run(tmp_path, _scenario(**_own(tmp_path, after_code=5)), _target(tmp_path))
    assert not result.ok and result.failure is not None and "exited 5" in result.failure
    assert order[-1] == "web-a"


def test_inherit_setup_false_keeps_the_scenarios_own_commands(tmp_path: Path) -> None:
    s = _scenario(**_own(tmp_path), preconditions={"inheritSetup": False})
    result, order = _run(tmp_path, s, _target(tmp_path))
    assert result.ok, result.failure
    assert order == ["own-b", "lease", "own-a"]


def test_a_scenario_command_runs_without_any_target_command(tmp_path: Path) -> None:
    result, order = _run(tmp_path, _scenario(**_own(tmp_path)))
    assert result.ok, result.failure
    assert order == ["own-b", "lease", "own-a"]


def test_a_missing_key_still_hands_its_outputs_to_the_teardown(tmp_path: Path) -> None:
    seen = tmp_path / "seen.txt"
    s = _typing(
        "${hooks.card.number}",
        hooks={
            "beforeScenario": [
                {"name": "card", "cmd": _script(tmp_path, "mk", 'echo "id=c-1" > $BAJUTSU_OUTPUT')}
            ],
            "afterScenario": [
                {"cmd": _script(tmp_path, "rm", f"echo $BAJUTSU_HOOK__card__id > {seen}")}
            ],
        },
    )
    result, _ = _run(tmp_path, s)
    assert not result.ok and "'number'" in (result.failure or "")
    assert seen.read_text().strip() == "c-1"


def test_a_scenario_command_learns_its_file_and_runs_beside_it(tmp_path: Path) -> None:
    path = tmp_path / "scenarios" / "flow.yaml"
    path.parent.mkdir()
    seen = tmp_path / "seen.txt"
    s = _scenario(
        hooks={
            "beforeScenario": [
                {"cmd": _script(tmp_path, "p", f'echo "$BAJUTSU_SCENARIO_FILE $(pwd)" > {seen}')}
            ]
        }
    )
    s.set_source_path(path)
    result, _ = _run(tmp_path, s)
    assert result.ok, result.failure
    assert seen.read_text().split() == [str(path), str(path.parent.resolve())]


def test_a_run_without_a_hook_session_fails_instead_of_skipping_the_commands() -> None:
    [result] = run_all(_eff(), [_scenario(**_OWN)], _lease)
    assert not result.ok and "hook session" in (result.failure or "")
    assert result.steps == []


def test_an_after_list_answers_the_verdict_and_every_rule_runs(tmp_path: Path) -> None:
    def rule(label: str, on: str, code: int = 0) -> HookCommand:
        return HookCommand.model_validate({"on": on, "cmd": _mark(tmp_path, label, code)})

    s = _scenario(
        steps=[{"tap": {"id": "missing"}}],
        hooks={
            "afterScenario": [
                rule("own-fail", "failure", 6),
                rule("own-ok", "success"),
                rule("own-always", "always"),
            ]
        },
    )
    result, order = _run(tmp_path, s)
    assert order == ["lease", "own-fail", "own-always"]
    assert result.failure is not None and result.failure.endswith("exited 6")
    assert not result.failure.startswith("afterScenario")


def test_the_target_rules_answer_the_verdict_the_steps_reached(tmp_path: Path) -> None:
    # One verdict decides every rule of the phase, as a scenario's `cleanup` rules already read it.
    target = HookPhases(
        after_scenario=[
            HookCommand(on="success", cmd=_mark(tmp_path, "web-ok")),
            HookCommand(on="failure", cmd=_mark(tmp_path, "web-fail")),
        ]
    )
    result, order = _run(tmp_path, _scenario(**_own(tmp_path, after_code=5)), target)
    assert not result.ok
    assert order == ["own-b", "lease", "own-a", "web-ok"]


def test_a_lone_teardown_is_skipped_when_a_target_never_prepared(tmp_path: Path) -> None:
    s = _scenario(hooks={"afterScenario": [{"cmd": _mark(tmp_path, "own-a")}]})
    _, order = _run(tmp_path, s, _target(tmp_path, before_code=3))
    assert order == ["web-b", "web-a"]


def test_two_scenarios_may_share_a_name_without_sharing_values(tmp_path: Path) -> None:
    def seeded(body: str, script: str) -> Scenario:
        return _typing(
            "${hooks.card.id}",
            hooks={
                "beforeScenario": [{"name": "card", "cmd": _script(tmp_path, script, body)}],
            },
        )

    scenarios = [seeded('echo "id=c-1" > $BAJUTSU_OUTPUT', "one"), seeded("true", "two")]
    eff = replace(_eff(), base_dir=tmp_path)
    session = start_session(
        {eff.target: eff},
        run_id="r1",
        runs_dir=tmp_path / "runs",
        base_dir=tmp_path,
        scenario_targets={eff.target},
    )
    driver = _fake_driver()

    def lease(e: Any, s: Any) -> Any:
        return replace(_lease(e, s), driver=driver)

    first, second = run_all(eff, scenarios, lease, hooks=session)
    assert first.ok, first.failure
    assert ("type", "c-1") in driver.actions  # type: ignore[attr-defined]
    assert not second.ok and "never wrote 'id'" in (second.failure or "")


def test_a_command_refuses_a_hook_token_it_would_receive_literally() -> None:
    s = _scenario(hooks={"afterScenario": [{"cmd": "rm ${hooks.card.id}"}]})
    why = sh.scenario_hook_error(s, _effs(web=HookPhases()), "web")
    assert why is not None and "BAJUTSU_HOOK__card__id" in why


def test_a_container_starts_for_the_scenarios_source_alone(tmp_path: Path) -> None:
    execution = CommandExecution(operator="allow", uploaded="sandbox")
    gate = CommandGate(policy=CommandPolicy("operator", execution), scenario_source="uploaded")
    box = HookSandbox(dockerImage="img")
    effs = _effs(web=HookPhases(sandbox=box), other=HookPhases(sandbox=box, after_run=_named("z")))
    session = start_session(
        effs, run_id="r", runs_dir=tmp_path, gate=gate, base_dir=tmp_path, scenario_targets={"web"}
    )
    assert session is not None and list(session.sandboxes) == ["web"]


def test_relative_run_and_scenario_paths_reach_the_command_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The command runs beside its scenario, so a path relative to bajutsu's own directory is lost.
    monkeypatch.chdir(tmp_path)
    Path("flows").mkdir()
    Path("flows/a.yaml").write_text("x")
    check = 'test -f "$BAJUTSU_SCENARIO_FILE" && echo "id=ok" > $BAJUTSU_OUTPUT && echo $BAJUTSU_RUN_DIR'
    s = _typing(
        "${hooks.card.id}",
        hooks={"beforeScenario": [{"name": "card", "cmd": _script(tmp_path, "rel", check)}]},
    )
    s.set_source_path(Path("flows/a.yaml"))
    eff = replace(_eff(), base_dir=tmp_path)
    session = start_session(
        {eff.target: eff},
        run_id="r1",
        runs_dir=Path("runs"),
        run_dir=Path("runs/r1"),
        base_dir=tmp_path,
        scenario_targets={eff.target},
    )
    assert session is not None and session.run_dir == tmp_path.resolve() / "runs" / "r1"
    [result] = run_all(eff, [s], _lease, hooks=session)
    assert result.ok, result.failure


class _Box:
    """Stands in for a container: records what it was asked to run, and runs nothing."""

    def __init__(self) -> None:
        self.ran: list[tuple[list[str], Path | None]] = []

    def wrap(
        self,
        argv: list[str],
        bajutsu_env: Mapping[str, str],
        declared: Mapping[str, str],
        cwd: Path | None,
    ) -> list[str]:
        self.ran.append((argv, cwd))
        return ["true"]


def test_a_sandboxed_scenario_command_runs_in_its_first_targets_container(
    tmp_path: Path,
) -> None:
    gate = CommandGate(policy=CommandPolicy("uploaded", CommandExecution(uploaded="sandbox")))
    box = HookPhases(sandbox=HookSandbox(dockerImage="img"))
    effs = {n: replace(e, base_dir=tmp_path) for n, e in _effs(demo=box, other=box).items()}
    session = start_session(
        effs,
        run_id="r1",
        runs_dir=tmp_path / "runs",
        base_dir=tmp_path,
        gate=gate,
        scenario_targets={"demo", "other"},
    )
    assert session is not None
    boxes = {"demo": _Box(), "other": _Box()}
    session.sandboxes = boxes  # type: ignore[assignment]
    eff = replace(effs["demo"], target="demo")
    s = _scenario(**_OWN)
    s.set_source_path(tmp_path / "scenarios" / "a.yaml")
    [result] = run_all(eff, [s], _lease, hooks=session)
    assert result.ok, result.failure
    assert boxes["demo"].ran == [(["./seed.sh"], tmp_path / "scenarios")]
    assert boxes["other"].ran == []


# --- in the report ---


def test_the_report_draws_each_level_as_its_own_labeled_block(tmp_path: Path) -> None:
    from bajutsu.common.capability.route_tokens import report_hook_phases
    from bajutsu.common.runner.hook_commands import hook_record_dicts

    eff = replace(_eff(), hooks=_target(tmp_path), base_dir=tmp_path)
    session = start_session(
        {eff.target: eff}, run_id="r1", runs_dir=tmp_path / "runs", base_dir=tmp_path
    )
    assert session is not None
    run_all(eff, [_scenario(**_own(tmp_path, after_code=2))], _lease, hooks=session)
    blocks: list[Any] = report_hook_phases(hook_record_dicts(session.records))
    assert [(b["list"], b["level"], b["ok"]) for b in blocks] == [
        ("beforeScenario", "target", True),
        ("beforeScenario", "scenario", True),
        ("afterScenario", "scenario", False),
        ("afterScenario", "target", True),
    ]
    assert [b["entries"][0]["owner"] for b in blocks] == [eff.target, "s", "s", eff.target]


def test_the_rendered_report_labels_the_scenario_level() -> None:
    from bajutsu.common.capability.route_tokens import report_hook_phases
    from bajutsu.common.report.html import html_report

    phases = report_hook_phases(
        [
            {"list": "beforeScenario", "entry": "seed", "target": "web"},
            {"list": "beforeScenario", "entry": "card", "target": "web", "declaredBy": "pay"},
        ]
    )
    page = html_report("r1", [], hook_phases=phases)
    assert "beforeScenario (target)" in page and "web/seed" in page
    assert "beforeScenario (scenario)" in page and "pay/card" in page


def test_two_targets_sharing_a_hook_name_start_no_session(tmp_path: Path) -> None:
    effs = _effs(web=HookPhases(before_run=_named("a")), admin=HookPhases(after_run=_named("a")))
    with pytest.raises(ValueError, match="both declare a hook named 'a'"):
        start_session(effs, run_id="r", runs_dir=tmp_path)
