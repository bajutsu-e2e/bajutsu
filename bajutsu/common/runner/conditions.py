"""The run-time facts a hook condition reads, and the setup blocks and cleanup rules they decide.

A `when` reads the scenario's tags, the target it judges — that target's name, declared platform,
and route — and the environment of the `bajutsu run` process (BE-0497, BE-0513). This module builds
the per-target half once per run, so the hook commands, the setup blocks, the preflight, and the
report all decide a condition from one set of facts. Every comparison is a plain string match: no
probe command and no model takes part (prime directive 1), and identical files, flags, environments,
and destinations always decide the same way (prime directive 2).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from bajutsu.common.backends import IMPLEMENTED, resolve_actuators
from bajutsu.common.capability.settings_support import route_for
from bajutsu.common.config import Effective
from bajutsu.common.scenario import CleanupRule, Step
from bajutsu.common.scenario.models.hook_command import HookWhen, JudgedTarget, WhenFacts

# How a skipped setup block or cleanup rule is labelled, in the words a skipped command uses.
SKIPPED_WHEN = "skipped (when)"


@dataclass(frozen=True)
class ConditionContext:
    """What a `when` reads besides the scenario's tags: each target, and the process environment.

    Args:
        targets: Every target the run may judge, keyed by name, with its platform and route.
        env: The process environment, or None where it is unknown — `serve`, routing a job to a
            worker whose environment it cannot see. An `env` field then counts as holding, so a
            block keeps the needs it would have where it runs.
    """

    targets: Mapping[str, JudgedTarget]
    env: Mapping[str, str] | None
    # Holds every condition: a check that runs before the run knows its routes keeps every block,
    # so it validates a superset of the steps any run of the same files could fold in.
    keep_all: bool = False

    @classmethod
    def every_block(cls) -> ConditionContext:
        """A context in which every condition holds, for a check made before the routes are known."""
        return cls({}, None, keep_all=True)

    def judged(self, names: Iterable[str]) -> tuple[JudgedTarget, ...]:
        """The judged targets for *names*; a name the run did not resolve has no platform or route."""
        return tuple(self.targets.get(name, JudgedTarget(name, "")) for name in names)

    def holds(self, when: HookWhen, names: Iterable[str], tags: Sequence[Sequence[str]]) -> bool:
        """Whether *when* holds for a scenario carrying *tags* that judges the targets *names*."""
        if self.keep_all:
            return True
        if self.env is None and when.env:
            if not (when.reads_tags or when.reads_target):
                return True
            # `model_copy` skips validation, so an emptied `env` cannot trip the empty-`when` check.
            when = when.model_copy(update={"env": {}})
        return when.holds(WhenFacts(tags=tags, targets=self.judged(names), env=self.env or {}))


def default_actuator(eff: Effective) -> str | None:
    """The actuator a run of *eff* selects when nothing overrides `backend`."""
    return next((a for a in resolve_actuators(list(eff.backend)) if a in IMPLEMENTED), None)


def condition_context(
    effs: Mapping[str, Effective],
    *,
    actuators: Mapping[str, str | None] | None = None,
    udids: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
    known_env: bool = True,
) -> ConditionContext:
    """The facts every condition of a run reads, fixed once before its first hook (BE-0513).

    Args:
        effs: Every target the run may judge.
        actuators: The actuator each target runs on, as `--backend` resolved it; a target left out
            uses its own `backend`.
        udids: The device spec each target runs on (`--udid`), which tells a real device and a live
            WebDriver endpoint from a simulator; a target left out uses `booted`.
        env: The environment to read; a snapshot of the process's own when None.
        known_env: False where the environment is unknown (`serve` routing a job).
    """
    actuators, udids = actuators or {}, udids or {}
    targets = {
        name: JudgedTarget(
            name,
            eff.schema_platform,
            route_for(
                actuators[name] if name in actuators else default_actuator(eff),
                eff,
                udids.get(name, "booted"),
            ),
        )
        for name, eff in effs.items()
    }
    if not known_env:
        return ConditionContext(targets, None)
    return ConditionContext(targets, dict(os.environ if env is None else env))


@dataclass(frozen=True)
class SkippedBlock:
    """A setup block or cleanup rule whose condition did not hold, as the report lists it."""

    # `setup` or `cleanup`.
    phase: str
    # The block's position in the folded list it was decided from, or the rule's `on`.
    label: str
    when: str

    def to_dict(self) -> dict[str, str]:
        """The manifest's form of this record."""
        return {"phase": self.phase, "label": self.label, "when": self.when}


def decide_setup(
    steps: Sequence[Step],
    judge: Iterable[str],
    tags: Sequence[str],
    context: ConditionContext,
    *,
    where: str,
    skipped: list[SkippedBlock],
    stamp: Callable[[Step, str], Step] | None = None,
) -> list[Step]:
    """*steps* with each conditional block replaced by its own steps, or dropped (BE-0513).

    A block that names `target` judges that target; otherwise it judges *judge*: the owner for a
    target's own block, every driven target for a scenario's. A dropped block is appended to
    *skipped*, so the report can say what did not run and why. *stamp* fills a kept block's
    `target` into its steps, for a target's own block, which the config never expands; a
    scenario's block had its steps stamped when it loaded.
    """
    names = tuple(judge)
    out: list[Step] = []
    for i, step in enumerate(steps):
        if step.when is None:
            out.append(step)
            continue
        judged = (step.target,) if step.target is not None else names
        if context.holds(step.when, judged, (tuple(tags),)):
            children = step.steps or []
            if stamp is not None and step.target is not None:
                children = [stamp(child, step.target) for child in children]
            out.extend(children)
        else:
            skipped.append(SkippedBlock("setup", f"{where}[{i}]", str(step.when)))
    return out


def decide_cleanup(
    rules: Sequence[CleanupRule],
    judge: Iterable[str],
    tags: Sequence[str],
    context: ConditionContext,
    *,
    where: str,
    skipped: list[SkippedBlock],
) -> list[CleanupRule]:
    """*rules* without the ones whose `when` does not hold; a kept rule keeps its `when`, inert."""
    names = tuple(judge)
    out: list[CleanupRule] = []
    for i, rule in enumerate(rules):
        if rule.when is None or context.holds(rule.when, names, (tuple(tags),)):
            out.append(rule)
        else:
            skipped.append(SkippedBlock("cleanup", f"{where}[{i}] (on: {rule.on})", str(rule.when)))
    return out
