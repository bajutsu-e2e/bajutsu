"""The scenario-level hook phases and the `${hooks.<name>.<key>}` token (BE-0491).

Driven through `run_all` over the fake backend with real shell scripts as the hook commands, so the
order against the device lease, the verdict folding, `inheritSetup`, and the token substitution are
checked on the deterministic run path rather than on the runner in isolation.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from _runner import _eff, _fake_driver, _lease

from bajutsu.common.config import Effective, HookCommand, HookPhases
from bajutsu.common.runner.hook_commands import HookRunner, raw_root
from bajutsu.common.runner.hook_session import HookSession, phases_of, start_session
from bajutsu.common.runner.pipeline import run_all
from bajutsu.common.scenario import Scenario


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _session(tmp_path: Path, phases: HookPhases) -> tuple[HookSession, Effective]:
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
    phases = HookPhases(after_scenario=[HookCommand(cmd=_script(tmp_path, "boom2.sh", "exit 3"))])
    session, eff = _session(tmp_path, phases)
    # A step that cannot pass on the fake screen, so the scenario fails on its own first.
    scenario = Scenario.model_validate({"name": "demo", "steps": [{"tap": {"id": "missing"}}]})
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
            HookCommand(
                name="user", cmd=_script(tmp_path, "u2.sh", 'echo "id=1" > $BAJUTSU_OUTPUT')
            )
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


def test_after_scenario_reads_what_before_scenario_wrote(tmp_path: Path) -> None:
    # The pair's whole point: a teardown script deletes the record its own counterpart created.
    seen = tmp_path / "torn.txt"
    phases = HookPhases(
        before_scenario=[
            HookCommand(
                name="user", cmd=_script(tmp_path, "mk2.sh", 'echo "userId=u-9" > $BAJUTSU_OUTPUT')
            )
        ],
        after_scenario=[
            HookCommand(
                cmd=_script(tmp_path, "rm.sh", f'echo "$BAJUTSU_HOOK__user__userId" > {seen}')
            )
        ],
    )
    session, eff = _session(tmp_path, phases)
    [result] = run_all(eff, [_scenario()], _lease, hooks=session)
    assert result.ok, result.failure
    assert seen.read_text().strip() == "u-9"


def test_a_before_scenario_failure_is_not_prefixed_twice(tmp_path: Path) -> None:
    phases = HookPhases(
        before_scenario=[HookCommand(name="seed", cmd=_script(tmp_path, "b2.sh", "exit 1"))]
    )
    session, eff = _session(tmp_path, phases)
    [result] = run_all(eff, [_scenario()], _lease, hooks=session)
    assert result.failure == "beforeScenario: seed: exited 1"


def test_a_scenario_the_worker_cannot_run_seeds_nothing(tmp_path: Path) -> None:
    # It never runs, so it must not seed data and then tear it down.
    ran = tmp_path / "seeded.txt"
    phases = HookPhases(
        before_scenario=[HookCommand(cmd=_script(tmp_path, "b3.sh", f"touch {ran}"))],
        after_scenario=[HookCommand(cmd=_script(tmp_path, "a3.sh", f"touch {ran}"))],
    )
    session, eff = _session(tmp_path, phases)
    [result] = run_all(
        eff, [_scenario()], _lease, hooks=session, worker_failures={0: "unsupported here"}
    )
    assert not result.ok and result.failure == "unsupported here"
    assert not ran.exists()


def test_a_cancelled_run_seeds_nothing_either(tmp_path: Path) -> None:
    ran = tmp_path / "seeded2.txt"
    phases = HookPhases(
        before_scenario=[HookCommand(cmd=_script(tmp_path, "b4.sh", f"touch {ran}"))]
    )
    session, eff = _session(tmp_path, phases)
    [result] = run_all(eff, [_scenario()], _lease, hooks=session, cancelled=lambda: True)
    assert not result.ok
    assert not ran.exists()


def test_a_masked_hook_value_is_masked_in_the_scenario_s_own_result(tmp_path: Path) -> None:
    # `mask: true` is a secret of this scenario's evidence too, not only of the hook's own log.
    phases = HookPhases(
        before_scenario=[
            HookCommand(
                name="cred",
                mask=True,
                cmd=_script(tmp_path, "c.sh", 'echo "pw=sekret99" > $BAJUTSU_OUTPUT'),
            )
        ]
    )
    session, eff = _session(tmp_path, phases)
    # A step that fails and quotes the value back, so the masking is observable in the result.
    scenario = Scenario.model_validate({"name": "demo", "steps": [{"tap": {"id": "sekret99"}}]})
    [result] = run_all(eff, [scenario], _lease, hooks=session)
    assert not result.ok and result.failure is not None
    assert "sekret99" not in result.failure


def test_a_before_run_failure_is_not_prefixed_twice(tmp_path: Path) -> None:
    # `HookFailure` already names the list and the entry; the run-level path must not say it again.
    phases = HookPhases(
        before_run=[HookCommand(name="srv", cmd=_script(tmp_path, "r.sh", "exit 4"))]
    )
    session, _eff_unused = _session(tmp_path, phases)
    result = session.before_run()
    assert not result.ok
    assert result.reason == "beforeRun: srv: exited 4"


def test_the_report_renders_each_hook_phase_as_its_own_block(tmp_path: Path) -> None:
    # A phase is its own block, apart from the scenario's numbered steps, the way `setup` and
    # `cleanup` already are, and each entry links the masked log Bajutsu copied into evidence.
    import json

    from bajutsu.common.capability.route_tokens import HOOKS_KEY, report_hook_phases
    from bajutsu.common.evidence.redaction import Redactor
    from bajutsu.common.evidence.sink import RunArtifactWriter
    from bajutsu.common.runner.pipeline import run_and_report
    from bajutsu.common.scenario import Redact

    run_dir = tmp_path / "runs" / "r1"
    phases = HookPhases(
        before_run=[
            HookCommand(
                name="srv", cmd=_script(tmp_path, "s1.sh", 'echo "port=8787" > $BAJUTSU_OUTPUT')
            )
        ],
        before_scenario=[HookCommand(name="user", cmd=_script(tmp_path, "s2.sh", "echo seeding"))],
        after_run=[HookCommand(cmd=_script(tmp_path, "s3.sh", "echo stopping"))],
    )
    eff = replace(_eff(), hooks=phases, base_dir=tmp_path)
    session = HookSession(
        runner=HookRunner(
            raw_dir=raw_root(tmp_path / "runs", "r1"),
            base_dir=tmp_path,
            writer=RunArtifactWriter(run_dir, Redactor(Redact(), [])),
        ),
        phases=phases_of({"demo": eff}),
        run_id="r1",
        run_dir=run_dir,
    )
    assert session.before_run().ok
    results, manifest = run_and_report(
        eff, [_scenario()], _lease, tmp_path / "runs", "r1", hooks=session
    )
    assert [r.ok for r in results] == [True]
    records = json.loads(manifest.read_text(encoding="utf-8"))["provenance"][HOOKS_KEY]
    assert [r["list"] for r in records] == ["beforeRun", "beforeScenario", "afterRun"]
    blocks = report_hook_phases(records)
    assert [b["list"] for b in blocks] == ["beforeRun", "beforeScenario", "afterRun"]
    assert all(b["ok"] for b in blocks)
    # Every entry links its own log, and the log is really there, masked, under the run directory.
    entries = [e for b in blocks for e in cast("list[dict[str, Any]]", b["entries"])]
    logs = [e["log"] for e in entries]
    assert logs == [
        "hooks/beforeRun/demo/srv.log",
        "hooks/beforeScenario/demo/00/user.log",
        "hooks/afterRun/demo/0.log",
    ]
    assert (
        run_dir / "hooks" / "beforeScenario" / "demo" / "00" / "user.log"
    ).read_text().strip() == ("seeding")


def test_a_failed_hook_marks_its_block_and_names_the_reason() -> None:
    from bajutsu.common.capability.route_tokens import report_hook_phases

    [block] = report_hook_phases(
        [{"list": "afterRun", "entry": "stop", "target": "demo", "failure": "exited 1"}]
    )
    assert block["ok"] is False
    entries = cast("list[dict[str, Any]]", block["entries"])
    assert entries[0]["reason"] == "exited 1"


def test_an_older_manifest_renders_no_hook_block() -> None:
    from bajutsu.common.capability.route_tokens import report_hook_phases

    assert report_hook_phases(None) == []
    assert report_hook_phases("not a list") == []


def test_the_raw_area_and_run_directory_are_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A command may run in a working directory of its own, where a relative runs directory would
    # name a path it cannot reach — `BAJUTSU_OUTPUT` then fails to open.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    seen = tmp_path / "out.txt"
    phases = HookPhases(
        before_run=[
            HookCommand(
                name="seed",
                cwd="sub",
                cmd=_script(
                    tmp_path,
                    "rel.sh",
                    f'echo "$BAJUTSU_OUTPUT" > {seen}; echo "k=v" > "$BAJUTSU_OUTPUT"',
                ),
            )
        ]
    )
    eff = replace(_eff(), hooks=phases, base_dir=tmp_path)
    session = start_session(
        {"demo": eff},
        run_id="r1",
        runs_dir=Path("runs"),
        run_dir=Path("runs/r1"),
        base_dir=tmp_path,
    )
    assert session is not None
    assert session.run_dir is not None and session.run_dir.is_absolute()
    result = session.before_run()
    assert result.ok, result.reason
    assert Path(seen.read_text().strip()).is_absolute()
    assert result.outputs.values["seed"] == {"k": "v"}


def test_a_scenario_that_never_runs_still_reports_its_progress_line(tmp_path: Path) -> None:
    lines: list[str] = []
    phases = HookPhases(before_scenario=[HookCommand(cmd=_script(tmp_path, "np.sh", "true"))])
    session, eff = _session(tmp_path, phases)
    run_all(
        eff,
        [_scenario()],
        _lease,
        hooks=session,
        progress=lines.append,
        worker_failures={0: "unsupported here"},
    )
    run_all(
        eff, [_scenario()], _lease, hooks=session, progress=lines.append, cancelled=lambda: True
    )
    assert any("unsupported on this worker" in line for line in lines)
    assert any("cancelled" in line for line in lines)


def test_a_cancelled_run_bounds_its_scenario_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The teardown deadline is the slice of the grace window a scenario's own `cleanup` uses, so a
    # cancelled run does not wait one more `timeout` per entry. The cancel has to land *during* the
    # scenario: one that arrives first returns at `_never_runs`, before any hook runs at all.
    import time as _time

    from bajutsu.common import cancellation

    monkeypatch.setattr(cancellation, "grace_seconds", lambda: 1.0)
    phases = HookPhases(
        after_scenario=[HookCommand(cmd=_script(tmp_path, "slow2.sh", "sleep 30"), timeout=300)]
    )
    session, eff = _session(tmp_path, phases)
    landed = {"yet": False}

    def cancelled() -> bool:
        return landed["yet"]

    def lease(e: object, s: object) -> object:
        landed["yet"] = True  # the cancel arrives once the scenario is already under way
        return _lease(e, s)  # type: ignore[arg-type]

    started = _time.monotonic()
    [result] = run_all(eff, [_scenario()], lease, hooks=session, cancelled=cancelled)  # type: ignore[arg-type]
    elapsed = _time.monotonic() - started
    assert not result.ok
    # The teardown ran and was cut short: the entry's own timeout is 300s, the grace slice a second.
    assert elapsed < 20, elapsed
    [record] = [r for r in session.records if r.list_name == "afterScenario"]
    assert record.failure is not None and "timed out" in record.failure


def test_a_skipped_and_a_failed_entry_reach_the_report_through_the_manifest(
    tmp_path: Path,
) -> None:
    # The manifest and the report meet here: rename a key on either side and the blocks lose the
    # skipped and failed rendering while every other test stays green.
    import json

    from bajutsu.common.capability.route_tokens import HOOKS_KEY, report_hook_phases
    from bajutsu.common.evidence.redaction import Redactor
    from bajutsu.common.evidence.sink import RunArtifactWriter
    from bajutsu.common.runner.pipeline import run_and_report
    from bajutsu.common.scenario import Redact

    run_dir = tmp_path / "runs" / "r1"
    phases = HookPhases(
        after_run=[
            HookCommand(name="stop", cmd=_script(tmp_path, "boom3.sh", "exit 5")),
            HookCommand(on="failure", cmd=_script(tmp_path, "never.sh", "true")),
        ]
    )
    eff = replace(_eff(), hooks=phases, base_dir=tmp_path)
    session = HookSession(
        runner=HookRunner(
            raw_dir=raw_root(tmp_path / "runs", "r1"),
            base_dir=tmp_path,
            writer=RunArtifactWriter(run_dir, Redactor(Redact(), [])),
        ),
        phases=phases_of({"demo": eff}),
        run_id="r1",
        run_dir=run_dir,
    )
    _results, manifest = run_and_report(
        eff, [_scenario()], _lease, tmp_path / "runs", "r1", hooks=session
    )
    records = json.loads(manifest.read_text(encoding="utf-8"))["provenance"][HOOKS_KEY]
    [block] = report_hook_phases(records)
    assert block["list"] == "afterRun" and block["ok"] is False
    entries = cast("list[dict[str, Any]]", block["entries"])
    assert entries[0]["reason"] == "exited 5" and entries[0]["ok"] is False
    assert entries[1]["skipped"] is True and "`on: failure`" in str(entries[1]["reason"])


def test_the_report_html_renders_the_hook_block_and_its_log_link(tmp_path: Path) -> None:
    # The template is the one place a Jinja key typo would ship silently.
    from bajutsu.common.report.html import html_report

    html = html_report(
        "r1",
        [],
        hook_phases=[
            {
                "list": "beforeRun",
                "ok": False,
                "entries": [
                    {
                        "entry": "srv",
                        "target": "demo",
                        "log": "hooks/beforeRun/demo/srv.log",
                        "skipped": False,
                        "reason": "exited 1",
                        "ok": False,
                    }
                ],
            }
        ],
    )
    assert "hook commands" in html
    assert 'href="hooks/beforeRun/demo/srv.log"' in html
    assert "demo/srv" in html and "exited 1" in html


def test_the_teardown_phase_runs_once_however_often_it_is_asked(tmp_path: Path) -> None:
    # Every real run asks twice: the pipeline before the report, then the command's own finally.
    # Running a teardown twice would undo more than its `before` entry created.
    counted = tmp_path / "count.txt"
    phases = HookPhases(
        after_run=[HookCommand(cmd=_script(tmp_path, "once.sh", f"echo x >> {counted}"))]
    )
    session, _eff_unused = _session(tmp_path, phases)
    first = session.after_run("success")
    assert session.after_run("failure") is first
    assert counted.read_text().split() == ["x"]


def _sandboxed_session(tmp_path: Path, phases: HookPhases) -> tuple[HookSession, Effective]:
    """A session whose policy sandboxes the config's commands, so `_sandboxes` builds a runtime."""
    from bajutsu.common.capability.command_gate import CommandGate
    from bajutsu.common.capability.command_policy import CommandExecution, CommandPolicy
    from bajutsu.common.runner.hook_session import start_session

    eff = replace(_eff(), hooks=phases, base_dir=tmp_path)
    gate = CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="sandbox"))
    )
    session = start_session(
        {"demo": eff},
        run_id="r1",
        runs_dir=tmp_path / "runs",
        run_dir=tmp_path / "runs" / "r1",
        gate=gate,
        base_dir=tmp_path,
    )
    assert session is not None
    return session, eff


