"""Run a target's external hook commands around a run and around each scenario (BE-0491).

One entry is a command, a bounded `timeout`, and an output file. The runner starts it in its own
process group without a shell, waits for it to finish, reads the `key=value` lines it wrote to
`$BAJUTSU_OUTPUT`, and hands those values to later commands (as `BAJUTSU_HOOK__<name>__<key>`) and
to the scenario (as `${hooks.<name>.<key>}`).

It stays inside the prime directives: a hook is an exit code, never a verdict on the app — a
failure fails the run or the scenario because a precondition or a teardown did not hold (prime
directive 1) — and every command is bounded by its own `timeout` rather than by a fixed pause, with
readiness left to the script, which can poll whatever condition its own server needs (directive 2).
Whether a command may run at all is the worker's policy (BE-0492), which the run's gate applies
before any device work.

Output and logs never go through a pipe. A script may leave a server running after it exits, and a
server that inherited our pipe would keep the runner waiting on a file descriptor that never closes;
each command writes to a file instead. That raw file lands outside the run directory, because a
sandboxed command can write into it: Bajutsu copies it into the evidence tree with secrets masked,
file by file, by the names it created itself.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from bajutsu.common.capability import command_gate
from bajutsu.common.capability.command_gate import CommandGate, CommandRefused
from bajutsu.common.capability.command_policy import (
    CommandKind,
    NamedCommandError,
    ResolvedNamedCommand,
    resolve_named_command,
)
from bajutsu.common.config.effective.hook_phases import HookPhases
from bajutsu.common.config.schema.hook_command import HookCommand, invalid_hook_name
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.run_meta.artifact_perms import make_run_dir, restrict_file
from bajutsu.common.runner.sandbox import SandboxError, SandboxRuntime

# The process seam `HookRunner` spawns through: argv, environment, working directory, the timeout it
# is bounded by, and the raw log file its output is appended to. Injected in tests, so every
# decision and argv is checkable without spawning anything.
SpawnFn = Callable[[list[str], dict[str, str], "Path | None", float, "Path | None"], int]

# The field name a hook entry's command is recorded under in `manifest.json` (BE-0492). The list and
# the entry are part of it, so a reader sees which phase ran what.
HOOK_FIELD = "hooks"

# Where the raw (unmasked) logs and output files of one run live: a sibling of the run directories,
# never inside the run directory that ships as evidence.
RAW_DIRNAME = ".hook-raw"
# The file naming the process that owns a raw directory, so a stale sweep never deletes the raw area
# of a run that is still going — several workers run side by side on one machine.
_OWNER_FILE = "owner.pid"


def _default_log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


@dataclass(frozen=True)
class HookContext:
    """What one hook command learns about the run and the scenario around it.

    Each field becomes a `BAJUTSU_*` variable, so a script can pick its data, its port, or its
    teardown by the run, the target, the scenario, or the worker it belongs to.
    """

    run_id: str
    target: str
    # None under `sandbox`, where the run directory is not mounted into the container.
    run_dir: Path | None = None
    scenario: str | None = None
    scenario_file: str | None = None
    # The worker index under `run --workers`; scenario hooks run in the worker that runs their
    # scenario, so a script that touches shared state can key on it.
    worker: int | None = None
    # `success` / `failure`, set for the `after` lists alone.
    verdict: str | None = None
    # The scenario's run-order index, which separates one scenario's logs from another's.
    scenario_index: int | None = None

    def env(self) -> dict[str, str]:
        """The context variables a command of this phase receives."""
        out = {"BAJUTSU_RUN_ID": self.run_id, "BAJUTSU_TARGET": self.target}
        if self.run_dir is not None:
            out["BAJUTSU_RUN_DIR"] = str(self.run_dir)
        if self.scenario is not None:
            out["BAJUTSU_SCENARIO"] = self.scenario
        if self.scenario_file is not None:
            out["BAJUTSU_SCENARIO_FILE"] = self.scenario_file
        if self.worker is not None:
            out["BAJUTSU_WORKER"] = str(self.worker)
        if self.verdict is not None:
            out["BAJUTSU_VERDICT"] = self.verdict
        return out


@dataclass
class HookOutputs:
    """The values the hook commands in one scope wrote, keyed by entry name then output key.

    A scenario sees the outputs of `beforeRun` and of its own `beforeScenario` entries, so a worker
    runs with the run-level outputs and adds its own scenario's on top (`merged`).
    """

    values: dict[str, dict[str, str]] = field(default_factory=dict)
    # Names whose values an entry marked `mask: true`, masked in evidence like a `${secrets.*}`.
    masked: set[str] = field(default_factory=set)

    def env(self) -> dict[str, str]:
        """These outputs as the `BAJUTSU_HOOK__<name>__<key>` variables a later command reads."""
        return {
            f"BAJUTSU_HOOK__{name}__{key}": value
            for name, keys in self.values.items()
            for key, value in keys.items()
        }

    def bindings(self) -> dict[str, str]:
        """These outputs as the `hooks.<name>.<key>` tokens a step substitutes."""
        return {
            f"hooks.{name}.{key}": value
            for name, keys in self.values.items()
            for key, value in keys.items()
        }

    def secret_values(self) -> list[str]:
        """The values to mask in evidence — those of an entry that asked to be masked."""
        return [v for name in self.masked for v in self.values.get(name, {}).values()]

    def merged(self, other: HookOutputs) -> HookOutputs:
        """These outputs with *other*'s laid on top, leaving both untouched."""
        return HookOutputs(
            values={**self.values, **other.values}, masked=self.masked | other.masked
        )


