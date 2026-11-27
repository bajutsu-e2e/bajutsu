"""One run's hook phases: `beforeRun` before the first scenario, `afterRun` after the last.

The shape every command that runs scenarios shares (`run`, `audit`) and that the scenario-less
commands (`record`, `crawl`, `repl`) reuse for their own bring-up, which is what `services.server`
did for them before BE-0491 replaced it. A session owns the raw area for the whole run, so the
outputs `beforeRun` wrote stay visible to each scenario's own hooks and to `afterRun`.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from bajutsu.common.capability.command_gate import CommandGate
from bajutsu.common.capability.command_policy import NamedCommand
from bajutsu.common.config import Effective, HookPhases
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.runner.conditions import ConditionContext, condition_context
from bajutsu.common.runner.hook_commands import (
    LOGS_SUBDIR,
    OUT_SUBDIR,
    HookContext,
    HookFailure,
    HookOutputs,
    HookPhaseResult,
    HookRecord,
    HookRunner,
    _default_log,
    component_of,
    duplicate_hook_name,
    raw_root,
    run_phase,
)
from bajutsu.common.runner.sandbox import (
    SandboxError,
    SandboxRuntime,
    grant_container_access,
)
from bajutsu.common.scenario import Scenario

_logger = logging.getLogger(__name__)


def phases_of(effs: Mapping[str, Effective], also: Collection[str] = ()) -> dict[str, HookPhases]:
    """Each target's hook lists, keyed by target name, keeping the declared order.

    Only the targets that declare a command at all, so a run of targets that declare none carries no
    hook phase and reaches its first scenario exactly as it did before BE-0491. A target named in
    *also* is kept whatever it declares: a scenario's own commands run in its container (BE-0501).
    """
    return {name: eff.hooks for name, eff in effs.items() if eff.hooks.any_command or name in also}


def run_tags(scenarios: Iterable[Scenario], primary: str) -> dict[str, tuple[tuple[str, ...], ...]]:
    """The tags of the scenarios that drive each target, which a run-level `when` reads (BE-0497).

    Args:
        scenarios: The run's selected scenarios, after expansion, `--tag`, and `--exclude`.
        primary: The target a scenario that declares none drives.
    """
    driven: dict[str, list[tuple[str, ...]]] = {}
    for s in scenarios:
        for target in s.target_names or [primary]:
            driven.setdefault(target, []).append(tuple(s.tags))
    return {target: tuple(tags) for target, tags in driven.items()}


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
    # Per target, the tags a run-level `when` reads (`run_tags`). Fixed when the session starts, so
    # `afterRun` decides on the set `beforeRun` saw, whatever the run did in between (BE-0497).
    tags: Mapping[str, Sequence[Sequence[str]]] = field(default_factory=dict)
    # The `afterRun` outcome, kept so the phase is idempotent: the pipeline runs it before it
    # assembles the report, and the command's own `finally` backstops the paths the pipeline never
    # reached (a device failure, a cancellation). Running a teardown twice would undo more than it
    # created.
    finished: HookPhaseResult | None = None
    # The per-run container of each target whose commands the worker's policy sandboxes (BE-0492).
    # One per target, started before its `beforeRun` and removed after its `afterRun`, so a server a
    # `beforeRun` script starts keeps serving for the whole run.
    sandboxes: dict[str, SandboxRuntime] = field(default_factory=dict)
    # Each target's platform and route and the environment snapshot a `when` reads (BE-0513),
    # fixed when the session starts, so `afterRun` decides from what `beforeRun` saw.
    conditions: ConditionContext = field(default_factory=lambda: ConditionContext({}, {}))

    def context(self, target: str, **extra: object) -> HookContext:
        """The context a command of *target* receives, with any scenario-level facts added.

        *target* is the entry's owner, the one target its `when` judges unless *extra* names others
        (a scenario's own entries judge every target the scenario drives).
        """
        facts: dict[str, object] = {
            "scenario_tags": self.tags.get(target, ()),
            "judged_targets": self.conditions.judged([target]),
            "process_env": self.conditions.env or {},
        }
        return HookContext(
            run_id=self.run_id,
            target=target,
            run_dir=self.run_dir,
            **{**facts, **extra},  # type: ignore[arg-type]
        )

    def before_run(self) -> HookPhaseResult:
        """Start any sandbox container, then run every target's `beforeRun` in declared order.

        A container that cannot start fails the phase like a failed command: no scenario runs, and
        the `afterRun` teardown still gets its turn.
        """
        for target, runtime in self.sandboxes.items():
            try:
                grant_container_access(runtime.raw_dir, root=runtime.raw_dir)
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

    def after_run(self, verdict: str, *, deadline: float | None = None) -> HookPhaseResult:
        """Run every target's `afterRun` in reverse declared order, then clear the raw area.

        Runs on every path out of the run, a cancelled one included, and keeps each scenario's
        verdict whatever it finds: a teardown failure is recorded and fails the run's own exit code,
        never a scenario's result. *deadline* bounds it after a cancel, from the slice of the grace
        window a scenario's own `cleanup` is bounded by, so a `SIGTERM` is not held off for one more
        `timeout` per entry.
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
            deadline=deadline,
        )
        self.records += result.records
        # Latched the moment the phase itself is done, before anything that could raise: a second
        # `after_run` would re-run every teardown command, which would undo more than the `before`
        # entries created.
        self.finished = result
        for runtime in self.sandboxes.values():
            runtime.stop()
        try:
            # Taken again before the raw area goes: a background process a script started may have
            # written to its log after the script itself exited.
            self.runner.recopy(self.records)
        except OSError as e:
            # Evidence, not the verdict: a full disk or a read-only run directory must not end the
            # run here, and must not leave the raw area behind either.
            _logger.warning("afterRun: copying the hook logs into the run directory failed (%s)", e)
        finally:
            self.runner.cleanup()
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
    scenario_targets: Collection[str] = (),
    tags: Mapping[str, Sequence[Sequence[str]]] | None = None,
    conditions: ConditionContext | None = None,
) -> HookSession | None:
    """The hook session for this run, or None when neither a target nor a scenario declares one.

    *scenario_targets* names the targets whose container a scenario's own commands may run in
    (BE-0501), which is non-empty exactly when some scenario declares a command. *conditions* are
    the facts a `when` reads (BE-0513); None builds them from the targets' own config and this
    process's environment, for a command with no `--backend` or `--udid` override.

    Returns None rather than an empty session, so every call site can tell at a glance that a run
    without hooks does no hook work at all.

    Raises:
        ValueError: Two targets of this run share a hook `name`, which would leave a
            `${hooks.<name>.<key>}` token ambiguous.
    """
    phases = phases_of(effs, scenario_targets)
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
        # Absolute for the same reason as the raw area: `BAJUTSU_RUN_DIR` must name a path a command
        # running in another working directory can still reach.
        run_dir=run_dir.resolve() if run_dir is not None else None,
        outputs=HookOutputs(),
        tags=tags or {},
        conditions=conditions if conditions is not None else condition_context(effs),
        sandboxes=_sandboxes(
            phases, gate=gate, raw_dir=raw, base_dir=base_dir, scenario_targets=scenario_targets
        ),
    )


