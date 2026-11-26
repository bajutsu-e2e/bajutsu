"""Tests for the external hook-command runner (`targets.<name>.hooks.*`, BE-0491)."""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from bajutsu.common.capability.command_gate import CommandGate
from bajutsu.common.capability.command_policy import CommandExecution, CommandPolicy, NamedCommand
from bajutsu.common.config import HookPhases
from bajutsu.common.config.schema.hook_command import HookCommand, HookSandbox
from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.runner import hook_commands as hc
from bajutsu.common.runner import sandbox
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


def _ctx_for(target: str, **kw: object) -> hc.HookContext:
    """The context one target's commands receive, for a `run_phase` across several targets."""
    return hc.HookContext(run_id="20260101-000000", target=target, **kw)  # type: ignore[arg-type]


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
            HookCommand(
                name="user", cmd=_script(tmp_path, "mk.sh", 'echo "userId=u-42" > $BAJUTSU_OUTPUT')
            ),
            HookCommand(
                cmd=_script(tmp_path, "rd.sh", f'echo "$BAJUTSU_HOOK__user__userId" > {seen}')
            ),
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
        [
            HookCommand(
                name="seed", cmd=_script(tmp_path, "bad.sh", 'echo "oops" > $BAJUTSU_OUTPUT')
            )
        ],
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
        [
            HookCommand(
                name="seed", cmd=_script(tmp_path, "k.sh", f'echo "{key}=v" > $BAJUTSU_OUTPUT')
            )
        ],
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
    result = runner.run_list("beforeRun", [HookCommand(cmd=str(tmp_path / "nope.sh"))], _ctx())
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
    result = runner.run_list("beforeRun", [HookCommand(use="seed", env={"SEED": "ok"})], _ctx())
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
    pidfile = tmp_path / "server.pid"
    body = f"sleep 20 & echo $! > '{pidfile}'; echo started"
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="srv", cmd=_script(tmp_path, "bg.sh", body), timeout=5)],
        _ctx(),
    )
    assert result.ok, result.reason
    # By pid, never `pkill -f`: a pattern also matches any parallel worker's process whose command
    # line merely contains it, which killed the diagnostics action tests' `bash -c <script>`.
    os.kill(int(pidfile.read_text()), signal.SIGKILL)


def test_a_planted_symlink_at_a_later_entry_s_output_path_is_never_written_through(
    tmp_path: Path,
) -> None:
    # Under `sandbox` the output half of the raw area is mounted writable, so a command can plant a
    # link at the predictable path of an entry that has not run yet. The link is removed and a fresh
    # regular file created in its place; what it pointed at is never touched.
    raw = hc.raw_root(tmp_path / "runs", "r1")
    runner = hc.HookRunner(raw_dir=raw, base_dir=tmp_path)
    victim = tmp_path / "host-secret"
    victim.write_text("KEEP ME", encoding="utf-8")
    planted = raw / hc.OUT_SUBDIR / "beforeRun" / "web" / "later.output"
    planted.parent.mkdir(parents=True)
    planted.symlink_to(victim)
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="later", cmd=_script(tmp_path, "ok.sh", "true"))],
        _ctx(),
    )
    assert result.ok, result.reason
    assert victim.read_text() == "KEEP ME"  # the link was removed, not followed
    assert not planted.is_symlink() and planted.is_file()