@dataclass(frozen=True)
class HookFailure:
    """One hook entry that did not succeed, as the verdict and the report word it."""

    list_name: str
    label: str
    reason: str

    def __str__(self) -> str:
        return f"{self.list_name}: {self.label}: {self.reason}"


@dataclass(frozen=True)
class HookRecord:
    """What one entry did, for the report's own hook block (BE-0491)."""

    list_name: str
    label: str
    # The run-dir-relative path of the masked log, or None when nothing was copied (no run
    # directory, or the raw file was refused).
    log: str | None
    skipped: bool = False
    failure: str | None = None


@dataclass
class HookPhaseResult:
    """The outcome of running one list: its outputs, its failures, and its report records."""

    outputs: HookOutputs = field(default_factory=HookOutputs)
    failures: list[HookFailure] = field(default_factory=list)
    records: list[HookRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every entry that ran succeeded."""
        return not self.failures

    @property
    def reason(self) -> str:
        """Every failure as one message, in the order the entries ran."""
        return "; ".join(str(f) for f in self.failures)


def raw_root(runs_dir: Path | None, run_id: str) -> Path:
    """The raw directory for *run_id*, created owner-only.

    It sits beside the run directories, never inside the one that ships as evidence. A run that
    keeps no run directory at all (`--sandbox`) has nothing to sit beside, so its raw files go to a
    private temporary directory, which `HookRunner.cleanup` removes just the same.
    """
    if runs_dir is None:
        return Path(tempfile.mkdtemp(prefix="bajutsu-hook-"))
    root = Path(runs_dir) / RAW_DIRNAME
    make_run_dir(root)
    _sweep_stale(root, run_id)
    own = make_run_dir(root / run_id)
    (own / _OWNER_FILE).write_text(str(os.getpid()), encoding="utf-8")
    return own


def _sweep_stale(root: Path, run_id: str) -> None:
    """Remove the raw area of every run that is no longer running (BE-0491).

    A run that dies before its own cleanup would otherwise leave plaintext behind, so the next run
    removes it. The owning process id is checked first: several workers share one machine, and a
    blind sweep would delete the raw area of a run that is still writing into it.
    """
    for child in root.iterdir():
        if not child.is_dir() or child.name == run_id or child.is_symlink():
            continue
        if _owner_alive(child):
            continue
        with contextlib.suppress(OSError):
            shutil.rmtree(child)


def _owner_alive(raw_dir: Path) -> bool:
    """Whether the process that created *raw_dir* is still running.

    An unreadable or malformed owner file counts as dead: the directory was written by a run that
    did not get as far as claiming it, so no live run depends on it.
    """
    try:
        pid = int((raw_dir / _OWNER_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # alive, owned by another user
        return True
    return True


def _read_regular(path: Path) -> str | None:
    """*path*'s text, or None when it is not a regular file we opened ourselves.

    A sandboxed command can write into the raw area, so a file that is a symbolic link — or that
    became one between the check and the open — must never pull host content into the evidence tree.
    `O_NOFOLLOW` refuses the link at open time, and the check runs on the opened descriptor, so
    there is no window to swap the file in.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, encoding="utf-8", errors="replace") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return None
        return fh.read()


