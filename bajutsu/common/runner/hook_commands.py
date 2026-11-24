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
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
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
from bajutsu.common.config.schema.hooks import COMMAND_LISTS
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.run_meta.artifact_perms import make_run_dir
from bajutsu.common.runner.sandbox import (
    SandboxError,
    SandboxRuntime,
    grant_container_access,
)

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
# The raw area has two halves. `out/` is the only one a sandboxed command can reach: it is mounted
# into the container, so the container may create and write the output files there. `logs/` is never
# mounted — under `sandbox` the host captures the `docker exec` client's own output, so the
# container never needs to write a log at all — which keeps a command from planting a link at a
# later entry's log path and having the host write through it.
OUT_SUBDIR = "out"
LOGS_SUBDIR = "logs"
# The file naming the process that owns a raw directory, so a stale sweep never deletes the raw area
# of a run that is still going — several workers run side by side on one machine.
_OWNER_FILE = "owner.pid"


def safe_segment(value: str, what: str) -> str:
    """*value* as one path segment, or raise: it must not climb, nest, or hide.

    A target name is an unvalidated map key under `targets:`, and it becomes a directory of the raw
    area and of the evidence tree. A name like `../../x` would otherwise appear to write outside
    both, so it is refused where the path is built rather than trusted.

    Raises:
        ValueError: *value* is empty, holds a separator, or starts with a dot.
    """
    if not value or value.startswith(".") or "/" in value or "\\" in value or value == "..":
        raise ValueError(
            f"{what} {value!r} cannot name a directory of the run's evidence: it must be one path "
            "segment, without a separator and not starting with a dot"
        )
    return value


def _safe_dir(root: Path, *segments: str) -> Path:
    """Create `root/segments…` one level at a time, refusing a symbolic link at any level.

    `mkdir(parents=True)` would happily walk a link a sandboxed command planted inside the mounted
    half of the raw area, so each level is created or checked on its own and a link anywhere on the
    path fails loudly.

    Raises:
        ValueError: A component of the path exists and is a symbolic link.
    """
    path = make_run_dir(root)
    for segment in segments:
        path = path / segment
        if path.is_symlink():
            raise ValueError(f"refusing to use a symlinked hook directory: {path}")
        path = make_run_dir(path)
    return path