def _sandboxes(
    phases: Mapping[str, HookPhases],
    *,
    gate: CommandGate | None,
    raw_dir: Path,
    base_dir: Path | None,
    scenario_targets: Collection[str],
) -> dict[str, SandboxRuntime]:
    """One container per target whose command lines the worker's policy sandboxes (BE-0492).

    A scenario's own commands are judged by the scenario's source, so a target may need a container
    for them alone (BE-0501).

    A target that declares no `hooks.sandbox` block gets none, and its first sandboxed entry then
    fails naming that block — the same message the pre-flight raises before any device work, for a
    run that reached here another way.
    """
    from bajutsu.common.capability import command_gate as gate_module

    judge = gate if gate is not None else gate_module.current()
    scenario_judge = judge.for_scenario()
    # A scenario's own entry a component in the config's tree added runs under the stricter of the
    # two sources (BE-0512), which may sandbox it where the scenario's source alone would not.
    joined = replace(scenario_judge.policy, component_source=judge.policy.source)
    scenario = "sandbox" in (scenario_judge.policy.action("command"), joined.action("command"))

    def sandboxes(phase: HookPhases) -> bool:
        return any(
            judge.for_entry(component_of(entry)).policy.action("command") == "sandbox"
            for _, _, entry in phase.entries()
        )

    return {
        target: SandboxRuntime(
            sandbox=phase.sandbox,
            bundle_cwd=base_dir or Path.cwd(),
            raw_dir=raw_dir / OUT_SUBDIR,
            # Docker's own output, in the half of the raw area no container can reach: a container
            # that cannot start then says why instead of leaving a bare exit code.
            docker_log=raw_dir / LOGS_SUBDIR / f"docker-{target}.log",
            log=_default_log,
        )
        for target, phase in phases.items()
        if phase.sandbox is not None
        and (sandboxes(phase) or (scenario and target in scenario_targets))
    }