def test_a_sandboxed_target_gets_one_container_started_and_removed(tmp_path: Path) -> None:
    # The security claim of the feature: under `sandbox` the command runs in the container, and the
    # container is removed when the run ends.
    from bajutsu.common.runner import sandbox as sandbox_mod

    phases = HookPhases(
        before_run=[HookCommand(name="srv", cmd="./start.sh")],
        after_run=[HookCommand(cmd="./stop.sh")],
        sandbox=__import__(
            "bajutsu.common.config.schema.hook_command", fromlist=["HookSandbox"]
        ).HookSandbox.model_validate({"dockerImage": "node:20-slim"}),
    )
    session, _eff_unused = _sandboxed_session(tmp_path, phases)
    [runtime] = session.sandboxes.values()
    started: list[str] = []
    removed: list[str] = []
    object.__setattr__(runtime, "run_fn", lambda argv, log=None: _AliveProc())

    def record_exec(argv: list[str]) -> None:
        (started if argv[-1] == "true" else removed).append(argv[-1])

    object.__setattr__(runtime, "exec_fn", record_exec)
    object.__setattr__(session.runner, "_run", lambda argv, env, cwd, timeout, log: 0)
    assert session.before_run().ok
    assert started  # the readiness probe ran, so the container was up before the first entry
    assert session.after_run("success").ok
    assert runtime.container in removed
    assert isinstance(runtime, sandbox_mod.SandboxRuntime)


