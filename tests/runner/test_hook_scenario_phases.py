"""The scenario-level hook phases and the `${hooks.<name>.<key>}` token (BE-0491).

Driven through `run_all` over the fake backend with real shell scripts as the hook commands, so the
order against the device lease, the verdict folding, `inheritSetup`, and the token substitution are
checked on the deterministic run path rather than on the runner in isolation.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from _runner import _eff, _fake_driver, _lease

from bajutsu.common.config import HookCommand, HookPhases
from bajutsu.common.runner.hook_commands import HookRunner, raw_root
from bajutsu.common.runner.hook_session import HookSession, phases_of
from bajutsu.common.runner.pipeline import run_all
from bajutsu.common.scenario import Scenario


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _session(tmp_path: Path, phases: HookPhases) -> tuple[HookSession, object]:
    eff = replace(_eff(), hooks=phases, base_dir=tmp_path)
    session = HookSession(
        runner=HookRunner(raw_dir=raw_root(tmp_path / "runs", "r1"), base_dir=tmp_path),
        phases=phases_of({"demo": eff}),
        run_id="r1",
    )
    return session, eff


def _scenario(**kw: object) -> Scenario:
    return Scenario.model_validate({"name": "demo", "steps": [{"tap": {"id": "ok"}}], **kw})


def test_before_scenario_runs_once_per_scenario_ahead_of_the_lease(tmp_path: Path) -> None:
    # The app has to read seeded data at startup, so the entries run before the device lease.
    order = tmp_path / "order.txt"
    phases = HookPhases(
        before_scenario=[
            HookCommand(name="user", cmd=_script(tmp_path, "mk.sh", f"echo before >> {order}"))
        ]
    )
    session, eff = _session(tmp_path, phases)

    def lease(e: object, s: object) -> object:
        order.open("a").write("lease\n")
        return _lease(e, s)  # type: ignore[arg-type]

    results = run_all(eff, [_scenario(), _scenario()], lease, hooks=session)  # type: ignore[arg-type]
    assert [r.ok for r in results] == [True, True]
    assert order.read_text().split() == ["before", "lease", "before", "lease"]


def test_a_failed_before_scenario_fails_the_scenario_without_leasing(tmp_path: Path) -> None:
    phases = HookPhases(before_scenario=[HookCommand(cmd=_script(tmp_path, "bad.sh", "exit 7"))])
    session, eff = _session(tmp_path, phases)

    def lease(e: object, s: object) -> object:
        raise AssertionError("a scenario whose beforeScenario failed leased a device")

    [result] = run_all(eff, [_scenario()], lease, hooks=session)  # type: ignore[arg-type]
    assert not result.ok
    assert result.failure is not None and result.failure.startswith("beforeScenario:")
    assert "exited 7" in result.failure
    assert result.steps == []


def test_after_scenario_sees_the_verdict_and_folds_its_failure_in(tmp_path: Path) -> None:
    # The rule a scenario's own `cleanup` follows: a teardown failure becomes the verdict on a
    # passing scenario.
    seen = tmp_path / "verdict.txt"
    phases = HookPhases(
        after_scenario=[
            HookCommand(cmd=_script(tmp_path, "v.sh", f'echo "$BAJUTSU_VERDICT" > {seen}')),
            HookCommand(cmd=_script(tmp_path, "boom.sh", "exit 2")),
        ]
    )
    session, eff = _session(tmp_path, phases)
    [result] = run_all(eff, [_scenario()], _lease, hooks=session)
    assert seen.read_text().strip() == "success"  # the steps passed
    assert not result.ok  # the teardown failure became the verdict
    assert result.failure is not None and "exited 2" in result.failure


def test_a_failed_scenario_keeps_its_own_failure_ahead_of_the_teardown_s(tmp_path: Path) -> None:
    phases = HookPhases(
        after_scenario=[HookCommand(cmd=_script(tmp_path, "boom2.sh", "exit 3"))]
    )
    session, eff = _session(tmp_path, phases)
    # A step that cannot pass on the fake screen, so the scenario fails on its own first.
    scenario = Scenario.model_validate(
        {"name": "demo", "steps": [{"tap": {"id": "missing"}}]}
    )
    [result] = run_all(eff, [scenario], _lease, hooks=session)
    assert not result.ok and result.failure is not None
    assert "exited 3" in result.failure  # appended behind the step's own failure
    assert not result.failure.startswith("afterScenario")


def test_inherit_setup_false_skips_both_scenario_lists(tmp_path: Path) -> None:
    # The two form a pair: an afterScenario script tears down what a beforeScenario script created,
    # so running it alone would act on outputs that were never written.
    ran = tmp_path / "ran.txt"
    phases = HookPhases(
        before_scenario=[HookCommand(cmd=_script(tmp_path, "b.sh", f"echo b >> {ran}"))],
        after_scenario=[HookCommand(cmd=_script(tmp_path, "a.sh", f"echo a >> {ran}"))],
    )
    session, eff = _session(tmp_path, phases)
    scenario = _scenario(preconditions={"inheritSetup": False})
    [result] = run_all(eff, [scenario], _lease, hooks=session)
    assert result.ok
    assert not ran.exists()


def test_a_scenario_reads_a_hook_output_through_the_token(tmp_path: Path) -> None:
    # `${hooks.<name>.<key>}` substitutes at action time, as `${secrets.*}` does.
    phases = HookPhases(
        before_scenario=[
            HookCommand(
                name="user",
                cmd=_script(tmp_path, "u.sh", 'echo "userId=u-42" > $BAJUTSU_OUTPUT'),
            )
        ]
    )
    session, eff = _session(tmp_path, phases)
    scenario = Scenario.model_validate(
        {
            "name": "demo",
            "steps": [{"type": {"text": "${hooks.user.userId}", "into": {"id": "ok"}}}],
        }
    )
    driver = _fake_driver()

    def lease(e: object, s: object) -> object:
        held = _lease(e, s)  # type: ignore[arg-type]
        return replace(held, driver=driver)

    [result] = run_all(eff, [scenario], lease, hooks=session)  # type: ignore[arg-type]
    assert result.ok, result.failure
    # The fake driver logs what it was asked to type, so the substituted value is observable.
    assert ("type", "u-42") in driver.actions  # type: ignore[attr-defined]


def test_a_key_the_command_never_wrote_fails_the_scenario_naming_both(tmp_path: Path) -> None:
    phases = HookPhases(
        before_scenario=[
            HookCommand(name="user", cmd=_script(tmp_path, "u2.sh", 'echo "id=1" > $BAJUTSU_OUTPUT'))
        ]
    )
    session, eff = _session(tmp_path, phases)
    scenario = Scenario.model_validate(
        {
            "name": "demo",
            "steps": [{"type": {"text": "${hooks.user.email}", "into": {"id": "ok"}}}],
        }
    )
    [result] = run_all(eff, [scenario], _lease, hooks=session)
    assert not result.ok and result.failure is not None
    assert "'user'" in result.failure and "'email'" in result.failure


def test_one_scenario_s_outputs_do_not_leak_into_the_next(tmp_path: Path) -> None:
    # Each scenario's lists start from a copy of the run-level outputs, so a value one scenario's
    # hook wrote is not visible to another's.
    seen = tmp_path / "seen.txt"
    phases = HookPhases(
        before_scenario=[
            HookCommand(
                name="user",
                cmd=_script(
                    tmp_path,
                    "u3.sh",
                    f'echo "prev=[$BAJUTSU_HOOK__user__id]" >> {seen}; '
                    'echo "id=fresh" > $BAJUTSU_OUTPUT',
                ),
            )
        ]
    )
    session, eff = _session(tmp_path, phases)
    run_all(eff, [_scenario(), _scenario()], _lease, hooks=session)
    assert seen.read_text().split() == ["prev=[]", "prev=[]"]