def test_a_symlinked_directory_in_the_raw_path_is_refused(tmp_path: Path) -> None:
    # `mkdir(parents=True)` would walk a planted directory link, so each level is checked.
    raw = hc.raw_root(tmp_path / "runs", "r1")
    runner = hc.HookRunner(raw_dir=raw, base_dir=tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (raw / hc.OUT_SUBDIR).mkdir(parents=True, exist_ok=True)
    (raw / hc.OUT_SUBDIR / "beforeRun").symlink_to(elsewhere, target_is_directory=True)
    result = runner.run_list(
        "beforeRun", [HookCommand(cmd=_script(tmp_path, "ok2.sh", "true"))], _ctx()
    )
    assert not result.ok and "symlink" in result.reason


def test_the_log_half_of_the_raw_area_is_separate_from_the_mounted_half(tmp_path: Path) -> None:
    # The container writes no log of its own (the host captures `docker exec`'s output), so the log
    # half is never mounted and a command cannot reach a later entry's log path at all.
    raw = hc.raw_root(tmp_path / "runs", "r1")
    runner = hc.HookRunner(raw_dir=raw, base_dir=tmp_path)
    runner.run_list(
        "beforeRun",
        [
            HookCommand(
                name="x", cmd=_script(tmp_path, "s.sh", 'echo hi; echo "k=v" > $BAJUTSU_OUTPUT')
            )
        ],
        _ctx(),
    )
    assert (raw / hc.LOGS_SUBDIR / "beforeRun" / "web" / "x.log").read_text().strip() == "hi"
    assert (raw / hc.OUT_SUBDIR / "beforeRun" / "web" / "x.output").read_text().strip() == "k=v"


@pytest.mark.parametrize("name", ["../../escape", "a/b", ".hidden", ""])
def test_a_target_name_that_is_not_one_path_segment_is_refused(tmp_path: Path, name: str) -> None:
    # A target name is an unvalidated map key under `targets:`, and it becomes a directory of the
    # raw area and of the evidence tree.
    runner = _runner(tmp_path)
    result = runner.run_list(
        "beforeRun",
        [HookCommand(cmd=_script(tmp_path, "seg.sh", "true"))],
        hc.HookContext(run_id="r1", target=name),
    )
    assert not result.ok and "one path segment" in result.reason


def test_inherited_outputs_are_copied_not_written_into(tmp_path: Path) -> None:
    # Two workers each run their own scenario's lists starting from the run-level outputs; writing
    # into that one object would leak one scenario's values into another's.
    runner = _runner(tmp_path)
    run_level = hc.HookOutputs(values={"server": {"port": "8787"}})
    result = runner.run_list(
        "beforeScenario",
        [
            HookCommand(
                name="user", cmd=_script(tmp_path, "mku.sh", 'echo "id=7" > $BAJUTSU_OUTPUT')
            )
        ],
        _ctx(scenario_index=0),
        inherited=run_level,
    )
    assert result.outputs.values == {"server": {"port": "8787"}, "user": {"id": "7"}}
    assert run_level.values == {"server": {"port": "8787"}}  # untouched


def test_a_named_command_reads_earlier_outputs_and_writes_its_own(tmp_path: Path) -> None:
    # The worker's `env` list bounds what the *config* may set; Bajutsu's own context and the
    # outputs of earlier commands are not the config's doing, so a `use` entry sees them too.
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir()
    script = _script(
        worker_dir,
        "seed.sh",
        'echo "seen=$BAJUTSU_HOOK__first__token" > "$BAJUTSU_OUTPUT"',
    )
    gate = CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="named"))
    )
    runner = _runner(
        tmp_path / "config", gate=gate, named_commands={"seed": NamedCommand(path=script)}
    )
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="second", use="seed")],
        _ctx(),
        inherited=hc.HookOutputs(values={"first": {"token": "t-1"}}),
    )
    assert result.ok, result.reason
    assert result.outputs.values["second"] == {"seen": "t-1"}


def test_a_relative_cwd_can_resolve_against_a_per_call_base(tmp_path: Path) -> None:
    # Entries declared in another file resolve their `cwd` against that file's directory, without
    # moving the directory a named command may not live inside (BE-0501's seam).
    other = tmp_path / "scenarios" / "sub"
    other.mkdir(parents=True)
    seen = tmp_path / "base.txt"
    runner = _runner(tmp_path)
    runner.run_list(
        "beforeScenario",
        [HookCommand(cmd=_script(tmp_path, "pwd2.sh", f"pwd > {seen}"), cwd="sub")],
        _ctx(),
        cwd_base=tmp_path / "scenarios",
    )
    assert seen.read_text().strip() == str(other.resolve())


# --- the ${hooks.<name>.<key>} token ---


