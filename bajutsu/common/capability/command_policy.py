"""Whether a config's commands run on this worker: the `commandExecution` policy (BE-0492).

A config can declare commands that Bajutsu runs on the machine that executes a run: the target's
hook commands (`hooks.beforeRun` and the three lists beside it, BE-0491), the app build
(`subject.build`), and the XCUITest runner build (`driver.runner.build`). The worker that runs them carries the risk, so the worker's own
`worker.yaml` decides, per trust level of the config — its *source* — whether each kind runs.

The decision is a table lookup on the source and the command's kind, so the same file, source, and
command always give the same answer (DESIGN §2), and no model sits anywhere on the path.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Where the config a run executes came from (BE-0492). `serve` labels each job, and a terminal or CI
# run labels its own config `operator`, since the person who started it chose the config.
ConfigSource = Literal["uploaded", "remote", "operator"]
CONFIG_SOURCES: tuple[ConfigSource, ...] = get_args(ConfigSource)
OPERATOR: ConfigSource = "operator"

CommandMode = Literal["allow", "sandbox", "named", "deny"]
COMMAND_MODES: tuple[CommandMode, ...] = get_args(CommandMode)

# The routing-token prefixes this policy fills in (`command-execution:<source>`,
# `named-execution:<source>`, `named-command:<name>`). Reserved beside the others in
# `worker_capability`, so a hand-written token cannot claim a source the file denies.
COMMAND_EXECUTION_PREFIX = "command-execution:"
NAMED_EXECUTION_PREFIX = "named-execution:"
NAMED_COMMAND_PREFIX = "named-command:"

# The config fields that declare a governed command, as the command record names them. A hook entry
# names its own list and slot (`hooks.beforeRun[server].cmd`), which `hook_commands` builds.
HOOKS_FIELD = "hooks"
SUBJECT_BUILD_FIELD = "subject.build"
RUNNER_BUILD_FIELD = "driver.runner.build"

# Variables that change how a process loads code. A config may set a named command's listed
# variables, so letting one of these through would let the config pick the code that runs.
_LOADER_VARIABLES = frozenset(
    {
        "PATH",
        "IFS",
        "ENV",
        "BASH_ENV",
        "SHELLOPTS",
        "NODE_OPTIONS",
        "NODE_PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "RUBYOPT",
        "RUBYLIB",
        "GEM_HOME",
        "GEM_PATH",
        "PERL5LIB",
        "PERL5OPT",
        "PERLLIB",
        "CLASSPATH",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
    }
)
_LOADER_PREFIXES = ("LD_", "DYLD_")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NAMED_COMMAND_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class CommandExecution(BaseModel):
    """The `commandExecution` block of `worker.yaml`: one mode per config source.

    Attributes:
        uploaded: The mode for a bundle a browser user uploaded.
        remote: The mode for a Git config a Web UI user bound after `serve` started.
        operator: The mode for the config the operator chose: the one `serve` started with, or the
            one a terminal or CI run names.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    uploaded: CommandMode = "sandbox"
    remote: CommandMode = "sandbox"
    operator: CommandMode = "allow"

    def mode_for(self, source: ConfigSource) -> CommandMode:
        """The mode that applies to a config from *source*."""
        mode: CommandMode = getattr(self, source)
        return mode

    def explicit_sandbox_sources(self) -> list[ConfigSource]:
        """The sources the file sets to `sandbox` itself, which need Docker at startup."""
        return [
            s
            for s in CONFIG_SOURCES
            if s in self.model_fields_set and self.mode_for(s) == "sandbox"
        ]


class NamedCommand(BaseModel):
    """One entry of `namedCommands`: a script the worker's operator installed and vouches for.

    Attributes:
        path: The script's absolute path on the worker, run directly without a shell.
        env: The variables a config may set for this command; nothing else from the config passes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    env: tuple[str, ...] = ()

    @field_validator("path")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError(
                f"path {value!r} is not absolute; a named command is a file on the worker"
            )
        return value

    @field_validator("env")
    @classmethod
    def _no_loader_variables(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        bad_names = [v for v in value if not _ENV_NAME.match(v)]
        if bad_names:
            raise ValueError(f"env lists invalid variable name(s) {bad_names}")
        loaders = [v for v in value if v in _LOADER_VARIABLES or v.startswith(_LOADER_PREFIXES)]
        if loaders:
            raise ValueError(
                f"env lists {loaders}, which change how a process loads code; a config must not "
                "steer what a named command runs"
            )
        return value


# What the run does with one governed command: run it on the host, run it in a container, refuse it,
# or skip it (a build the mode does not run).
Action = Literal["host", "sandbox", "refuse", "skip"]
CommandKind = Literal["command", "build", "named"]

_TABLE: dict[CommandKind, dict[CommandMode, Action]] = {
    # A command line the config spells out (a `hooks.*` entry's `cmd`).
    "command": {"allow": "host", "sandbox": "sandbox", "named": "refuse", "deny": "refuse"},
    # `subject.build` / `driver.runner.build`: an iOS build needs the macOS toolchain, which no
    # Linux container provides, so neither runs under `sandbox`.
    "build": {"allow": "host", "sandbox": "skip", "named": "skip", "deny": "skip"},
    # A named command's code is the operator's own, so it runs on the host under every mode but deny.
    "named": {"allow": "host", "sandbox": "host", "named": "host", "deny": "refuse"},
}


def action_for(kind: CommandKind, mode: CommandMode) -> Action:
    """What the policy does with a command of *kind* under *mode*."""
    return _TABLE[kind][mode]


@dataclass(frozen=True)
class CommandPolicy:
    """The policy one run applies: its config's source and the worker's `commandExecution` block.

    Attributes:
        source: Where the run's config came from.
        execution: The worker's modes; the shipped default allows the operator's own config.
    """

    source: ConfigSource = OPERATOR
    execution: CommandExecution = field(default_factory=CommandExecution)

    @property
    def mode(self) -> CommandMode:
        """The mode that applies to this run's config."""
        return self.execution.mode_for(self.source)

    def action(self, kind: CommandKind) -> Action:
        """What this run does with a command of *kind*."""
        return action_for(kind, self.mode)

    def refusal(self, field: str, cmd: str) -> str:
        """The message naming a command this policy refuses or a build it does not run."""
        return (
            f"{field} {cmd!r} does not run: the worker's commandExecution sets "
            f"{self.source}: {self.mode} (worker.yaml)"
        )


