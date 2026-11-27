"""One run's command gate: the worker's policy applied to each command the config declares (BE-0492).

A run checks every governed command against its `CommandPolicy` before it runs: the target's
server command, the app build, and the XCUITest runner build. The gate decides, and it keeps the
record `manifest.json` carries as `commandExecutions`, so a command that should not have run leaves
a trace with its source, mode, decision, and hash.

The runner build is decided deep inside the XCUITest environment, so the run installs its gate for
the process (`install`) instead of threading it through every driver layer, the way the environment
variable this replaces reached that code. A command with no gate installed — a library call outside
`run` / `record` / `crawl` — falls under the operator's own policy, as a terminal run always has.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bajutsu.common.capability.command_policy import (
    CommandKind,
    CommandPolicy,
    ConfigSource,
    command_sha256,
)

_MASK = "***"


class CommandRefused(RuntimeError):
    """A governed command the worker's policy does not run; the run exits 2 with the message."""


@dataclass
class CommandGate:
    """The policy one run applies, and the record of every governed command it met.

    Attributes:
        policy: The run's config source and the worker's modes.
        config_dir: The config's directory; an executable inside it is hashed, since a bundle or
            checkout can change it under the same command line.
        secret_values: Values masked out of a recorded command line, as `${secrets.*}` are in
            evidence.
        records: The `commandExecutions` entries, in the order the run met the commands.
        scenario_source: Where the scenario files came from; None means the config's own source.
        client_roots: The directories holding those scenario files. A `hooks` component file under
            one came with them, so it is judged by *scenario_source* rather than by the config's.
    """

    policy: CommandPolicy = field(default_factory=CommandPolicy)
    config_dir: Path | None = None
    secret_values: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    # Where the scenario files came from when that differs from the config (BE-0501).
    scenario_source: ConfigSource | None = None
    client_roots: tuple[Path, ...] = ()
    # The config's own source, kept once `for_scenario` swaps the policy's for the scenario's.
    config_source: ConfigSource | None = None
    # The `hooks` component file that added the command this gate judges (BE-0512), for the record.
    component: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def for_scenario(self) -> CommandGate:
        """This gate for a scenario's own commands: judged by its source, kept in the same record."""
        if self.scenario_source is None:
            return self
        return replace(
            self,
            policy=replace(self.policy, source=self.scenario_source),
            config_source=self.config_source or self.policy.source,
        )

    def for_entry(self, component: str | None) -> CommandGate:
        """This gate for a command a `hooks` component file added, or itself for a direct one.

        Two files bear on such a command, the one that applies the component and the component's
        own, so it runs under the stricter mode of their two sources (BE-0512).
        """
        if component is None:
            return self
        path = Path(component)
        own = self._source_of(path)
        return replace(
            self,
            policy=replace(
                self.policy, component_source=own if own != self.policy.source else None
            ),
            component=self._shown(path),
        )

    def _source_of(self, path: Path) -> ConfigSource:
        """Where a component file came from: with the scenario files when it lies among them."""
        resolved = path.resolve()
        if self.scenario_source is not None and any(
            resolved.is_relative_to(root.resolve()) for root in self.client_roots
        ):
            return self.scenario_source
        return self.config_source or self.policy.source

    def _shown(self, path: Path) -> str:
        """*path* as a report names it: relative to the config's directory when inside it."""
        if self.config_dir is not None:
            try:
                return path.resolve().relative_to(self.config_dir.resolve()).as_posix()
            except ValueError:
                pass
        return str(path)

    def check(self, kind: CommandKind, field_name: str, cmd: str) -> str:
        """Decide *cmd* (declared at *field_name*); return the action, recording a refusal.

        Returns:
            `host` or `sandbox` when the command may run (the caller records its outcome with
            `record`), or `skip` for a build the mode does not run (nothing recorded until the build
            turns out to be needed, when the caller calls `refuse`).

        Raises:
            CommandRefused: The mode refuses a command line or a named command outright.
        """
        action = self.policy.action(kind)
        if action == "refuse":
            self.refuse(field_name, cmd)
        return action

    def refuse(self, field_name: str, cmd: str) -> None:
        """Record *cmd* as refused and raise, naming the source and the mode.

        Raises:
            CommandRefused: Always.
        """
        self._append(field_name, cmd, "refused")
        # The message reaches stdout and every scenario's failure, so it carries the masked line.
        raise CommandRefused(self.policy.refusal(field_name, self._mask(cmd)))

    def record(
        self,
        field_name: str,
        cmd: str,
        *,
        sandboxed: bool = False,
        exit_code: int | None = None,
        cwd: Path | None = None,
    ) -> None:
        """Record a command that ran, on the host or in a container."""
        self._append(
            field_name, cmd, "sandboxed" if sandboxed else "ran", exit_code=exit_code, cwd=cwd
        )

    def record_skip(self, field_name: str, cmd: str, *, cwd: Path | None = None) -> None:
        """Record a hook command whose `when` did not match, so it never ran (BE-0497)."""
        self._append(field_name, cmd, "skipped", cwd=cwd)

    def _append(
        self,
        field_name: str,
        cmd: str,
        decision: str,
        *,
        exit_code: int | None = None,
        cwd: Path | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "field": field_name,
            "cmd": self._mask(cmd),
            "source": self.policy.source,
            "mode": self.policy.mode,
            "decision": decision,
            "sha256": command_sha256(cmd, self._config_executable(cmd, cwd)),
        }
        if exit_code is not None:
            entry["exitCode"] = exit_code
        if self.component is not None:
            entry["component"] = self.component
            if self.policy.component_source is not None:
                entry["componentSource"] = self.policy.component_source
        with self._lock:
            self.records.append(entry)

    def _mask(self, cmd: str) -> str:
        masked = cmd
        for value in sorted((v for v in self.secret_values if v), key=len, reverse=True):
            masked = masked.replace(value, _MASK)
        return masked

    def _config_executable(self, cmd: str, cwd: Path | None) -> Path | None:
        """The command's executable when it lies inside the config's directory, else None."""
        if self.config_dir is None:
            return None
        words = cmd.split()
        if not words:
            return None
        candidate = Path(words[0])
        if not candidate.is_absolute():
            candidate = (cwd or self.config_dir) / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError):  # ValueError: a NUL byte in the path
            return None
        if not resolved.is_file() or not resolved.is_relative_to(self.config_dir.resolve()):
            return None
        return resolved


# The one gate this process's governed commands consult; a list so `install` rebinds no global.
_installed: list[CommandGate] = []


def install(gate: CommandGate) -> CommandGate:
    """Make *gate* the one this process's governed commands consult, and return it."""
    _installed[:] = [gate]
    return gate


def current() -> CommandGate:
    """The gate installed for this run, or a fresh operator-policy gate when none is."""
    return _installed[0] if _installed else CommandGate()
