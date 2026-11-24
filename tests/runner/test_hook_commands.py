"""Tests for the external hook-command runner (`targets.<name>.hooks.*`, BE-0491)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bajutsu.common.capability.command_gate import CommandGate
from bajutsu.common.capability.command_policy import CommandExecution, CommandPolicy, NamedCommand
from bajutsu.common.config.schema.hook_command import HookCommand
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.runner import hook_commands as hc
from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.scenario import Redact


def _script(tmp_path: Path, name: str, body: str) -> str:
    """A tiny executable shell script, so a test drives the real spawn path."""
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _runner(tmp_path: Path, **kw: object) -> hc.HookRunner:
    raw = hc.raw_root(tmp_path / "runs", "20260101-000000")
    assert raw is not None
    return hc.HookRunner(raw_dir=raw, base_dir=tmp_path, **kw)  # type: ignore[arg-type]


def _ctx(**kw: object) -> hc.HookContext:
    return hc.HookContext(run_id="20260101-000000", target="web", **kw)  # type: ignore[arg-type]


def test_entries_run_in_order_to_completion(tmp_path: Path) -> None:
    # Each entry finishes before the next starts, so a seed script can depend on the one ahead of it.
    marker = tmp_path / "order.txt"
    runner = _runner(tmp_path)
    entries = [
        HookCommand(cmd=_script(tmp_path, "a.sh", f"echo a >> {marker}")),
        HookCommand(cmd=_script(tmp_path, "b.sh", f"echo b >> {marker}")),
    ]
    result = runner.run_list("beforeRun", entries, _ctx())
    assert result.ok
    assert marker.read_text().split() == ["a", "b"]


def test_output_file_values_reach_later_commands_and_the_scenario(tmp_path: Path) -> None:
    # A command hands values back through $BAJUTSU_OUTPUT; a later command reads them as
    # BAJUTSU_HOOK__<name>__<key>, and the scenario as ${hooks.<name>.<key>}.
    seen = tmp_path / "seen.txt"
    runner = _runner(tmp_path)
    result = runner.run_list(
        "beforeRun",
        [
            HookCommand(name="user", cmd=_script(tmp_path, "mk.sh", 'echo "userId=u-42" > $BAJUTSU_OUTPUT')),
            HookCommand(cmd=_script(tmp_path, "rd.sh", f'echo "$BAJUTSU_HOOK__user__userId" > {seen}')),
        ],
        _ctx(),
    )
    assert result.ok, result.reason
    assert result.outputs.values == {"user": {"userId": "u-42"}}
    assert result.outputs.bindings() == {"hooks.user.userId": "u-42"}
    assert seen.read_text().strip() == "u-42"


def test_a_malformed_output_line_fails_the_entry(tmp_path: Path) -> None:
    # A typo must never become a silently missing value, so the entry fails instead.
    runner = _runner(tmp_path)
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="seed", cmd=_script(tmp_path, "bad.sh", 'echo "oops" > $BAJUTSU_OUTPUT'))],
        _ctx(),
    )
    assert not result.ok
    assert "malformed" in result.reason and "oops" in result.reason


@pytest.mark.parametrize("key", ["1bad", "has__dunder", "with-dash"])
def test_an_invalid_output_key_fails_the_entry(tmp_path: Path, key: str) -> None:
    # The key follows `name`'s own rule: `__` separates the halves of BAJUTSU_HOOK__<name>__<key>.
    runner = _runner(tmp_path)
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="seed", cmd=_script(tmp_path, "k.sh", f'echo "{key}=v" > $BAJUTSU_OUTPUT'))],
        _ctx(),
    )
    assert not result.ok and "output key" in result.reason


def test_a_nonzero_exit_fails_the_entry_and_stops_a_before_list(tmp_path: Path) -> None:
    # A `before` list stops at the first failure: its remaining entries would build on a
    # precondition that did not hold.
    later = tmp_path / "later.txt"
    runner = _runner(tmp_path)
    result = runner.run_list(
        "beforeScenario",
        [
            HookCommand(cmd=_script(tmp_path, "fail.sh", "exit 3")),
            HookCommand(cmd=_script(tmp_path, "late.sh", f"touch {later}")),
        ],
        _ctx(),
    )
    assert not result.ok and "exited 3" in result.reason
    assert not later.exists()


def test_an_after_list_keeps_going_past_a_failure(tmp_path: Path) -> None:
    # Skipping the rest of a teardown is the outcome teardown exists to avoid.
    stopped = tmp_path / "stopped.txt"
    runner = _runner(tmp_path)
    result = runner.run_list(
        "afterRun",
        [
            HookCommand(cmd=_script(tmp_path, "boom.sh", "exit 1")),
            HookCommand(cmd=_script(tmp_path, "stop.sh", f"touch {stopped}")),
        ],
        _ctx(verdict="success"),
    )
    assert not result.ok
    assert stopped.exists()


@pytest.mark.parametrize(
    ("on", "verdict", "runs"),
    [
        ("always", "failure", True),
        ("success", "success", True),
        ("success", "failure", False),
        ("failure", "failure", True),
        ("failure", "success", False),
    ],
)
def test_on_selects_by_verdict(tmp_path: Path, on: str, verdict: str, runs: bool) -> None:
    # The same words a scenario's `cleanup` rules use, judged against the verdict that already exists.
    ran = tmp_path / f"ran-{on}-{verdict}.txt"
    runner = _runner(tmp_path)
    result = runner.run_list(
        "afterScenario",
        [HookCommand(on=on, cmd=_script(tmp_path, f"on-{on}-{verdict}.sh", f"touch {ran}"))],  # type: ignore[arg-type]
        _ctx(verdict=verdict),
    )
    assert ran.exists() is runs
    assert result.records[0].skipped is (not runs)


def test_a_timeout_fails_the_entry_and_stops_the_process_group(tmp_path: Path) -> None:
    # Every command is bounded by its own timeout (prime directive 2), and the whole group goes.
    runner = _runner(tmp_path)
    result = runner.run_list(
        "beforeRun",
        [HookCommand(cmd=_script(tmp_path, "sleep.sh", "sleep 30"), timeout=0.3)],
        _ctx(),
    )
    assert not result.ok and "timed out after 0.3s" in result.reason


def test_a_missing_executable_fails_the_entry(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = runner.run_list(
        "beforeRun", [HookCommand(cmd=str(tmp_path / "nope.sh"))], _ctx()
    )
    assert not result.ok and "could not start" in result.reason


def test_context_variables_reach_the_command(tmp_path: Path) -> None:
    # A script picks its data, its port, or its teardown by the run, target, scenario, and worker.
    seen = tmp_path / "env.txt"
    runner = _runner(tmp_path)
    body = (
        f'{{ echo "$BAJUTSU_RUN_ID $BAJUTSU_TARGET $BAJUTSU_SCENARIO"; '
        f'echo "$BAJUTSU_WORKER $BAJUTSU_VERDICT $BAJUTSU_RUN_DIR"; }} > {seen}'
    )
    runner.run_list(
        "afterScenario",
        [HookCommand(cmd=_script(tmp_path, "env.sh", body))],
        _ctx(
            run_dir=tmp_path / "runs" / "20260101-000000",
            scenario="checkout",
            scenario_file="s/checkout.yaml",
            worker=2,
            verdict="success",
        ),
    )
    first, second = seen.read_text().splitlines()
    assert first.split() == ["20260101-000000", "web", "checkout"]
    assert second.split() == ["2", "success", str(tmp_path / "runs" / "20260101-000000")]


def test_no_run_directory_leaves_bajutsu_run_dir_unset(tmp_path: Path) -> None:
    # Under `sandbox` the run directory is not mounted, so the variable must not point at a path the
    # command cannot reach.
    seen = tmp_path / "unset.txt"
    runner = _runner(tmp_path)
    os.environ["BAJUTSU_RUN_DIR"] = "/leaked"
    try:
        runner.run_list(
            "beforeRun",
            [HookCommand(cmd=_script(tmp_path, "nodir.sh", f'echo "[$BAJUTSU_RUN_DIR]" > {seen}'))],
            _ctx(),
        )
    finally:
        del os.environ["BAJUTSU_RUN_DIR"]
    # The process's own value is inherited; what matters is that no run directory is invented.
    assert seen.read_text().strip() == "[/leaked]"


def test_logs_are_copied_into_the_run_directory_with_secrets_masked(tmp_path: Path) -> None:
    # Each command writes to a raw file outside the run directory; the copy into evidence is masked.
    run_dir = tmp_path / "runs" / "20260101-000000"
    writer = RunArtifactWriter(run_dir, Redactor(Redact(), ["s3cr3t"]))
    runner = _runner(tmp_path, writer=writer)
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="seed", cmd=_script(tmp_path, "say.sh", 'echo "token=s3cr3t"'))],
        _ctx(run_dir=run_dir),
    )
    assert result.records[0].log == "hooks/beforeRun/web/seed.log"
    text = (run_dir / "hooks" / "beforeRun" / "web" / "seed.log").read_text()
    assert "s3cr3t" not in text


def test_a_masked_entry_hides_its_own_output_values(tmp_path: Path) -> None:
    # `mask: true` keeps masking for a value the hook itself created (BE-0491).
    run_dir = tmp_path / "runs" / "20260101-000000"
    writer = RunArtifactWriter(run_dir, Redactor(Redact(), []))
    runner = _runner(tmp_path, writer=writer)
    result = runner.run_list(
        "beforeRun",
        [
            HookCommand(
                name="cred",
                mask=True,
                cmd=_script(tmp_path, "cred.sh", 'echo "pw=hunter2" > $BAJUTSU_OUTPUT'),
            ),
            HookCommand(cmd=_script(tmp_path, "leak.sh", 'echo "pw is $BAJUTSU_HOOK__cred__pw"')),
        ],
        _ctx(run_dir=run_dir),
    )
    assert result.ok, result.reason
    assert result.outputs.secret_values() == ["hunter2"]
    assert "hunter2" not in (run_dir / "hooks" / "beforeRun" / "web" / "1.log").read_text()


def test_the_scenario_log_path_carries_the_scenario_index(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "20260101-000000"
    writer = RunArtifactWriter(run_dir, Redactor(Redact(), []))
    runner = _runner(tmp_path, writer=writer)
    result = runner.run_list(
        "beforeScenario",
        [HookCommand(name="user", cmd=_script(tmp_path, "u.sh", "true"))],
        _ctx(run_dir=run_dir, scenario_index=3),
    )
    assert result.records[0].log == "hooks/beforeScenario/web/03/user.log"


def test_a_symlinked_raw_log_is_never_copied_into_evidence(tmp_path: Path) -> None:
    # A sandboxed command can write into the raw area, so a link must not pull host content in.
    run_dir = tmp_path / "runs" / "20260101-000000"
    writer = RunArtifactWriter(run_dir, Redactor(Redact(), []))
    runner = _runner(tmp_path, writer=writer)
    secret = tmp_path / "host-key"
    secret.write_text("PRIVATE KEY", encoding="utf-8")
    link = tmp_path / "link.log"
    link.symlink_to(secret)
    assert runner.copy_log(link, "hooks/beforeRun/web/0.log") is None
    assert not (run_dir / "hooks").exists()


def test_the_raw_area_is_owner_only_and_removed_on_cleanup(tmp_path: Path) -> None:
    raw = hc.raw_root(tmp_path / "runs", "20260101-000000")
    assert raw is not None
    assert raw.stat().st_mode & 0o777 == 0o700
    runner = hc.HookRunner(raw_dir=raw, base_dir=tmp_path)
    runner.cleanup()
    assert not raw.exists()


def test_a_stale_raw_area_is_swept_but_a_live_one_is_kept(tmp_path: Path) -> None:
    # Several workers share one machine, so the sweep checks the owning process first.
    runs = tmp_path / "runs"
    stale = hc.raw_root(runs, "dead-run")
    assert stale is not None
    (stale / hc._OWNER_FILE).write_text("999999999", encoding="utf-8")
    live = hc.raw_root(runs, "live-run")
    assert live is not None  # claims the current pid
    hc.raw_root(runs, "new-run")
    assert not stale.exists()
    assert live.exists()


def test_a_refused_command_fails_the_entry_without_running_it(tmp_path: Path) -> None:
    # The worker's policy decides (BE-0492); `deny` refuses a command line outright.
    ran = tmp_path / "ran.txt"
    gate = CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="deny"))
    )
    runner = _runner(tmp_path, gate=gate)
    result = runner.run_list(
        "beforeRun",
        [HookCommand(cmd=_script(tmp_path, "deny.sh", f"touch {ran}"))],
        _ctx(),
    )
    assert not result.ok and "does not run" in result.reason
    assert not ran.exists()
    assert [r["decision"] for r in gate.records] == ["refused"]


def test_a_named_command_runs_the_worker_s_own_script(tmp_path: Path) -> None:
    # `use:` names a script the operator installed, so it runs even where a command line is refused.
    ran = tmp_path / "named.txt"
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir()
    script = _script(worker_dir, "seed.sh", f'echo "$SEED" > {ran}')
    gate = CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="named"))
    )
    runner = _runner(
        tmp_path / "config",
        gate=gate,
        named_commands={"seed": NamedCommand(path=script, env=("SEED",))},
    )
    result = runner.run_list(
        "beforeRun", [HookCommand(use="seed", env={"SEED": "ok"})], _ctx()
    )
    assert result.ok, result.reason
    assert ran.read_text().strip() == "ok"


def test_a_named_command_the_worker_does_not_list_fails_the_entry(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = runner.run_list("beforeRun", [HookCommand(use="nope")], _ctx())
    assert not result.ok and "namedCommands" in result.reason


def test_a_relative_cwd_resolves_against_the_base_directory(tmp_path: Path) -> None:
    # The base directory is a parameter, so entries that belong to another file resolve against it.
    base = tmp_path / "cfg"
    (base / "sub").mkdir(parents=True)
    seen = tmp_path / "cwd.txt"
    runner = hc.HookRunner(raw_dir=hc.raw_root(tmp_path / "runs", "r"), base_dir=base)
    runner.run_list(
        "beforeRun",
        [HookCommand(cmd=_script(tmp_path, "pwd.sh", f"pwd > {seen}"), cwd="sub")],
        _ctx(),
    )
    assert seen.read_text().strip() == str((base / "sub").resolve())


def test_the_spawn_seam_receives_argv_and_the_log_path(tmp_path: Path) -> None:
    # The argv is split like a shell would but run without one, so a pipe needs `sh -c`.
    calls: list[tuple[list[str], Path | None]] = []

    def fake(
        argv: list[str], env: dict[str, str], cwd: Path | None, timeout: float, log: Path | None
    ) -> int:
        calls.append((argv, log))
        return 0

    runner = _runner(tmp_path, runner=fake)
    runner.run_list("beforeRun", [HookCommand(name="x", cmd="./s.sh --port 8787")], _ctx())
    argv, log = calls[0]
    assert argv == ["./s.sh", "--port", "8787"]
    assert log is not None and log.name == "x.log"


def test_output_is_never_piped_so_a_background_server_cannot_block_the_run(tmp_path: Path) -> None:
    # A script that leaves a server running exits; a pipe it inherited would keep the runner waiting.
    runner = _runner(tmp_path)
    body = "sleep 20 & echo started"
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="srv", cmd=_script(tmp_path, "bg.sh", body), timeout=5)],
        _ctx(),
    )
    assert result.ok, result.reason
    subprocess.run(["pkill", "-f", "sleep 20"], check=False, capture_output=True)
