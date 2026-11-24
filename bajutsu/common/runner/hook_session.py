"""One run's hook phases: `beforeRun` before the first scenario, `afterRun` after the last.

The shape every command that runs scenarios shares (`run`, `audit`) and that the scenario-less
commands (`record`, `crawl`, `repl`) reuse for their own bring-up, which is what `services.server`
did for them before BE-0491 replaced it. A session owns the raw area for the whole run, so the
outputs `beforeRun` wrote stay visible to each scenario's own hooks and to `afterRun`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from bajutsu.common.capability.command_gate import CommandGate
from bajutsu.common.capability.command_policy import NamedCommand
from bajutsu.common.config import Effective, HookPhases
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.runner.hook_commands import (
    HookContext,
    HookFailure,
    HookOutputs,
    HookPhaseResult,
    HookRecord,
    HookRunner,
    duplicate_hook_name,
    raw_root,
    run_phase,
)
from bajutsu.common.runner.sandbox import SandboxError, SandboxRuntime


def phases_of(effs: Mapping[str, Effective]) -> dict[str, HookPhases]:
    """Each target's hook lists, keyed by target name, keeping the declared order.

    Only the targets that declare a command at all, so a run of targets that declare none carries no
    hook phase and reaches its first scenario exactly as it did before BE-0491.
    """
    return {name: eff.hooks for name, eff in effs.items() if eff.hooks.any_command}


@dataclass
class HookSession:
    """The hook runner for one run, plus what its `beforeRun` phase produced.

    Held across the whole run: `outputs` is what each scenario's own `beforeScenario` entries build
    on and what a `${hooks.<name>.<key>}` token resolves against, and `records` is what the report's
    hook blocks render.
    """

    runner: HookRunner
    phases: dict[str, HookPhases]
    run_id: str
    run_dir: Path | None = None
    outputs: HookOutputs = field(default_factory=HookOutputs)
    records: list[HookRecord] = field(default_factory=list)
    # The `afterRun` outcome, kept so the phase is idempotent: the pipeline runs it before it
    # assembles the report, and the command's own `finally` backstops the paths the pipeline never
    # reached (a device failure, a cancellation). Running a teardown twice would undo more than it
    # created.
    finished: HookPhaseResult | None = None
    # The per-run container of each target whose commands the worker's policy sandboxes (BE-0492).
    # One per target, started before its `beforeRun` and removed after its `afterRun`, so a server a
    # `beforeRun` script starts keeps serving for the whole run.
    sandboxes: dict[str, SandboxRuntime] = field(default_factory=dict)

    def context(self, target: str, **extra: object) -> HookContext:
        """The context a command of *target* receives, with any scenario-level facts added."""
        return HookContext(
            run_id=self.run_id,
            target=target,
            run_dir=self.run_dir,
            **extra,  # type: ignore[arg-type]
        )

    def before_run(self) -> HookPhaseResult:
        """Start any sandbox container, then run every target's `beforeRun` in declared order.

        A container that cannot start fails the phase like a failed command: no scenario runs, and
        the `afterRun` teardown still gets its turn.
        """
        for target, runtime in self.sandboxes.items():
            try:
                runtime.start()
            except SandboxError as e:
                return HookPhaseResult(
                    outputs=self.outputs,
                    failures=[HookFailure("beforeRun", f"{target} sandbox", str(e))],
                )
        result = run_phase(
            self.runner, "beforeRun", self.phases, self.context, sandboxes=self.sandboxes
        )
        self.outputs = result.outputs
        self.records += result.records
        return result

    def after_run(self, verdict: str) -> HookPhaseResult:
        """Run every target's `afterRun` in reverse declared order, then clear the raw area.

        Runs on every path out of the run, a cancelled one included, and keeps each scenario's
        verdict whatever it finds: a teardown failure is recorded and fails the run's own exit code,
        never a scenario's result.
        """
        if self.finished is not None:
            return self.finished
        result = run_phase(
            self.runner,
            "afterRun",
            self.phases,
            lambda target: self.context(target, verdict=verdict),
            inherited=self.outputs,
            sandboxes=self.sandboxes,
        )
        self.records += result.records
        for runtime in self.sandboxes.values():
            runtime.stop()
        # Taken again before the raw area goes: a background process a script started may have
        # written to its log after the script itself exited.
        self.runner.recopy(self.records)
        self.runner.cleanup()
        self.finished = result
        return result


def start_session(
    effs: Mapping[str, Effective],
    *,
    run_id: str,
    runs_dir: Path | None,
    run_dir: Path | None = None,
    gate: CommandGate | None = None,
    writer: RunArtifactWriter | None = None,
    named_commands: Mapping[str, NamedCommand] | None = None,
    base_dir: Path | None = None,
) -> HookSession | None:
    """The hook session for this run, or None when no target declares a hook command.

    Returns None rather than an empty session, so every call site can tell at a glance that a run
    without hooks does no hook work at all.

    Raises:
        ValueError: Two targets of this run share a hook `name`, which would leave a
            `${hooks.<name>.<key>}` token ambiguous.
    """
    phases = phases_of(effs)
    if not phases:
        return None
    if (clash := duplicate_hook_name(phases)) is not None:
        raise ValueError(clash)
    raw = raw_root(runs_dir, run_id)
    runner = HookRunner(
        raw_dir=raw,
        base_dir=base_dir,
        gate=gate,
        writer=writer,
        named_commands=named_commands,
    )
    return HookSession(
        runner=runner,
        phases=phases,
        run_id=run_id,
        run_dir=run_dir,
        outputs=HookOutputs(),
        sandboxes=_sandboxes(phases, gate=gate, raw_dir=raw, base_dir=base_dir),
    )


def _sandboxes(
    phases: Mapping[str, HookPhases],
    *,
    gate: CommandGate | None,
    raw_dir: Path,
    base_dir: Path | None,
) -> dict[str, SandboxRuntime]:
    """One container per target whose command lines the worker's policy sandboxes (BE-0492).

    A target that declares no `hooks.sandbox` block gets none, and its first sandboxed entry then
    fails naming that block — the same message the pre-flight raises before any device work, for a
    run that reached here another way.
    """
    from bajutsu.common.capability import command_gate as gate_module

    policy = (gate if gate is not None else gate_module.current()).policy
    if policy.action("command") != "sandbox":
        return {}
    return {
        target: SandboxRuntime(
            sandbox=phase.sandbox,
            bundle_cwd=base_dir or Path.cwd(),
            raw_dir=raw_dir,
        )
        for target, phase in phases.items()
        if phase.sandbox is not None
    }
