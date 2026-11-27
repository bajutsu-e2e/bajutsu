"""One scenario's own hook commands, nested inside its targets' (BE-0501).

A scenario's lists run as one more entry of the phases `run_phase` walks, under the reserved key
`SCENARIO_LEVEL` and always last. That position is what nests the two levels: a `before` list
reaches the scenario's entries after every target's and stops ahead of them when a target failed,
and an `after` list walks the same mapping in reverse, so the scenario tears down first.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from bajutsu.common.capability.command_gate import CommandGate
from bajutsu.common.config import Effective, HookPhases
from bajutsu.common.runner.hook_commands import hook_refusal, unknown_hook_tokens
from bajutsu.common.runner.hook_session import phases_of
from bajutsu.common.scenario import Scenario
from bajutsu.common.scenario.hook_scopes import scope_scenario_tokens
from bajutsu.common.scenario.interp import find_tokens
from bajutsu.common.scenario.models.hook_command import SCENARIO_LEVEL, env_name, split_hook_token


def driven_targets(s: Scenario, primary: str) -> list[str]:
    """The names of the targets *s* drives: those it declares, else the run's own."""
    return list(s.target_names) or [primary]


def own_phases(s: Scenario) -> HookPhases | None:
    """*s*'s own two lists in the shape a target's take, or None when it declares no command."""
    if s.hooks is None:
        return None
    phases = HookPhases(
        before_scenario=list(s.hooks.before_scenario), after_scenario=list(s.hooks.after_scenario)
    )
    return phases if phases.any_command else None


def sandbox_targets(scenarios: Iterable[Scenario], primary: str) -> set[str]:
    """The targets whose container runs some scenario's own commands under `sandbox`.

    It is each scenario's first declared target. That target needs its container even when it
    declares no command of its own.
    """
    return {driven_targets(s, primary)[0] for s in scenarios if own_phases(s) is not None}


def component_targets(scenarios: Iterable[Scenario], primary: str) -> set[str]:
    """The targets of `sandbox_targets` whose container runs a command a `hooks` component added.

    Such a command answers to the stricter of the scenario's source and the component file's
    (BE-0512), so its container may be needed where the scenario's own source sandboxes nothing.
    """
    return {
        driven_targets(s, primary)[0]
        for s in scenarios
        if (own := own_phases(s)) is not None and any(e.origin for _, _, e in own.entries())
    }


def nested(phases: Mapping[str, HookPhases], own: HookPhases | None) -> dict[str, HookPhases]:
    """The targets' *phases* with the scenario's own level innermost."""
    return {**phases, SCENARIO_LEVEL: own} if own is not None else dict(phases)


def scenario_hook_refusal(
    scenarios: Iterable[Scenario], effs: Mapping[str, Effective], primary: str, gate: CommandGate
) -> str | None:
    """Why *gate*'s policy will not run one of these scenarios' own commands, or None.

    The target-level `hook_refusal` applied to the scenario level, before any device work. A
    sandboxed command runs in the container of the scenario's first declared target.
    """
    for s in scenarios:
        own = own_phases(s)
        if own is None:
            continue
        target = driven_targets(s, primary)[0]
        eff = effs.get(target)
        phases = HookPhases(
            before_scenario=own.before_scenario,
            after_scenario=own.after_scenario,
            sandbox=eff.hooks.sandbox if eff is not None else None,
        )
        if (
            why := hook_refusal({target: phases}, gate.for_scenario(), prefix="scenario.")
        ) is not None:
            return f"scenario {s.name!r}: {why}"
    return None


def run_hook_refusal(
    scenarios: Iterable[Scenario], effs: Mapping[str, Effective], primary: str, gate: CommandGate
) -> str | None:
    """Why *gate*'s policy will not run one of this run's hook commands, at either level, or None."""
    return hook_refusal(phases_of(effs), gate) or scenario_hook_refusal(
        scenarios, effs, primary, gate
    )


def component_scopes(
    s: Scenario, effs: Mapping[str, Effective], primary: str
) -> dict[str, set[str]]:
    """The names the driven targets' `hooks` components bring, each with the targets holding it."""
    out: dict[str, set[str]] = {}
    for target in driven_targets(s, primary):
        if (eff := effs.get(target)) is None:
            continue
        for name, entry in eff.hooks.named().items():
            if entry.origin is not None:
                out.setdefault(name, set()).add(target)
    return out


