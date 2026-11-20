"""The worker's command policy: `commandExecution`, `namedCommands`, and the decision table (BE-0492).

Everything here is a pure table lookup or a file check, so no Docker daemon, device, or server is
needed: Docker's presence is injected.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bajutsu.common.capability.command_policy import (
    CommandExecution,
    CommandPolicy,
    NamedCommand,
    NamedCommandError,
    action_for,
    advertised_tokens,
    command_sha256,
    docker_available,
    resolve_named_command,
)
from bajutsu.common.capability.worker_capability import (
    WorkerCapabilityError,
    load_worker_capability,
    parse_worker_capability,
    reserved_routing_tokens,
)
from bajutsu.serve.cli.worker import _command_policy_tokens_or_exit


def _parse(body: str) -> object:
    return parse_worker_capability("version: 1\ndrivers: [playwright]\n" + body)


# --- the block in worker.yaml --------------------------------------------------------------------


def test_defaults_sandbox_untrusted_sources_and_allow_the_operator() -> None:
    cap = load_worker_capability()
    assert cap.command_execution == CommandExecution(
        uploaded="sandbox", remote="sandbox", operator="allow"
    )
    assert cap.command_execution.explicit_sandbox_sources() == []
    assert cap.named_commands == {}


def test_block_overrides_per_source_and_remembers_which_keys_it_set() -> None:
    cap = parse_worker_capability(
        "version: 1\ndrivers: [playwright]\ncommandExecution:\n  uploaded: deny\n  remote: sandbox\n"
    )
    assert cap.command_execution.mode_for("uploaded") == "deny"
    assert cap.command_execution.mode_for("operator") == "allow"
    assert cap.command_execution.explicit_sandbox_sources() == ["remote"]


@pytest.mark.parametrize(
    "body",
    [
        "commandExecution:\n  uploaded: reuse\n",  # BE-0090's reuse mode does not carry over
        "commandExecution:\n  ci: allow\n",
        "namedCommands:\n  seed:\n    path: relative/seed.sh\n",
        "namedCommands:\n  seed:\n    path: /opt/seed.sh\n    env: [PATH]\n",
        "namedCommands:\n  seed:\n    path: /opt/seed.sh\n    env: [LD_PRELOAD]\n",
        "namedCommands:\n  seed:\n    path: /opt/seed.sh\n    env: [DYLD_INSERT_LIBRARIES]\n",
        "namedCommands:\n  seed:\n    path: /opt/seed.sh\n    env: [NODE_OPTIONS]\n",
        "namedCommands:\n  seed:\n    path: /opt/seed.sh\n    env: [1BAD]\n",
        "namedCommands:\n  9seed:\n    path: /opt/seed.sh\n",
        "namedCommands:\n  seed:\n    path: /opt/seed.sh\n    args: [x]\n",
    ],
)
def test_rejects_a_bad_block(body: str) -> None:
    with pytest.raises(WorkerCapabilityError):
        _parse(body)


def test_named_commands_parse() -> None:
    cap = parse_worker_capability(
        "version: 1\ndrivers: [playwright]\nnamedCommands:\n"
        "  seed-users:\n    path: /opt/bajutsu/seed.sh\n    env: [SEED_COUNT]\n"
    )
    assert cap.named_commands["seed-users"] == NamedCommand(
        path="/opt/bajutsu/seed.sh", env=("SEED_COUNT",)
    )


# --- the decision table --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "mode", "action"),
    [
        ("command", "allow", "host"),
        ("command", "sandbox", "sandbox"),
        ("command", "named", "refuse"),
        ("command", "deny", "refuse"),
        ("build", "allow", "host"),
        ("build", "sandbox", "skip"),
        ("build", "named", "skip"),
        ("build", "deny", "skip"),
        ("named", "allow", "host"),
        ("named", "sandbox", "host"),
        ("named", "named", "host"),
        ("named", "deny", "refuse"),
    ],
)
def test_action_table(kind: str, mode: str, action: str) -> None:
    assert action_for(kind, mode) == action  # type: ignore[arg-type]


def test_policy_reads_the_mode_of_its_source_and_names_it_in_a_refusal() -> None:
    policy = CommandPolicy(source="uploaded", execution=CommandExecution(uploaded="deny"))
    assert policy.mode == "deny"
    assert policy.action("command") == "refuse"
    message = policy.refusal("services.server.cmd", "./start.sh")
    assert "services.server.cmd" in message and "uploaded: deny" in message
    assert CommandPolicy().action("command") == "host"  # a plain CLI run is the operator's own


# --- routing tokens ------------------------------------------------------------------------------


def test_default_tokens_with_docker() -> None:
    assert advertised_tokens(CommandExecution(), {}, docker=True) == [
        "command-execution:operator",
        "command-execution:remote",
        "command-execution:uploaded",
        "named-execution:operator",
        "named-execution:remote",
        "named-execution:uploaded",
    ]


def test_sandbox_without_docker_and_named_and_deny_take_no_command_token() -> None:
    execution = CommandExecution(uploaded="deny", remote="named", operator="sandbox")
    named = {"seed": NamedCommand(path="/opt/seed.sh")}
    assert advertised_tokens(execution, named, docker=False) == [
        "named-command:seed",
        "named-execution:operator",
        "named-execution:remote",
    ]


def test_policy_prefixes_are_reserved() -> None:
    assert reserved_routing_tokens(
        ["command-execution:uploaded", "named-execution:remote", "named-command:x", "gpu"]
    ) == ["command-execution:uploaded", "named-command:x", "named-execution:remote"]


def test_worker_stops_when_an_explicit_sandbox_has_no_docker() -> None:
    cap = parse_worker_capability(
        "version: 1\ndrivers: [playwright]\ncommandExecution:\n  uploaded: sandbox\n"
    )
    with pytest.raises(typer.BadParameter, match=r"commandExecution\.uploaded"):
        _command_policy_tokens_or_exit(cap, docker=False)


def test_worker_withholds_a_default_sandbox_token_without_docker() -> None:
    tokens = _command_policy_tokens_or_exit(load_worker_capability(), docker=False)
    assert "command-execution:operator" in tokens
    assert "command-execution:uploaded" not in tokens
    assert "named-execution:uploaded" in tokens


def test_docker_available_reads_the_path() -> None:
    assert docker_available(lambda _: "/usr/bin/docker") is True
    assert docker_available(lambda _: None) is False


# --- named commands ------------------------------------------------------------------------------


def test_resolve_named_command(tmp_path: Path) -> None:
    script = tmp_path / "worker" / "seed.sh"
    commands = {"seed": NamedCommand(path=str(script), env=("SEED_COUNT",))}
    resolved = resolve_named_command(
        commands, "seed", config_dir=tmp_path / "bundle", env={"SEED_COUNT": "3"}
    )
    assert (resolved.path, resolved.env) == (str(script), {"SEED_COUNT": "3"})


def test_resolve_named_command_refusals(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    inside = {"seed": NamedCommand(path=str(bundle / "seed.sh"))}
    outside = {"seed": NamedCommand(path=str(tmp_path / "seed.sh"), env=("A",))}
    with pytest.raises(NamedCommandError, match="not listed"):
        resolve_named_command(outside, "other", config_dir=bundle, env={})
    with pytest.raises(NamedCommandError, match="inside the config"):
        resolve_named_command(inside, "seed", config_dir=bundle, env={})
    with pytest.raises(NamedCommandError, match="does not accept"):
        resolve_named_command(outside, "seed", config_dir=bundle, env={"B": "1"})


def test_command_sha256_hashes_the_line_and_the_executable(tmp_path: Path) -> None:
    script = tmp_path / "start.sh"
    script.write_text("echo one\n")
    first = command_sha256("./start.sh", script)
    script.write_text("echo two\n")
    second = command_sha256("./start.sh", script)
    assert first["cmd"] == second["cmd"]
    assert first["executable"] != second["executable"]
    assert set(command_sha256("x", tmp_path / "missing")) == {"cmd"}


# --- the report's notices ------------------------------------------------------------------------


def test_report_notices_list_each_command_the_run_met() -> None:
    from bajutsu.common.capability.route_tokens import report_notices

    notices = report_notices(
        {
            "commandExecutions": [
                {
                    "field": "services.server.cmd",
                    "cmd": "./start.sh",
                    "source": "uploaded",
                    "mode": "sandbox",
                    "decision": "sandboxed",
                },
                {
                    "field": "subject.build",
                    "cmd": "make",
                    "source": "operator",
                    "mode": "allow",
                    "decision": "ran",
                    "exitCode": 0,
                },
            ]
        }
    )
    assert notices == [
        "services.server.cmd './start.sh' from the uploaded config: ran in a container under "
        "commandExecution sandbox (BE-0492)",
        "subject.build 'make' from the operator config: ran on the host (exit 0) under "
        "commandExecution allow (BE-0492)",
    ]
    assert report_notices({"commandExecutions": "garbage"}) == []
    assert report_notices({"commandExecutions": ["garbage"]}) == []