def _create_fresh(path: Path, mode: int) -> None:
    """Create *path* empty, failing rather than writing through a link that is already there.

    `O_EXCL | O_NOFOLLOW` is what makes this safe in the half of the raw area a sandboxed command
    can write: an existing file (a link it planted at a later entry's predictable path, say) is
    removed first, and if it reappears between the two calls the create fails instead of truncating
    whatever it points at.

    Raises:
        OSError: The file could not be created, a planted link included.
    """
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode))


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
    # The evidence subdirectory this pass writes under (`<engine>` in a cross-browser matrix), so
    # one pass's logs never overwrite another's. Empty for a run that writes at the top level.
    log_prefix: str = ""
    # Stands in for the target in the log path and the command record, for a scenario's own entries
    # (BE-0501): `BAJUTSU_TARGET` still names the target the scenario drives first.
    level: str | None = None
    # The tags an entry's `when` reads, one collection per scenario (BE-0497): the run's scenarios
    # that drive this target for a run-level list, the scenario's own for a scenario-level one.
    # Empty, the case of a command that runs no scenario file, matches no condition.
    scenario_tags: Sequence[Sequence[str]] = ()

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

    def copy(self) -> HookOutputs:
        """An independent copy, so a caller that adds to it cannot write into the original."""
        return HookOutputs(
            values={name: dict(keys) for name, keys in self.values.items()}, masked=set(self.masked)
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
    # Which target's phase this entry belongs to, so a report labels the block rather than
    # inferring it from the log path.
    target: str
    # The run-dir-relative path of the masked log, or None when nothing was copied (no run
    # directory, or the raw file was refused).
    log: str | None
    # The scenario this entry belongs to, by run order, or None for a run-level entry. Parallel
    # workers append to one list, so the report sorts on this rather than on arrival order.
    scenario: int | None = None
    skipped: bool = False
    # The entry's `when`, as a reader reads it, when that condition is what skipped it (BE-0497).
    when: str | None = None
    failure: str | None = None
    # Why it was skipped, when the reason is not simply its `on` (a spent teardown budget).
    reason: str | None = None
    # The scenario whose own list declared the entry, or None for a target's (BE-0501): the report
    # draws the two levels as separate blocks.
    declared_by: str | None = None


@dataclass
class HookPhaseResult:
    """The outcome of running one list: its outputs, its failures, and its report records."""

    outputs: HookOutputs = field(default_factory=HookOutputs)
    failures: list[HookFailure] = field(default_factory=list)
    records: list[HookRecord] = field(default_factory=list)
    # The keys of the phases whose entries began, so a caller can tell which level a `before` list
    # stopped ahead of (BE-0501).
    reached: list[str] = field(default_factory=list)

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
    # Absolute: an entry may run in a working directory of its own (`cwd`, or a config in a
    # subdirectory), where a relative runs directory would name a path the command cannot reach.
    root = Path(runs_dir).resolve() / RAW_DIRNAME
    make_run_dir(root)
    _sweep_stale(root, run_id)
    own = make_run_dir(root / run_id)
    (own / _OWNER_FILE).write_text(str(os.getpid()), encoding="utf-8")
    # Both halves up front, owner-only: the writable one is the container's bind-mount source, and
    # a missing source would have the Docker daemon create it as root instead.
    make_run_dir(own / OUT_SUBDIR)
    make_run_dir(own / LOGS_SUBDIR)
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

    `O_NONBLOCK` covers the other shape the same command can plant: a named pipe, which
    `O_NOFOLLOW` admits and whose open blocks for as long as no one writes to it. The entry's own
    `timeout` has already been spent by then, so a blocking open would hold the run with nothing
    left to bound it. The flag is a no-op for the regular file this is meant to read.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
        if key in values:
            return {}, f"line {number} writes {key!r} again, which line order alone would resolve"
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
        cwd_base: Path | None = None,
        deadline: float | None = None,
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
            cwd_base: Where these entries' relative `cwd` resolves, in place of the runner's own
                base directory. For entries declared in another file — a scenario's own (BE-0501) —
                that is the directory of the file that declared them. It does not move the
                directory a named command may not live inside, which stays the config's own.
            deadline: A monotonic instant the whole list must finish by, which a cancelled run's
                teardown sets from the slice of the grace window `cleanup` already uses. Each
                entry's own `timeout` is capped by what is left of it, and once it is spent the
                remaining entries are recorded as skipped rather than run — the designed bound, not
                a failure.
        """
        # Copied, never shared: a scenario's own lists start from the run-level outputs, and two
        # workers writing into that one object would leak one scenario's values into another's.
        result = HookPhaseResult(
            outputs=inherited.copy() if inherited is not None else HookOutputs()
        )
        after = list_name.startswith("after")
        for index, entry in enumerate(entries):
            if entry.when is not None and not any(
                entry.when.matches(tags) for tags in context.scenario_tags
            ):
                result.records.append(
                    HookRecord(
                        list_name,
                        entry.label,
                        context.target,
                        None,
                        scenario=context.scenario_index,
                        skipped=True,
                        when=str(entry.when),
                    )
                )
                self._gate_for(context).record_skip(
                    self._record_field(list_name, entry.name or str(index), context),
                    entry.cmd if entry.cmd is not None else f"use:{entry.use}",
                    cwd=self._cwd(entry.cwd, cwd_base) if entry.cmd is not None else None,
                )
                continue
            if after and entry.on != "always" and entry.on != context.verdict:
                # The report shows a bare skip otherwise, and a caller that forgot the verdict
                # would silently skip every conditional entry of the list.
                assert context.verdict is not None, "an after list needs the run's verdict"
                result.records.append(
                    HookRecord(
                        list_name,
                        entry.label,
                        context.target,
                        None,
                        scenario=context.scenario_index,
                        skipped=True,
                        reason=f"`on: {entry.on}` does not match the verdict {context.verdict!r}",
                    )
                )
                continue
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                self._say(f"{list_name}: {entry.label} — teardown budget spent; not run")
                result.records.append(
                    HookRecord(
                        list_name,
                        entry.label,
                        context.target,
                        None,
                        scenario=context.scenario_index,
                        skipped=True,
                        reason="teardown budget spent",
                    )
                )
                continue
            record, failure, values = self._run_entry(
                list_name, index, entry, context, result, sandbox, cwd_base, left
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
                    # The values themselves were registered with the writer in `_run_entry`, before
                    # this entry's own log was copied; this records which hook asked for it.
                    result.outputs.masked.add(entry.name)
        if context.level is not None:
            result.records = [replace(r, declared_by=context.scenario) for r in result.records]
        return result

    def _run_entry(
        self,
        list_name: str,
        index: int,
        entry: HookCommand,
        context: HookContext,
        so_far: HookPhaseResult,
        sandbox: SandboxRuntime | None = None,
        cwd_base: Path | None = None,
        budget: float | None = None,
    ) -> tuple[HookRecord, HookFailure | None, dict[str, str]]:
        slot = entry.name or str(index)
        try:
            segments = [
                *(
                    [safe_segment(context.log_prefix, "an evidence prefix")]
                    if context.log_prefix
                    else []
                ),
                safe_segment(list_name, "a hook list"),
                safe_segment(context.level or context.target, "a target name"),
            ]
            if context.scenario_index is not None:
                segments.append(f"{context.scenario_index:02d}")
            raw_log, raw_out = self._raw_paths(segments, slot)
        except (OSError, ValueError) as e:
            return (
                HookRecord(
                    list_name,
                    entry.label,
                    context.target,
                    None,
                    scenario=context.scenario_index,
                    failure=str(e),
                ),
                HookFailure(list_name, entry.label, str(e)),
                {},
            )
        if sandbox is not None and raw_out is not None:
            # Created after the container started, so the hand-over is per entry, not once at start.
            grant_container_access(raw_out, root=self._raw / OUT_SUBDIR if self._raw else None)
        log_name = "/".join([HOOK_FIELD, *segments, f"{slot}.log"])
        field_name = self._record_field(list_name, slot, context)
        try:
            argv, env, cwd, sandboxed = self._resolve(
                entry, context, so_far.outputs, raw_out, sandbox, cwd_base, field_name=field_name
            )
        except (CommandRefused, NamedCommandError, SandboxError, ValueError) as e:
            return (
                HookRecord(
                    list_name,
                    entry.label,
                    context.target,
                    None,
                    scenario=context.scenario_index,
                    failure=str(e),
                ),
                HookFailure(list_name, entry.label, str(e)),
                {},
            )
        self._say(f"{list_name}: {entry.label} — running")
        reason: str | None = None
        timeout = entry.timeout if budget is None else min(entry.timeout, budget)
        try:
            code = self._run(argv, env, cwd, timeout, raw_log)
        except subprocess.TimeoutExpired as expired:
            note = expired.output.decode(errors="replace") if expired.output else ""
            code = -1
            reason = f"timed out after {timeout:g}s{f' {note}' if note else ''}"
        except OSError as e:  # the executable is missing / not executable
            code, reason = -1, f"could not start: {e}"
        if reason is None and code != 0:
            reason = f"exited {code}"
        self._gate_for(context).record(
            field_name,
            entry.cmd if entry.cmd is not None else f"use:{entry.use}",
            sandboxed=sandboxed,
            exit_code=code,
            cwd=cwd,
        )
        values: dict[str, str] = {}
        if reason is None and raw_out is not None:
            # Three causes read the same through `None` — the file is gone, it is no longer the
            # regular file Bajutsu created, or it could not be opened — and all three mean the
            # entry's values are lost. Reporting them as an empty file would mark it green.
            text = _read_regular(raw_out)
            if text is None:
                reason = (
                    "its $BAJUTSU_OUTPUT file could not be read: it is gone, or is no longer the "
                    f"regular file Bajutsu created ({raw_out})"
                )
            else:
                values, why = parse_outputs(text)
                if why is not None:
                    reason = f"wrote a malformed $BAJUTSU_OUTPUT file: {why}"
        if reason is None and entry.mask:
            # Registered before the log is copied, so this entry's *own* log is masked too. The
            # writer masks every artifact written from that call on, and its log comes next.
            self._mask(values.values())
        log = self.copy_log(raw_log, log_name)
        failure = HookFailure(list_name, entry.label, reason) if reason is not None else None
        record = HookRecord(
            list_name,
            entry.label,
            context.target,
            log,
            scenario=context.scenario_index,
            failure=reason,
        )
        return record, failure, values

    def _gate_for(self, context: HookContext) -> CommandGate:
        """The gate that judges this entry, a scenario's own by the scenario's source (BE-0501)."""
        return self._gate if context.level is None else self._gate.for_scenario()

    def _record_field(self, list_name: str, slot: str, context: HookContext) -> str:
        """How `manifest.json` names the command's declaring field (BE-0492)."""
        prefix = "scenario." if context.level is not None else ""
        return f"{prefix}{HOOK_FIELD}.{list_name}[{slot}].cmd"

    def _raw_paths(self, segments: Sequence[str], slot: str) -> tuple[Path | None, Path | None]:
        """This entry's raw log and output file, created fresh, or `(None, None)`.

        The log lands in the half of the raw area no container can reach; the output file lands in
        the mounted half, since a sandboxed command has to be able to write it. Both are created
        empty rather than appended to, so a key one entry wrote can never be read as a later
        entry's own value.

        Raises:
            ValueError: A component of either path is a symbolic link.
            OSError: A file could not be created fresh (see `_create_fresh`).
        """
        if self._raw is None:
            return None, None
        log = _safe_dir(self._raw / LOGS_SUBDIR, *segments) / f"{slot}.log"
        out = _safe_dir(self._raw / OUT_SUBDIR, *segments) / f"{slot}.output"
        _create_fresh(log, 0o600)
        # The container runs as the worker's own user (see `sandbox.container_user`), so owner-only
        # is what lets it write; group and world still get nothing.
        _create_fresh(out, 0o600)
        return log, out

    def _resolve(
        self,
        entry: HookCommand,
        context: HookContext,
        outputs: HookOutputs,
        raw_out: Path | None,
        sandbox: SandboxRuntime | None,
        cwd_base: Path | None = None,
        *,
        field_name: str,
    ) -> tuple[list[str], dict[str, str], Path | None, bool]:
        """This entry's argv, environment, and working directory, with the gate consulted first.

        Returns the four as `(argv, env, cwd, sandboxed)`; `sandboxed` is what the command record
        states, so the report never claims a command ran in a container when it ran on the host.
        *field* is how `manifest.json` names this entry, so a refusal raised here is recorded under
        the same name as a command that ran.
        """
        if entry.use is not None:
            # A named command's code is the operator's own, so it runs on the host under every mode
            # but `deny`, inside no container. The worker's `env` list bounds what the *config* may
            # set, which is why `entry.env` passes through `resolve_named_command`; Bajutsu's own
            # context and the outputs of earlier commands are not the config's doing, so they reach
            # it like any other entry — the item has every later command see them.
            named = self._resolve_named(entry, field_name, context)
            env = {**os.environ, **context.env(), **outputs.env(), **named.env}
            if raw_out is not None:
                env["BAJUTSU_OUTPUT"] = str(raw_out)
            else:
                env.pop("BAJUTSU_OUTPUT", None)  # never a stale value from the worker's own
            return [named.path], env, None, False
        assert entry.cmd is not None  # the schema admits exactly one of `cmd` / `use`
        action = self._gate_for(context).check("command", field_name, entry.cmd)
        argv = shlex.split(entry.cmd)
        if not argv:
            raise ValueError(f"`cmd` {entry.cmd!r} splits into no command")
        cwd = self._cwd(entry.cwd, cwd_base)
        # What Bajutsu itself gives the command, kept apart from the worker's own environment: the
        # container receives this set and the entry's own `env`, and nothing else.
        bajutsu_env = {**context.env(), **outputs.env()}
        if raw_out is not None:
            bajutsu_env["BAJUTSU_OUTPUT"] = str(raw_out)
        if action != "sandbox":
            env = {**os.environ, **bajutsu_env, **entry.env}
            if raw_out is None:
                # A stale value inherited from the worker's own environment would have the command
                # write its values into another run's file.
                env.pop("BAJUTSU_OUTPUT", None)
            return argv, env, cwd, False
        if sandbox is None:
            raise SandboxError(
                f"{entry.label}: the worker's commandExecution sandboxes this config's commands, "
                "but no `hooks.sandbox` container was started for this target (BE-0491)"
            )
        # The container's own cwd and environment ride in the `docker exec` argv. The host-side
        # spawn of that client passes the worker's environment, which the client needs to reach the
        # daemon and which never crosses into the container.
        wrapped = sandbox.wrap(argv, bajutsu_env, entry.env, cwd)
        return wrapped, {**os.environ}, None, True

    def _resolve_named(
        self, entry: HookCommand, field_name: str, context: HookContext
    ) -> ResolvedNamedCommand:
        """The worker's script for `use:`, with the variables this entry sets (BE-0492)."""
        assert entry.use is not None
        self._gate_for(context).check("named", field_name, f"use:{entry.use}")
        from bajutsu.common.capability.command_policy import NamedCommand

        named: Mapping[str, NamedCommand] = {
            k: v for k, v in self._named.items() if isinstance(v, NamedCommand)
        }
        return resolve_named_command(
            named, entry.use, config_dir=self._base or Path.cwd(), env=entry.env
        )

    def _cwd(self, configured: str | None, base: Path | None = None) -> Path | None:
        """A relative `cwd` resolved against *base*, else this runner's own base directory."""
        root = base if base is not None else self._base
        if configured is None:
            return root
        path = Path(configured)
        return path if path.is_absolute() else (root or Path.cwd()) / path

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
            self._say(
                f"{name}: the raw hook log was refused — it is no longer the regular file Bajutsu "
                "created, so no log reaches the run's evidence"
            )
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
            raw = (
                self._raw / LOGS_SUBDIR / record.log.removeprefix(f"{HOOK_FIELD}/")
                if self._raw
                else None
            )
            self.copy_log(raw, record.log)

    def cleanup(self) -> None:
        """Remove this run's raw area, so a plaintext secret never outlives the run.

        A failure never fails the run — the verdict does not depend on cleanup — but it is said out
        loud, because what it leaves behind is the unmasked output of a `mask: true` hook.
        """
        if self._raw is None:
            return
        try:
            shutil.rmtree(self._raw)
        except OSError as e:
            self._say(
                f"the raw hook area could not be removed: {e} — it may hold unmasked hook output; "
                f"remove {self._raw} by hand"
            )


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
        except subprocess.TimeoutExpired as expired:
            # The kill's own failure travels on the timeout rather than replacing it: a
            # `PermissionError` escaping here would be reported as "could not start", the opposite
            # of what happened, and the surviving process would go unmentioned.
            if (note := _terminate(proc)) is not None:
                expired.output = note.encode()
            raise


def _terminate(proc: subprocess.Popen[bytes]) -> str | None:
    """SIGTERM the command's process group, then SIGKILL after a short grace. Idempotent.

    Returns:
        What went wrong, when the command could not be stopped, so the caller can say it beside the
        timeout. None when the command is gone.
    """
    if proc.poll() is not None:
        return None
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(OSError):
            proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError) as e:
            try:
                proc.kill()
            except OSError:
                return f"and could not be stopped: {e}"
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return "and is still running: it did not go on SIGKILL"
    return None