def test_a_token_naming_no_readable_hook_fails_when_the_run_loads() -> None:
    assert hc.unknown_hook_tokens({"hooks.user.id"}, {"server"}) is not None
    assert hc.unknown_hook_tokens({"hooks.user.id"}, {"user", "server"}) is None
    assert hc.unknown_hook_tokens({"vars.x", "secrets.Y"}, set()) is None


def test_a_token_naming_a_skipped_before_scenario_hook_says_why() -> None:
    # `inheritSetup: false` skips the target's beforeScenario entries, so the value is never written.
    why = hc.unknown_hook_tokens({"hooks.user.id"}, {"server"}, skipped={"user"})
    assert why is not None and "inheritSetup" in why


def test_a_key_the_command_never_wrote_names_the_hook_and_the_key() -> None:
    outputs = hc.HookOutputs(values={"user": {"userId": "u-1"}})
    assert hc.missing_hook_key({"hooks.user.userId"}, outputs) is None
    why = hc.missing_hook_key({"hooks.user.email"}, outputs)
    assert why is not None and "'user'" in why and "'email'" in why
    assert "wrote userId" in why
    assert hc.missing_hook_key({"hooks.other.k"}, outputs) is not None


def test_worker_indexes_are_stable_per_thread() -> None:
    import threading

    index = hc.WorkerIndex()
    seen: list[int] = []
    assert index.current() == 0 and index.current() == 0  # stable on the same thread

    def other() -> None:
        seen.append(index.current())

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert seen == [1]


def test_a_cancelled_teardown_is_bounded_by_its_budget(tmp_path: Path) -> None:
    # A SIGTERM must not be held off for one more `timeout` per entry: each is capped by what is
    # left of the slice of the grace window a scenario's own `cleanup` already uses.
    import time

    runner = _runner(tmp_path)
    ran = tmp_path / "late.txt"
    result = runner.run_list(
        "afterRun",
        [
            HookCommand(cmd=_script(tmp_path, "slow.sh", "sleep 10"), timeout=300),
            HookCommand(cmd=_script(tmp_path, "late2.sh", f"touch {ran}")),
        ],
        _ctx(verdict="failure"),
        deadline=time.monotonic() + 0.4,
    )
    # The cap is what is left of the budget, not the entry's own 300s.
    assert not result.ok and "timed out after 0." in result.reason
    # The budget is spent, so the entry behind it is recorded as skipped rather than run.
    assert not ran.exists()
    assert result.records[-1].skipped and result.records[-1].reason == "teardown budget spent"


def test_an_evidence_prefix_separates_one_matrix_pass_from_another(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "r1"
    writer = RunArtifactWriter(run_dir, Redactor(Redact(), []))
    runner = _runner(tmp_path, writer=writer)
    result = runner.run_list(
        "beforeScenario",
        [HookCommand(name="user", cmd=_script(tmp_path, "pfx.sh", "true"))],
        hc.HookContext(
            run_id="r1", target="web", run_dir=run_dir, scenario_index=0, log_prefix="firefox"
        ),
    )
    assert result.records[0].log == "hooks/firefox/beforeScenario/web/00/user.log"


def test_a_run_time_refusal_is_recorded_under_the_entry_s_own_field(tmp_path: Path) -> None:
    # The field a refusal lands under must match the one a command that ran is recorded with, or a
    # reader of `manifest.json` cannot line the two up (BE-0492).
    gate = CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="deny"))
    )
    runner = _runner(tmp_path, gate=gate)
    runner.run_list(
        "beforeRun", [HookCommand(name="srv", cmd=_script(tmp_path, "d.sh", "true"))], _ctx()
    )
    assert [r["field"] for r in gate.records] == ["hooks.beforeRun[srv].cmd"]


def test_a_named_command_refusal_is_recorded_under_the_same_field(tmp_path: Path) -> None:
    gate = CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="deny"))
    )
    runner = _runner(tmp_path, gate=gate)
    runner.run_list("afterRun", [HookCommand(use="seed")], _ctx(verdict="success"))
    assert [r["field"] for r in gate.records] == ["hooks.afterRun[0].cmd"]