def test_a_container_that_cannot_start_fails_the_before_run_phase(tmp_path: Path) -> None:
    # A bring-up failure is a phase failure: no scenario runs, and the teardown still gets its turn.
    from bajutsu.common.runner.sandbox import SandboxError

    phases = HookPhases(
        before_run=[HookCommand(cmd="./start.sh")],
        sandbox=__import__(
            "bajutsu.common.config.schema.hook_command", fromlist=["HookSandbox"]
        ).HookSandbox.model_validate({"dockerImage": "node:20-slim"}),
    )
    session, _eff_unused = _sandboxed_session(tmp_path, phases)
    [runtime] = session.sandboxes.values()

    def refuse(_argv: list[str], _log: object = None) -> object:
        raise SandboxError("docker: no such image")

    object.__setattr__(runtime, "run_fn", refuse)
    result = session.before_run()
    assert not result.ok
    assert "no such image" in result.reason and "demo sandbox" in result.reason


class _AliveProc:
    """A container process that stays up, so the readiness probe decides when it is ready."""

    returncode = 0

    def poll(self) -> int | None:
        return None


def test_the_teardown_backstop_tears_down_as_a_failure(tmp_path: Path) -> None:
    # Every report path runs the phase before it assembles the report, so the backstop only ever
    # executes it for a run that never finished — a device failure, an exception, a cancellation.
    # Telling it `success` there skips the `on: failure` entries that exist for exactly that case.
    from bajutsu.run import cli as run_cli

    seen = tmp_path / "verdict.txt"
    phases = HookPhases(
        after_run=[HookCommand(cmd=_script(tmp_path, "v2.sh", f'echo "$BAJUTSU_VERDICT" > {seen}'))]
    )
    session, _eff_unused = _session(tmp_path, phases)

    class _Plan:
        hooks = session
        cancelled = staticmethod(lambda: False)

    run_cli._finish_hooks(_Plan())  # type: ignore[arg-type]
    assert seen.read_text().strip() == "failure"