def hook_record_dicts(records: Iterable[HookRecord]) -> list[dict[str, object]]:
    """The hook records as the manifest carries them, in a deterministic order.

    Sorted by the phase's place in one run's lifecycle, then by scenario, rather than kept in
    arrival order: parallel workers append their scenarios' records to one list, so arrival order
    depends on thread timing and two identical runs would write different evidence. The sort is
    stable, so the entries of one scenario keep their declaration order.
    """
    order = {name: i for i, name in enumerate(COMMAND_LISTS)}
    out: list[dict[str, object]] = []
    for record in sorted(
        records, key=lambda r: (order.get(r.list_name, len(order)), r.scenario or 0)
    ):
        entry: dict[str, object] = {
            "list": record.list_name,
            "entry": record.label,
            "target": record.target,
        }
        if record.scenario is not None:
            entry["scenario"] = record.scenario
        if record.declared_by is not None:
            entry["declaredBy"] = record.declared_by
        if record.log is not None:
            entry["log"] = record.log
        if record.skipped:
            entry["skipped"] = True
        if record.when is not None:
            entry["when"] = record.when
        if record.reason is not None:
            entry["reason"] = record.reason
        if record.failure is not None:
            entry["failure"] = record.failure
        out.append(entry)
    return out


def run_phase(
    runner: HookRunner,
    list_name: str,
    phases: Mapping[str, HookPhases],
    context: Callable[[str], HookContext],
    *,
    inherited: HookOutputs | None = None,
    sandboxes: Mapping[str, SandboxRuntime] | None = None,
    cwd_bases: Mapping[str, Path] | None = None,
    deadline: float | None = None,
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
        cwd_bases: Where the relative `cwd` of one key's entries resolves, for entries declared in
            another file; see `HookRunner.run_list`.
        deadline: A monotonic instant every entry must finish by, which a cancelled run's teardown
            sets; see `HookRunner.run_list`.
    """
    merged = HookPhaseResult(outputs=inherited or HookOutputs())
    after = list_name.startswith("after")
    names = list(phases)
    for target in reversed(names) if after else names:
        entries = phases[target].lists[list_name]
        if not entries:
            continue
        merged.reached.append(target)
        one = runner.run_list(
            list_name,
            entries,
            context(target),
            inherited=merged.outputs,
            sandbox=(sandboxes or {}).get(target),
            cwd_base=(cwd_bases or {}).get(target),
            deadline=deadline,
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


def hook_refusal(
    phases: Mapping[str, HookPhases], gate: CommandGate, *, prefix: str = ""
) -> str | None:
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
            field_name = f"{prefix}{HOOK_FIELD}.{list_name}[{entry.name or index}].cmd"
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


class WorkerIndex:
    """A stable zero-based index per worker thread, for `BAJUTSU_WORKER`.

    `run --workers` maps the scenarios over a thread pool, which carries no worker number of its
    own, so one is assigned here on each thread's first scenario and kept for the rest of the run. A
    script that touches shared state, or that starts a server per scenario, can then pick its data
    or its port by that index and never collide with a sibling worker.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids: dict[int, int] = {}

    def current(self) -> int:
        """This thread's index, assigned on first use."""
        thread = threading.get_ident()
        with self._lock:
            return self._ids.setdefault(thread, len(self._ids))


def missing_hook_key(referenced: Iterable[str], outputs: HookOutputs) -> str | None:
    """Why a `${hooks.<name>.<key>}` token this scenario uses has no value, or None (BE-0491).

    The hook *name* is checked statically when the run loads; a *key* can only be checked once the
    command has run, since the command decides which keys it writes. A key it never wrote names both
    the hook and the key, rather than leaving the token in the step's own text.
    """
    for token in sorted(referenced):
        if not token.startswith("hooks."):
            continue
        _, _, rest = token.partition("hooks.")
        name, _, key = rest.partition(".")
        if not key:
            return f"${{{token}}} names no output key; a token reads `hooks.<name>.<key>`"
        if name not in outputs.values:
            return f"${{{token}}}: hook {name!r} wrote no output at all"
        if key not in outputs.values[name]:
            wrote = ", ".join(sorted(outputs.values[name])) or "nothing"
            return f"${{{token}}}: hook {name!r} never wrote {key!r} (it wrote {wrote})"
    return None


def unknown_hook_tokens(
    referenced: Iterable[str], readable: Iterable[str], *, skipped: Iterable[str] = ()
) -> str | None:
    """Why a `${hooks.*}` token can never resolve, checked when the run loads, or None.

    A token naming no `beforeRun` or `beforeScenario` entry of a target this scenario drives is an
    error before anything runs, as is one naming a `beforeScenario` entry the scenario skips through
    `preconditions.inheritSetup: false` — the value would never be written, so the run would fail
    halfway through instead of at load.
    """
    names, skipped_names = set(readable), set(skipped)
    for token in sorted(referenced):
        if not token.startswith("hooks."):
            continue
        name = token.removeprefix("hooks.").partition(".")[0]
        if name in skipped_names:
            return (
                f"${{{token}}} reads the hook {name!r}, which this scenario skips: "
                "`preconditions.inheritSetup: false` skips the target's beforeScenario entries"
            )
        if name not in names:
            known = ", ".join(sorted(names)) or "none"
            return (
                f"${{{token}}} names no hook this scenario can read; a token reads a `beforeRun` or "
                f"`beforeScenario` entry of a target it drives (known: {known})"
            )
    return None


def unmet_hook_condition(
    referenced: Iterable[str],
    phases: Mapping[str, HookPhases],
    driven: Sequence[str],
    tags: Sequence[str],
    own: HookPhases | None = None,
) -> str | None:
    """Why a `${hooks.*}` token reads an entry whose `when` this scenario fails, or None (BE-0497).

    Tags are static once a scenario is expanded, so a value that would be missing surfaces at load
    rather than mid-run.

    Args:
        referenced: The tokens the scenario uses.
        phases: Every target of the config, so a run-level entry of a target this scenario does not
            drive is named as such.
        driven: The targets the scenario drives.
        tags: The expanded scenario's own tags.
        own: The scenario's own lists (BE-0501), when it declares any.
    """
    mine = {
        e.name: (list_name, e)
        for phase in [*(phases[t] for t in driven if t in phases), *([own] if own else [])]
        for list_name in ("beforeRun", "beforeScenario")
        for e in phase.lists[list_name]
        if e.name is not None
    }
    elsewhere = {
        e.name: target
        for target, phase in phases.items()
        if target not in driven
        for e in phase.before_run
        if e.name is not None and e.when is not None
    }
    carried = ", ".join(tags) or "none"
    for token in sorted(referenced):
        if not token.startswith("hooks."):
            continue
        name = token.removeprefix("hooks.").partition(".")[0]
        if name in mine:
            list_name, entry = mine[name]
            if entry.when is not None and not entry.when.matches(tags):
                return (
                    f"${{{token}}} reads the {list_name} hook {name!r}, which runs only when "
                    f"{{{entry.when}}} matches; this scenario's tags are: {carried}"
                )
        elif name in elsewhere:
            return (
                f"${{{token}}} reads the conditional beforeRun hook {name!r} of target "
                f"{elsewhere[name]!r}, which this scenario does not drive"
            )
    return None