def parse_outputs(text: str) -> tuple[dict[str, str], str | None]:
    """The `key=value` lines of an output file, or the reason the file is malformed.

    A typo must never turn into a silently missing value, so a line that is not `key=value` with a
    well-formed key fails the entry rather than being skipped. Blank lines are ignored, and a value
    keeps everything after the first `=`, spaces included.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        if not sep:
            return {}, f"line {number} is not `key=value`: {line!r}"
        key = key.strip()
        if (why := invalid_hook_name(key)) is not None:
            return {}, f"line {number} output key {why}"
        values[key] = value
    return values, None


class HookRunner:
    """Runs the hook entries of one run, carrying the raw area, the gate, and the outputs.

    One runner serves a whole run: `run_list` executes one list and returns what it produced, and
    the run-level outputs it collects are handed to each scenario's own lists. Construct it with the
    run's artifact writer to have each command's log copied into the evidence tree with secrets
    masked; without one (a `--sandbox` run, or `repl`) the raw log stays where it was written and no
    evidence is claimed.
    """

    def __init__(
        self,
        *,
        raw_dir: Path | None,
        base_dir: Path | None = None,
        gate: CommandGate | None = None,
        writer: RunArtifactWriter | None = None,
        named_commands: Mapping[str, object] | None = None,
        log: Callable[[str], None] | None = None,
        runner: SpawnFn | None = None,
    ) -> None:
        self._raw = raw_dir
        # Where a relative `cwd` resolves, and the directory a named command may not live inside.
        # Taken as a parameter rather than read off the process, so a caller whose entries belong to
        # another file (a scenario's own, BE-0501) resolves them against that file's directory.
        self._base = base_dir
        self._gate = gate if gate is not None else command_gate.current()
        self._writer = writer
        self._named = named_commands or {}
        self._say = log or _default_log
        # The process seam, so every decision and argv is testable without spawning anything.
        self._run = runner or _spawn

    def run_list(
        self,
        list_name: str,
        entries: Sequence[HookCommand],
        context: HookContext,
        *,
        inherited: HookOutputs | None = None,
        sandbox: SandboxRuntime | None = None,
    ) -> HookPhaseResult:
        """Run one list's entries in declaration order and return what they produced.

        Every entry runs to completion before the next starts. A failure does not stop the list for
        an `after` list — skipping the rest of a teardown is the outcome teardown exists to avoid —
        and for a `before` list the caller fails the run or the scenario on the first failure it is
        handed, so the remaining entries of that list are not run.

        Args:
            list_name: `beforeRun` / `afterRun` / `beforeScenario` / `afterScenario`; names the log
                directory and the report block.
            entries: The target's entries for that list.
            context: The run / scenario facts each command receives as `BAJUTSU_*` variables.
            inherited: Outputs already in scope (the run-level ones for a scenario list), visible to
                these commands and carried into the result.
            sandbox: This target's per-run container, when the worker's policy sandboxes its
                commands (BE-0492); each entry then runs inside it instead of on the host.
        """
        result = HookPhaseResult(outputs=inherited or HookOutputs())
        after = list_name.startswith("after")
        for index, entry in enumerate(entries):
            if after and entry.on != "always" and entry.on != context.verdict:
                result.records.append(HookRecord(list_name, entry.label, None, skipped=True))
                continue
            record, failure, values = self._run_entry(
                list_name, index, entry, context, result, sandbox
            )
            result.records.append(record)
            if failure is not None:
                result.failures.append(failure)
                if not after:
                    break
                continue
            if entry.name is not None:
                result.outputs.values[entry.name] = values
                if entry.mask:
                    result.outputs.masked.add(entry.name)
                    self._mask(values.values())
        return result

    def _run_entry(
        self,
        list_name: str,
        index: int,
        entry: HookCommand,
        context: HookContext,
        so_far: HookPhaseResult,
        sandbox: SandboxRuntime | None = None,
    ) -> tuple[HookRecord, HookFailure | None, dict[str, str]]:
        slot = entry.name or str(index)
        stem = f"{list_name}/{context.target}"
        if context.scenario_index is not None:
            stem = f"{stem}/{context.scenario_index:02d}"
        log_name = f"{HOOK_FIELD}/{stem}/{slot}.log"
        raw_log, raw_out = self._raw_paths(stem, slot)
        try:
            argv, env, cwd, sandboxed = self._resolve(
                entry, context, so_far.outputs, raw_out, sandbox
            )
        except (CommandRefused, NamedCommandError, SandboxError, ValueError) as e:
            return (
                HookRecord(list_name, entry.label, None, failure=str(e)),
                HookFailure(list_name, entry.label, str(e)),
                {},
            )
        self._say(f"{list_name}: {entry.label} — running")
        reason: str | None = None
        try:
            code = self._run(argv, env, cwd, entry.timeout, raw_log)
        except subprocess.TimeoutExpired:
            code, reason = -1, f"timed out after {entry.timeout:g}s"
        except OSError as e:  # the executable is missing / not executable
            code, reason = -1, f"could not start: {e}"
        if reason is None and code != 0:
            reason = f"exited {code}"
        self._gate.record(
            self._record_field(list_name, slot),
            entry.cmd if entry.cmd is not None else f"use:{entry.use}",
            sandboxed=sandboxed,
            exit_code=code,
            cwd=cwd,
        )
        values: dict[str, str] = {}
        if reason is None and raw_out is not None:
            values, why = parse_outputs(_read_regular(raw_out) or "")
            if why is not None:
                reason = f"wrote a malformed $BAJUTSU_OUTPUT file: {why}"
        log = self.copy_log(raw_log, log_name)
        failure = HookFailure(list_name, entry.label, reason) if reason is not None else None
        return HookRecord(list_name, entry.label, log, failure=reason), failure, values

    def _record_field(self, list_name: str, slot: str) -> str:
        """How `manifest.json` names the command's declaring field (BE-0492)."""
        return f"{HOOK_FIELD}.{list_name}[{slot}].cmd"

    def _raw_paths(self, stem: str, slot: str) -> tuple[Path | None, Path | None]:
        """This entry's raw log and output file, created owner-only, or `(None, None)`."""
        if self._raw is None:
            return None, None
        directory = make_run_dir(self._raw / stem)
        log, out = directory / f"{slot}.log", directory / f"{slot}.output"
        for path in (log, out):
            # Truncated, not appended: the output file is a *fresh, empty* file per entry, so a key
            # one entry wrote can never be read as a later entry's own value.
            path.write_text("", encoding="utf-8")
            restrict_file(path)
        return log, out

    def _resolve(
        self,
        entry: HookCommand,
        context: HookContext,
        outputs: HookOutputs,
        raw_out: Path | None,
        sandbox: SandboxRuntime | None,
    ) -> tuple[list[str], dict[str, str], Path | None, bool]:
        """This entry's argv, environment, and working directory, with the gate consulted first.

        Returns the four as `(argv, env, cwd, sandboxed)`; `sandboxed` is what the command record
        states, so the report never claims a command ran in a container when it ran on the host.
        """
        named = self._resolve_named(entry) if entry.use is not None else None
        if named is not None:
            # A named command's code is the operator's own, so it runs on the host under every mode
            # but `deny` — inside no container, and with only the variables the worker listed.
            return [named.path], {**os.environ, **context.env(), **named.env}, None, False
        assert entry.cmd is not None  # the schema admits exactly one of `cmd` / `use`
        action = self._gate.check("command", self._record_field("hooks", entry.label), entry.cmd)
        argv = shlex.split(entry.cmd)
        if not argv:
            raise ValueError(f"`cmd` {entry.cmd!r} splits into no command")
        cwd = self._cwd(entry.cwd)
        env = {**os.environ, **context.env(), **outputs.env(), **entry.env}
        if raw_out is not None:
            env["BAJUTSU_OUTPUT"] = str(raw_out)
        else:
            env.pop("BAJUTSU_OUTPUT", None)
        if action != "sandbox":
            return argv, env, cwd, False
        if sandbox is None:
            raise SandboxError(
                f"{entry.label}: the worker's commandExecution sandboxes this config's commands, "
                "but no `hooks.sandbox` container was started for this target (BE-0491)"
            )
        wrapped, _ = sandbox.wrap(argv, env, cwd)
        # The container's own cwd and environment are in the argv; the host-side spawn passes none.
        return wrapped, {**os.environ}, None, True

    def _resolve_named(self, entry: HookCommand) -> ResolvedNamedCommand:
        """The worker's script for `use:`, with the variables this entry sets (BE-0492)."""
        assert entry.use is not None
        self._gate.check("named", self._record_field("hooks", entry.label), f"use:{entry.use}")
        from bajutsu.common.capability.command_policy import NamedCommand

        named: Mapping[str, NamedCommand] = {
            k: v for k, v in self._named.items() if isinstance(v, NamedCommand)
        }
        return resolve_named_command(
            named, entry.use, config_dir=self._base or Path.cwd(), env=entry.env
        )

    def _cwd(self, configured: str | None) -> Path | None:
        """A relative `cwd` resolved against this runner's base directory."""
        if configured is None:
            return self._base
        path = Path(configured)
        return path if path.is_absolute() else (self._base or Path.cwd()) / path

    def copy_log(self, raw_log: Path | None, name: str) -> str | None:
        """Copy one raw log into the run directory with secrets masked, or None.

        Called when the entry exits, and again at the end of the run, for output a background
        process wrote after its script returned. The copy goes by the name Bajutsu created, never as
        a directory sweep, and refuses anything that is not a regular file.
        """
        if raw_log is None or self._writer is None:
            return None
        text = _read_regular(raw_log)
        if text is None:
            return None
        self._writer.write_text(name, text)
        return name

    def _mask(self, values: Iterable[str]) -> None:
        if self._writer is not None:
            self._writer.add_secret_values([v for v in values if v])

    def recopy(self, records: Iterable[HookRecord]) -> None:
        """Take the masked copy of every log again, for what a background process wrote later."""
        for record in records:
            if record.log is None:
                continue
            raw = self._raw / record.log.removeprefix(f"{HOOK_FIELD}/") if self._raw else None
            self.copy_log(raw, record.log)

    def cleanup(self) -> None:
        """Remove this run's raw area, so a plaintext secret never outlives the run."""
        if self._raw is None:
            return
        with contextlib.suppress(OSError):
            shutil.rmtree(self._raw)


