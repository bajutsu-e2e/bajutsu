"""Expand the `hooks` components a target or a scenario applies (BE-0512).

An application reads the component file, substitutes its parameters, and wraps the entries written
beside it: a component's preparation runs ahead of them and its teardown behind them, so teardown
runs in the reverse order of preparation. All of it happens while the files load, so the run loop
sees ordinary command entries and steps.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bajutsu.common import _yaml
from bajutsu.common.scenario import interp
from bajutsu.common.scenario.expand import expand_steps
from bajutsu.common.scenario.load_expanded import (
    ComponentResolver,
    contained_ref,
    parse_yaml_named,
)
from bajutsu.common.scenario.models import CleanupRule, Scenario, Step
from bajutsu.common.scenario.models.hook_command import HookCommand
from bajutsu.common.scenario.models.hook_component import HookComponent, HookUse
from bajutsu.common.scenario.step_walk import nested_steps, reject_scenario_only

# The lists a component's preparation goes ahead of the direct entries in; every other list holds
# teardown, which goes behind them in the reverse order of `use`.
_PREPARATION = frozenset({"beforeRun", "beforeScenario"})


def load_hook_component(text: str) -> HookComponent:
    """Parse a YAML string (one component mapping) into a validated `HookComponent`."""
    return HookComponent.model_validate(_yaml.safe_load(text))


@dataclass(frozen=True)
class HookApplication:
    """One `hooks.use` entry with its component read and its parameters substituted."""

    use: HookUse
    # The component file's real path; with `use.as_`, what tells this application from another.
    path: Path
    commands: dict[str, list[HookCommand]]
    setup: list[Step]
    cleanup: list[CleanupRule]

    @property
    def identity(self) -> tuple[Path, str | None]:
        """The resolved component file and the alias."""
        return self.path, self.use.as_


def apply_hook_component(use: HookUse, *, root: Path, base: Path) -> HookApplication:
    """Read the component *use* names and substitute its parameters.

    Args:
        use: The `hooks.use` entry.
        root: The suite root the component file must stay inside (BE-0174).
        base: The directory of the file that holds *use*, which its `hook` resolves against.

    Raises:
        ValueError: The file escapes *root*, cannot be read, or is not a valid component; a
            parameter is missing or unknown; a `${params.*}` token stays unresolved; or a step is
            one that wraps no single scenario (`group`, `setMocks`).
    """
    where = f"hooks.use {use.hook!r}"
    path, component = _read(use, root=root, base=base, where=where)
    bindings = {f"params.{k}": v for k, v in use.with_.items()}
    here = path.parent
    origin = {"component": str(path), "alias": use.as_}
    try:
        commands = {
            name: [_command(entry, bindings, here, origin) for entry in entries]
            for name, entries in component.command_lists.items()
        }
        setup = _steps(component.setup, bindings)
        cleanup = [
            CleanupRule.model_validate(interp.interpolate(_dump(rule), bindings))
            for rule in component.cleanup
        ]
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from e
    dumps = [
        *(_dump(e) for entries in commands.values() for e in entries),
        *(_dump(s) for s in setup),
        *(_dump(r) for r in cleanup),
    ]
    _reject_residual(dumps, component.params, where)
    # A step component the file calls resolves against the file's own directory, as one called from
    # a component file of steps does (BE-0422).
    resolver = ComponentResolver({}, root=root, base=here, source=None)
    try:
        setup = expand_steps(setup, resolver)
        for rule in cleanup:
            rule.steps = expand_steps(rule.steps, resolver)
    except OSError as e:
        raise ValueError(f"{where}: cannot read a step component: {e}") from e
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from e
    reject_scenario_only(
        [*nested_steps(setup), *(s for r in cleanup for s in nested_steps(r.steps))],
        f"{where}: a hooks component",
    )
    if use.as_ is not None:
        # After the nested step components expand, so their tokens are rewritten too. Only the names
        # this component declares move under the alias; a token naming an outer entry stays.
        declared = {e.name for entries in commands.values() for e in entries if e.name is not None}
        setup = [_aliased(s, use.as_, declared) for s in setup]
        for rule in cleanup:
            rule.steps = [_aliased(s, use.as_, declared) for s in rule.steps]
    return HookApplication(use=use, path=path, commands=commands, setup=setup, cleanup=cleanup)


def _read(use: HookUse, *, root: Path, base: Path, where: str) -> tuple[Path, HookComponent]:
    """The component file *use* names, read inside *root*, with its parameters checked."""
    try:
        path = contained_ref(root, base, use.hook)
        component = parse_yaml_named(path, load_hook_component)
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{where}: cannot read the component: {e}") from e
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from e
    if missing := sorted(set(component.params) - set(use.with_)):
        raise ValueError(f"{where}: missing required params: {missing}")
    if unknown := sorted(set(use.with_) - set(component.params)):
        raise ValueError(f"{where}: unknown params: {unknown}")
    return path, component


def _reject_residual(dumps: list[Any], params: list[str], where: str) -> None:
    """Fail on a `${params.*}` token substitution left behind."""
    residual = sorted(t for t in interp.find_tokens(dumps) if t.startswith("params."))
    if unsubstituted := [t for t in residual if t.removeprefix("params.") in params]:
        raise ValueError(
            f"{where}: ${{{unsubstituted[0]}}} sits in a field that takes no parameter; only a "
            "command's `cmd` and `env` values and the steps do"
        )
    if residual:
        raise ValueError(f"{where}: references undeclared params: {residual}")


def _dump(model: Any) -> Any:
    # Only what the author wrote: a dumped default `on` would read as one set on a `before` entry.
    return model.model_dump(by_alias=True, exclude_unset=True)


def _steps(steps: list[Step], bindings: dict[str, str]) -> list[Step]:
    return [Step.model_validate(interp.interpolate(_dump(s), bindings)) for s in steps]


# A two-part `${hooks.<name>.<key>}` token, the form a component's own steps read its names by.
_LOCAL_TOKEN = re.compile(r"\$\{\s*hooks\.([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z][A-Za-z0-9_]*)\s*\}")


def _aliased(step: Step, alias: str, declared: set[str]) -> Step:
    """*step* with each token naming one of *declared* read under *alias* instead."""

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            return _LOCAL_TOKEN.sub(
                lambda m: f"${{hooks.{alias}.{m[1]}.{m[2]}}}" if m[1] in declared else m.group(0),
                value,
            )
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        return value

    dumped = step.model_dump(by_alias=True, exclude_none=True)
    out = rewrite(dumped)
    return step if out == dumped else Step.model_validate(out)


def _command(
    entry: HookCommand, bindings: dict[str, str], here: Path, origin: dict[str, str | None]
) -> HookCommand:
    """*entry* with its parameters substituted, its working directory fixed to *here*, and *origin*."""
    data = _dump(entry)
    data["_origin"] = origin
    if entry.cmd is not None:
        if interp.find_tokens(entry.cmd):
            # Split first, then substitute into each argument, so a value holding a space or a quote
            # stays one argument; the runner splits the joined line back the same way.
            try:
                argv = shlex.split(entry.cmd)
            except ValueError as e:
                raise ValueError(f"`cmd` {entry.cmd!r} does not split into arguments: {e}") from e
            data["cmd"] = shlex.join(str(interp.interpolate(arg, bindings)) for arg in argv)
        # The component and its scripts move together, so its commands run from its directory.
        cwd = Path(entry.cwd) if entry.cwd is not None else Path()
        data["cwd"] = str(cwd if cwd.is_absolute() else here / cwd)
    if entry.env:
        data["env"] = interp.interpolate(entry.env, bindings)
    return HookCommand.model_validate(data)


def reject_repeated(applications: Iterable[HookApplication], where: str) -> None:
    """Fail when two applications share one component file and one alias.

    Raises:
        ValueError: Two of *applications* have the same identity.
    """
    seen: set[tuple[Path, str | None]] = set()
    for app in applications:
        if app.identity in seen:
            alias = f" as {app.use.as_!r}" if app.use.as_ is not None else ""
            raise ValueError(
                f"{where}: {app.use.hook!r}{alias} is applied twice; give one of them an `as` to "
                "tell the two applications apart"
            )
        seen.add(app.identity)


def wrap_commands(
    applications: Sequence[HookApplication], list_name: str, direct: list[HookCommand]
) -> list[HookCommand]:
    """*direct* with the applications' entries of *list_name* wrapped around it."""
    if list_name in _PREPARATION:
        return [*(e for app in applications for e in app.commands[list_name]), *direct]
    return [*direct, *(e for app in reversed(applications) for e in app.commands[list_name])]