def scope_hook_tokens(s: Scenario, effs: Mapping[str, Effective], primary: str) -> None:
    """Rewrite *s*'s tokens that read a target's component names to that target's scope (BE-0512).

    Raises:
        ValueError: A step or assertion reads a component name its own target does not hold.
    """
    scope_scenario_tokens(s, driven_targets(s, primary)[0], component_scopes(s, effs, primary))


def _component_name_error(s: Scenario, effs: Mapping[str, Effective], primary: str) -> str | None:
    """Why *s*'s view of the driven targets' component names is ambiguous, or None (BE-0512)."""
    scopes = component_scopes(s, effs, primary)
    own = own_phases(s)
    if own is not None:
        # A scenario's own command receives the component outputs of every target it drives, so a
        # name two of them hold would give it one value without saying whose.
        for name, holders in sorted(scopes.items()):
            if len(holders) > 1:
                both = " and ".join(repr(t) for t in sorted(holders))
                return (
                    f"hooks: targets {both} both apply a hooks component naming {name!r}, which "
                    "this scenario's own commands would both receive; give one application an `as`"
                )
    seen = {
        name
        for target in driven_targets(s, primary)
        if (eff := effs.get(target)) is not None
        for name in eff.hooks.named()
    } | (set(own.named()) if own is not None else set())
    entries = [
        e
        for target in driven_targets(s, primary)
        if (eff := effs.get(target)) is not None
        for _, _, e in eff.hooks.entries()
    ] + ([e for _, _, e in own.entries()] if own is not None else [])
    aliases = {e.origin.alias for e in entries if e.origin is not None and e.origin.alias}
    if clash := sorted(aliases & seen):
        return (
            f"hooks: the alias {clash[0]!r} is also an entry name this scenario sees; an alias "
            "must differ from every hook name, so a token names one of the two"
        )
    try:
        scope_hook_tokens(s.model_copy(deep=True), effs, primary)
    except ValueError as e:
        return str(e)
    return None


def scenario_hook_error(s: Scenario, effs: Mapping[str, Effective], primary: str) -> str | None:
    """Why *s*'s hook names or `${hooks.<name>.<key>}` tokens do not resolve, or None.

    A scenario sees its own entries and those of the targets it drives; one that sets
    `preconditions.inheritSetup: false` does not see the targets' scenario lists. A name must be
    unique among the entries it sees, and a token must name one whose command runs before the steps.
    """
    own = own_phases(s)
    own_names = own.named() if own is not None else {}
    readable: set[str] = set()
    skipped: set[str] = set()
    for target in driven_targets(s, primary):
        eff = effs.get(target)
        if eff is None:
            continue
        hooks = eff.hooks
        before_scenario = {n for e in hooks.before_scenario if (n := e.qualified_name) is not None}
        visible = {
            n
            for name, _, e in hooks.entries()
            if (n := e.qualified_name) is not None
            and (s.preconditions.inherit_setup or not name.endswith("Scenario"))
        }
        if clash := sorted(visible & own_names.keys()):
            return (
                f"hooks: an entry named {clash[0]!r} is also declared by target {target!r}; a hook "
                f"name must be unique so ${{hooks.{clash[0]}.*}} names one command"
            )
        readable |= {n for e in hooks.before_run if (n := e.qualified_name) is not None}
        if s.preconditions.inherit_setup:
            readable |= before_scenario
        else:
            skipped |= before_scenario
    if own is not None:
        # A command reads earlier outputs from its environment; a token there would reach it literally.
        assert s.hooks is not None
        if found := sorted(t for t in find_tokens(s.hooks.model_dump()) if t.startswith("hooks.")):
            name, key = split_hook_token(found[0])
            return (
                f"hooks: ${{{found[0]}}} is not substituted in a command; read the variable "
                f"BAJUTSU_HOOK__{env_name(name)}__{key or '<key>'} instead"
            )
        readable |= {n for e in own.before_scenario if (n := e.qualified_name) is not None}
    return unknown_hook_tokens(
        find_tokens(s.model_dump(by_alias=True)), readable, skipped=skipped - readable
    ) or _component_name_error(s, effs, primary)