def _spawn(
    argv: list[str],
    env: dict[str, str],
    cwd: Path | None,
    timeout: float,
    log_path: Path | None,
) -> int:
    """Run *argv* to completion in its own process group, returning its exit code.

    Output goes to the files the environment already points at (`BAJUTSU_OUTPUT`) and to the raw log
    the caller opened, never to a pipe: a server a script leaves running would otherwise hold a pipe
    open and keep the runner waiting after the script itself exited. On a timeout the whole process
    group is stopped, so a command's own children go with it.

    Raises:
        subprocess.TimeoutExpired: The command outlived *timeout*.
        OSError: The command could not be started at all.
    """
    with contextlib.ExitStack() as stack:
        sink = (
            stack.enter_context(log_path.open("a", encoding="utf-8"))
            if log_path is not None
            else subprocess.DEVNULL
        )
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT if log_path is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(proc)
            raise


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM the command's process group, then SIGKILL after a short grace. Idempotent."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


def run_phase(
    runner: HookRunner,
    list_name: str,
    phases: Mapping[str, HookPhases],
    context: Callable[[str], HookContext],
    *,
    inherited: HookOutputs | None = None,
    sandboxes: Mapping[str, SandboxRuntime] | None = None,
) -> HookPhaseResult:
    """Run one list across every target a run or a scenario drives, and merge the outcome.

    A run executes the hooks of every target it drives. Within a `before` list the targets run in
    the order they were declared; an `after` list walks them in reverse, so the last target prepared
    is the first one torn down. A `before` list stops at the first failing target, since the work it
    was preparing for will not happen; an `after` list visits every target, as teardown must.

    Args:
        runner: The run's hook runner, which owns the raw area and the gate.
        list_name: Which of the four lists to run.
        phases: Each target's resolved hooks, keyed by target name in declared order.
        context: Builds the context for one target's commands, given the target name.
        inherited: Outputs already in scope, visible to these commands.
        sandboxes: The per-run container of each target whose commands the worker sandboxes.
    """
    merged = HookPhaseResult(outputs=inherited or HookOutputs())
    after = list_name.startswith("after")
    names = list(phases)
    for target in reversed(names) if after else names:
        entries = phases[target].lists[list_name]
        if not entries:
            continue
        one = runner.run_list(
            list_name,
            entries,
            context(target),
            inherited=merged.outputs,
            sandbox=(sandboxes or {}).get(target),
        )
        merged.outputs = one.outputs
        merged.failures += one.failures
        merged.records += one.records
        if one.failures and not after:
            break
    return merged