def test_the_backstop_does_not_run_a_teardown_the_pipeline_already_ran(tmp_path: Path) -> None:
    counted = tmp_path / "count2.txt"
    phases = HookPhases(
        after_run=[HookCommand(cmd=_script(tmp_path, "once2.sh", f"echo x >> {counted}"))]
    )
    session, _eff_unused = _session(tmp_path, phases)
    session.after_run("success")  # the pipeline's own call, before the report

    from bajutsu.run import cli as run_cli

    class _Plan:
        hooks = session
        cancelled = staticmethod(lambda: False)

    run_cli._finish_hooks(_Plan())  # type: ignore[arg-type]
    assert counted.read_text().split() == ["x"]


def test_a_hookless_run_starts_no_session(tmp_path: Path) -> None:
    # None rather than an empty session, so every call site can tell at a glance that a run without
    # hook commands does no hook work at all.
    from bajutsu.common.runner.hook_session import start_session

    eff = replace(_eff(), base_dir=tmp_path)
    assert start_session({"demo": eff}, run_id="r1", runs_dir=tmp_path / "runs") is None


def test_two_targets_sharing_a_hook_name_refuse_to_start(tmp_path: Path) -> None:
    # A `${hooks.<name>.<key>}` token could not say which command it meant, so the run fails before
    # any device work rather than resolving it arbitrarily.
    from bajutsu.common.runner.hook_session import start_session

    phases = HookPhases(before_run=[HookCommand(name="srv", cmd="true")])
    effs = {
        name: replace(_eff(), target=name, hooks=phases, base_dir=tmp_path)
        for name in ("alpha", "beta")
    }
    with pytest.raises(ValueError, match="'srv'"):
        start_session(effs, run_id="r1", runs_dir=tmp_path / "runs")


def test_a_failed_log_copy_does_not_end_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Evidence, not the verdict: a full disk must not end the run here, and must not leave the raw
    # area behind either — that area holds the unmasked output of a `mask: true` hook.
    phases = HookPhases(after_run=[HookCommand(cmd=_script(tmp_path, "t.sh", "true"))])
    session, _eff_unused = _session(tmp_path, phases)

    def refuse(_records: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(session.runner, "recopy", refuse)
    result = session.after_run("success")
    assert result.ok  # the phase itself succeeded
    assert session.finished is result  # latched, so a second call cannot re-run the teardown