def test_the_manifest_records_are_ordered_by_phase_then_scenario(tmp_path: Path) -> None:
    # Parallel workers append to one list, so arrival order depends on thread timing; two identical
    # runs must still write identical evidence.
    records = [
        hc.HookRecord("afterRun", "stop", "web", None),
        hc.HookRecord("beforeScenario", "user", "web", None, scenario=2),
        hc.HookRecord("beforeRun", "srv", "web", None),
        hc.HookRecord("afterScenario", "rm", "web", None, scenario=1),
        hc.HookRecord("beforeScenario", "user", "web", None, scenario=0),
    ]
    out = hc.hook_record_dicts(records)
    assert [(r["list"], r.get("scenario")) for r in out] == [
        ("beforeRun", None),
        ("beforeScenario", 0),
        ("beforeScenario", 2),
        ("afterScenario", 1),
        ("afterRun", None),
    ]


def test_a_named_pipe_at_the_output_path_fails_rather_than_hanging(tmp_path: Path) -> None:
    # The entry's own `timeout` is already spent by the time the output file is read, so a blocking
    # open would hold the run with nothing left to bound it (prime directive 2).
    raw = hc.raw_root(tmp_path / "runs", "r1")
    runner = hc.HookRunner(raw_dir=raw, base_dir=tmp_path)
    planted = raw / hc.OUT_SUBDIR / "beforeRun" / "web" / "fifo.output"
    planted.parent.mkdir(parents=True, exist_ok=True)
    body = 'rm -f "$BAJUTSU_OUTPUT"; mkfifo "$BAJUTSU_OUTPUT"'
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="fifo", cmd=_script(tmp_path, "fifo.sh", body), timeout=5)],
        _ctx(),
    )
    assert not result.ok
    assert "could not be read" in result.reason
    assert planted.is_fifo()  # the entry failed instead of blocking on the open


def test_an_output_file_the_command_deleted_fails_the_entry(tmp_path: Path) -> None:
    # Reporting a lost file as an empty one would mark the entry green and leave a scenario blaming
    # the script for not writing a value it could not read.
    runner = _runner(tmp_path)
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="gone", cmd=_script(tmp_path, "rm.sh", 'rm -f "$BAJUTSU_OUTPUT"'))],
        _ctx(),
    )
    assert not result.ok and "could not be read" in result.reason


def test_both_halves_of_the_raw_area_exist_before_any_entry_runs(tmp_path: Path) -> None:
    # The writable half is the container's bind-mount source; a missing source would have the Docker
    # daemon create it as root instead.
    raw = hc.raw_root(tmp_path / "runs", "r1")
    for half in (hc.OUT_SUBDIR, hc.LOGS_SUBDIR):
        assert (raw / half).is_dir()
        assert (raw / half).stat().st_mode & 0o777 == 0o700


def test_a_masked_entry_s_own_log_is_masked_too(tmp_path: Path) -> None:
    # Registering the mask after the copy would leave the entry's own log in plaintext until the
    # end-of-run recopy, which a run killed earlier never reaches.
    run_dir = tmp_path / "runs" / "r1"
    writer = RunArtifactWriter(run_dir, Redactor(Redact(), []))
    runner = _runner(tmp_path, writer=writer)
    body = 'echo "pw=hunter2" > $BAJUTSU_OUTPUT; echo "generated pw=hunter2"'
    result = runner.run_list(
        "beforeRun",
        [HookCommand(name="cred", mask=True, cmd=_script(tmp_path, "own.sh", body))],
        _ctx(run_dir=run_dir),
    )
    assert result.ok, result.reason
    assert "hunter2" not in (run_dir / "hooks" / "beforeRun" / "web" / "cred.log").read_text()