def duplicate_hook_name(phases: Mapping[str, HookPhases]) -> str | None:
    """Why two targets of this run share a hook `name`, or None (BE-0491).

    A name is unique across the four lists of every target a run drives, so a
    `${hooks.<name>.<key>}` token never resolves ambiguously. One target's own duplicates are
    caught when its config loads; this is the cross-target half, which no single target can see.
    """
    owner: dict[str, str] = {}
    for target, phase in phases.items():
        for name in phase.named():
            if (first := owner.get(name)) is not None and first != target:
                return (
                    f"targets {first!r} and {target!r} both declare a hook named {name!r}; a hook "
                    f"name must be unique across the run so ${{hooks.{name}.*}} names one command"
                )
            owner[name] = target
    return None


def hook_refusal(phases: Mapping[str, HookPhases], gate: CommandGate) -> str | None:
    """Why *gate*'s policy will not run one of these hook commands, recording it, or None.

    Checked before any device work, so a run whose hook command can never run fails before it
    reserves a device — where `services.server`'s own refusal was checked before BE-0491 replaced
    it. A target whose commands the policy sandboxes needs a `hooks.sandbox` block to run them in,
    and the message names that block rather than failing later inside the container bring-up.
    """
    for target, phase in phases.items():
        for list_name, index, entry in phase.entries():
            kind: CommandKind = "named" if entry.use is not None else "command"
            action = gate.policy.action(kind)
            field_name = f"{HOOK_FIELD}.{list_name}[{entry.name or index}].cmd"
            if action == "refuse":
                try:
                    gate.refuse(field_name, entry.cmd or f"use:{entry.use}")
                except CommandRefused as e:
                    return str(e)
            if action == "sandbox" and phase.sandbox is None:
                return (
                    f"{field_name} cannot run: the worker's commandExecution sandboxes the commands "
                    f"of this {gate.policy.source} config, and target {target!r} declares no "
                    "`hooks.sandbox` block to run them in (BE-0491)"
                )
    return None