def wrap_setup(applications: Sequence[HookApplication], direct: list[Step]) -> list[Step]:
    """The applications' `setup` steps in `use` order, then *direct*."""
    return [*(s for app in applications for s in app.setup), *direct]


def wrap_cleanup(
    applications: Sequence[HookApplication], direct: list[CleanupRule]
) -> list[CleanupRule]:
    """*direct*, then the applications' `cleanup` rules in the reverse of `use` order."""
    return [*direct, *(r for app in reversed(applications) for r in app.cleanup)]


def expand_scenario_hook_uses(scenarios: Iterable[Scenario], *, root: Path, base: Path) -> None:
    """Expand each scenario's `hooks.use` into its own command lists, `setup`, and `cleanup`.

    In place, and before the step components expand, so the scenario-level checks that pass runs
    see the component's steps too. A scenario is not a run, so a component holding `beforeRun` or
    `afterRun` fails here.

    Args:
        scenarios: The scenarios of one file.
        root: The suite root a component file must stay inside.
        base: The scenario file's directory, which each `hook` resolves against.

    Raises:
        ValueError: As `apply_hook_component`, or a component holds a run-level list, or one
            application repeats another.
    """
    for s in scenarios:
        if s.hooks is None or not s.hooks.use:
            continue
        where = f"scenario {s.name!r}"
        try:
            applications = [apply_hook_component(u, root=root, base=base) for u in s.hooks.use]
        except ValueError as e:
            raise ValueError(f"{where}: {e}") from e
        for app in applications:
            for list_name in ("beforeRun", "afterRun"):
                if app.commands[list_name]:
                    raise ValueError(
                        f"{where}: hooks.use {app.use.hook!r} holds `{list_name}`, which a scenario "
                        "cannot apply: a scenario is not a run; apply the component from "
                        "`targets.<name>.hooks.use` instead"
                    )
        reject_repeated(applications, f"{where}: hooks.use")
        hooks = s.hooks
        hooks.before_scenario = wrap_commands(applications, "beforeScenario", hooks.before_scenario)
        hooks.after_scenario = wrap_commands(applications, "afterScenario", hooks.after_scenario)
        hooks.use = []
        seen: set[str] = set()
        for entry in (*hooks.before_scenario, *hooks.after_scenario):
            if (name := entry.qualified_name) is None:
                continue
            if name in seen:
                raise ValueError(
                    f"{where}: two hook entries are named {name!r} once its hooks components "
                    f"expand; a hook name must be unique so ${{hooks.{name}.*}} names one command; "
                    "give one hooks.use application an `as`"
                )
            seen.add(name)
        s.setup = wrap_setup(applications, s.setup)
        s.cleanup = wrap_cleanup(applications, s.cleanup)