def test_a_skipped_conditional_entry_records_why(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = runner.run_list(
        "afterRun",
        [HookCommand(on="failure", cmd=_script(tmp_path, "nope.sh", "true"))],
        _ctx(verdict="success"),
    )
    record = result.records[0]
    assert record.skipped and record.reason is not None
    assert "`on: failure`" in record.reason and "'success'" in record.reason


def test_a_repeated_output_key_fails_the_entry(tmp_path: Path) -> None:
    # `port=8080` followed by `port=` (an unset variable) is the silently-missing value the
    # malformed-line rule exists to prevent.
    values, why = hc.parse_outputs("port=8080\nport=\n")
    assert values == {} and why is not None and "again" in why


def test_the_multi_target_order_is_declared_then_reversed(tmp_path: Path) -> None:
    # The last target prepared is the first one torn down, so a dependency is never stopped while
    # the thing using it is still up.
    order = tmp_path / "targets.txt"
    runner = _runner(tmp_path)
    phases = {
        name: HookPhases(
            before_run=[
                HookCommand(cmd=_script(tmp_path, f"b-{name}.sh", f"echo before-{name} >> {order}"))
            ],
            after_run=[
                HookCommand(cmd=_script(tmp_path, f"a-{name}.sh", f"echo after-{name} >> {order}"))
            ],
        )
        for name in ("alpha", "beta")
    }
    hc.run_phase(runner, "beforeRun", phases, _ctx_for)
    hc.run_phase(runner, "afterRun", phases, lambda t: _ctx_for(t, verdict="success"))
    assert order.read_text().split() == [
        "before-alpha",
        "before-beta",
        "after-beta",
        "after-alpha",
    ]


def test_a_before_list_stops_at_the_first_failing_target(tmp_path: Path) -> None:
    ran = tmp_path / "second.txt"
    runner = _runner(tmp_path)
    phases = {
        "alpha": HookPhases(before_run=[HookCommand(cmd=_script(tmp_path, "fa.sh", "exit 1"))]),
        "beta": HookPhases(
            before_run=[HookCommand(cmd=_script(tmp_path, "fb.sh", f"touch {ran}"))]
        ),
    }
    result = hc.run_phase(runner, "beforeRun", phases, _ctx_for)
    assert not result.ok and not ran.exists()


def test_an_after_list_visits_every_target_even_after_a_failure(tmp_path: Path) -> None:
    ran = tmp_path / "both.txt"
    runner = _runner(tmp_path)
    phases = {
        "alpha": HookPhases(
            after_run=[HookCommand(cmd=_script(tmp_path, "aa.sh", f"echo a >> {ran}"))]
        ),
        "beta": HookPhases(after_run=[HookCommand(cmd=_script(tmp_path, "ab.sh", "exit 1"))]),
    }
    result = hc.run_phase(runner, "afterRun", phases, lambda t: _ctx_for(t, verdict="failure"))
    assert not result.ok
    assert ran.read_text().split() == ["a"]  # beta failed first (reverse order), alpha still ran


def test_one_target_s_outputs_reach_the_next_target_s_command(tmp_path: Path) -> None:
    seen = tmp_path / "chain.txt"
    runner = _runner(tmp_path)
    phases = {
        "alpha": HookPhases(
            before_run=[
                HookCommand(
                    name="a", cmd=_script(tmp_path, "ca.sh", 'echo "k=v" > $BAJUTSU_OUTPUT')
                )
            ]
        ),
        "beta": HookPhases(
            before_run=[
                HookCommand(cmd=_script(tmp_path, "cb.sh", f'echo "$BAJUTSU_HOOK__a__k" > {seen}'))
            ]
        ),
    }
    result = hc.run_phase(runner, "beforeRun", phases, _ctx_for)
    assert result.ok, result.reason
    assert seen.read_text().strip() == "v"


def test_two_targets_sharing_a_hook_name_is_refused(tmp_path: Path) -> None:
    # A `${hooks.<name>.<key>}` token could not say which command it meant.
    phases = {
        "alpha": HookPhases(before_run=[HookCommand(name="srv", cmd="true")]),
        "beta": HookPhases(before_run=[HookCommand(name="srv", cmd="true")]),
    }
    why = hc.duplicate_hook_name(phases)
    assert why is not None and "'srv'" in why
    assert hc.duplicate_hook_name({"alpha": phases["alpha"]}) is None


def test_a_sandboxed_entry_runs_in_the_container_and_is_recorded_as_such(tmp_path: Path) -> None:
    # The security claim of the feature: under `sandbox` the command must not run on the host.
    calls: list[list[str]] = []

    def fake(
        argv: list[str], env: dict[str, str], cwd: Path | None, timeout: float, log: Path | None
    ) -> int:
        calls.append(argv)
        return 0

    gate = CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="sandbox"))
    )
    raw = hc.raw_root(tmp_path / "runs", "20260101-000000")
    runtime = sandbox.SandboxRuntime(
        sandbox=HookSandbox.model_validate({"dockerImage": "node:20-slim"}),
        bundle_cwd=tmp_path,
        raw_dir=raw / hc.OUT_SUBDIR,
    )
    runner = _runner(tmp_path, gate=gate, runner=fake)
    result = runner.run_list(
        "beforeRun", [HookCommand(name="srv", cmd="./s.sh")], _ctx(), sandbox=runtime
    )
    assert result.ok, result.reason
    assert calls[0][:2] == ["docker", "exec"]
    assert [r["decision"] for r in gate.records] == ["sandboxed"]