def docker_available(which: Callable[[str], str | None] = shutil.which) -> bool:
    """Whether a `docker` client is on this host's PATH, which the `sandbox` mode needs."""
    return which("docker") is not None


def advertised_tokens(
    execution: CommandExecution,
    named_commands: Mapping[str, NamedCommand],
    *,
    docker: bool,
) -> list[str]:
    """The routing tokens a worker advertises for its command policy, sorted.

    `command-execution:<source>` means the worker runs a command line from that source, so a
    `sandbox` source without Docker advertises none, and neither does a `named` or `deny` source:
    the worker never takes a job it would then fail. `named-execution:<source>` covers every source
    but a `deny` one, since a named command runs on the host, and `named-command:<name>` names each
    installed script.
    """
    tokens: list[str] = []
    for source in CONFIG_SOURCES:
        mode = execution.mode_for(source)
        if action_for("command", mode) == "host" or (mode == "sandbox" and docker):
            tokens.append(f"{COMMAND_EXECUTION_PREFIX}{source}")
        if mode != "deny":
            tokens.append(f"{NAMED_EXECUTION_PREFIX}{source}")
    tokens += [f"{NAMED_COMMAND_PREFIX}{name}" for name in named_commands]
    return sorted(tokens)


def command_requirement(source: ConfigSource) -> str:
    """The token a job whose config declares a governed command line requires."""
    return f"{COMMAND_EXECUTION_PREFIX}{source}"


def named_requirement(source: ConfigSource) -> str:
    """The token a job whose config runs a named command requires (BE-0491).

    Separate from the script's own `named-command:<name>`: a worker may run named commands from this
    source and still not have installed the one this config asks for, so a job needs both.
    """
    return f"{NAMED_EXECUTION_PREFIX}{source}"


class NamedCommandError(ValueError):
    """A named command a config uses cannot run on this worker."""


@dataclass(frozen=True)
class ResolvedNamedCommand:
    """A named command ready to run: the script, and the environment the config may add."""

    name: str
    path: str
    env: dict[str, str]


def resolve_named_command(
    named_commands: Mapping[str, NamedCommand],
    name: str,
    *,
    config_dir: Path,
    env: Mapping[str, str],
) -> ResolvedNamedCommand:
    """Resolve the named command *name* that a config uses, with the variables it sets.

    Args:
        named_commands: The worker's `namedCommands`.
        name: The name the config uses.
        config_dir: The config's directory; a script inside it is refused, so a bundle cannot
            replace the file the operator vouched for.
        env: The variables the config sets; each must be one the entry lists.

    Raises:
        NamedCommandError: The worker lists no such command, its script lies inside the config's
            directory, or the config sets a variable the entry does not list.
    """
    entry = named_commands.get(name)
    if entry is None:
        raise NamedCommandError(
            f"named command {name!r} is not listed in this worker's namedCommands"
        )
    script = Path(entry.path).resolve()
    if script.is_relative_to(config_dir.resolve()):
        raise NamedCommandError(
            f"named command {name!r} points inside the config's directory ({entry.path}); "
            "its script must be a file the worker owns"
        )
    unlisted = sorted(set(env) - set(entry.env))
    if unlisted:
        raise NamedCommandError(
            f"named command {name!r} does not accept variable(s) {unlisted}; it lists {list(entry.env)}"
        )
    return ResolvedNamedCommand(name=name, path=entry.path, env=dict(env))


def command_sha256(cmd: str, executable: Path | None = None) -> dict[str, str]:
    """The hashes the command record keeps: of the command line, and of the executable when given.

    The executable is hashed only when it lies inside the config's directory or is a named command's
    script, which the caller decides; a changed script then shows a different hash even when its
    command line stayed the same.
    """
    hashes = {"cmd": hashlib.sha256(cmd.encode()).hexdigest()}
    if executable is not None:
        with contextlib.suppress(OSError):
            hashes["executable"] = hashlib.sha256(executable.read_bytes()).hexdigest()
    return hashes


# The manifest provenance key the command record lives under (BE-0492).
COMMAND_EXECUTIONS_KEY = "commandExecutions"

_DECISION_WORDS = {
    "ran": "ran on the host",
    "sandboxed": "ran in a container",
    "refused": "refused",
    "skipped": "skipped (when)",
}


def command_notices(records: object) -> list[str]:
    """One report notice per governed command a run met, read from its `commandExecutions`.

    The notice states the field, the command line as recorded (already masked), the config source,
    the worker's mode, and what happened, so a reader sees what the run executed beside its other
    provenance. Anything that is not a list of records yields no notice.
    """
    if not isinstance(records, list):
        return []
    notices = []
    for record in records:
        if not isinstance(record, dict):
            continue
        decision = str(record.get("decision", ""))
        outcome = _DECISION_WORDS.get(decision, decision)
        if "exitCode" in record:
            outcome += f" (exit {record['exitCode']})"
        notices.append(
            f"{record.get('field')} {record.get('cmd')!r} from the {record.get('source')} config: "
            f"{outcome} under commandExecution {record.get('mode')} (BE-0492)"
        )
    return notices