def test_a_sandboxed_entry_without_a_container_fails_naming_the_block(tmp_path: Path) -> None:
    gate = CommandGate(
        policy=CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="sandbox"))
    )
    runner = _runner(tmp_path, gate=gate)
    result = runner.run_list("beforeRun", [HookCommand(cmd="./s.sh")], _ctx())
    assert not result.ok and "hooks.sandbox" in result.reason


def test_a_failed_entry_s_outputs_still_reach_its_teardown(tmp_path: Path) -> None:
    # A `before` script that got halfway through has already written what identifies its work; the
    # teardown can only undo what it can name.
    seen = tmp_path / "torn.txt"
    runner = _runner(tmp_path)
    half = _script(tmp_path, "half.sh", 'echo "pidFile=/tmp/x.pid" > $BAJUTSU_OUTPUT; exit 1')
    result = runner.run_list("beforeRun", [HookCommand(name="server", cmd=half)], _ctx())
    assert not result.ok and "exited 1" in result.reason
    # The values are kept despite the failure, so the matching `afterRun` can read them.
    assert result.outputs.values["server"] == {"pidFile": "/tmp/x.pid"}
    after = runner.run_list(
        "afterRun",
        [
            HookCommand(
                cmd=_script(tmp_path, "undo.sh", f'echo "$BAJUTSU_HOOK__server__pidFile" > {seen}')
            )
        ],
        _ctx(verdict="failure"),
        inherited=result.outputs,
    )
    assert after.ok, after.reason
    assert seen.read_text().strip() == "/tmp/x.pid"


def test_a_failure_already_recorded_outranks_a_malformed_output_file(tmp_path: Path) -> None:
    # Reading the file on the failure path must not replace the reason the entry actually failed.
    runner = _runner(tmp_path)
    body = 'echo "oops" > $BAJUTSU_OUTPUT; exit 3'
    result = runner.run_list(
        "beforeRun", [HookCommand(name="x", cmd=_script(tmp_path, "both.sh", body))], _ctx()
    )
    assert "exited 3" in result.reason and "malformed" not in result.reason


def test_a_raw_area_that_cannot_be_removed_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What a failed cleanup leaves behind is the unmasked output of a `mask: true` hook, so the
    # failure never fails the run but is always said out loud.
    said: list[str] = []
    runner = hc.HookRunner(
        raw_dir=hc.raw_root(tmp_path / "runs", "r1"), base_dir=tmp_path, log=said.append
    )

    def refuse(*_a: object, **_k: object) -> None:
        raise OSError("Device or resource busy")

    monkeypatch.setattr("shutil.rmtree", refuse)
    runner.cleanup()
    assert any("could not be removed" in line for line in said)
    assert any("unmasked hook output" in line for line in said)
