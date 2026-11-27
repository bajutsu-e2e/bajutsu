"""Scope `${hooks.*}` tokens that read a target's `hooks` component to that target (BE-0512).

Two targets may apply one component, so a name like `user` can belong to both. A token resolves by
the target of the step or assertion that reads it: the load rewrites it to the target-scoped key
`hooks.<target>:<name>.<key>`, under which the run stores that target's values. The run loop then
substitutes through the one binding map it already uses, unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any

from pydantic import BaseModel

from bajutsu.common.scenario.models import Scenario, Step
from bajutsu.common.scenario.models.hook_command import SCOPE_SEP, split_hook_token

_TOKEN = re.compile(r"\$\{\s*(hooks\.[^}\s]+)\s*\}")

# Which targets hold each component-scoped name, keyed by the name a token reads (`<as>.<name>`).
Scoped = Mapping[str, Collection[str]]


def _scope(value: Any, target: str | None, scoped: Scoped) -> Any:
    """*value* with each token naming a scoped name rewritten for the target that reads it.

    Models are rewritten in place rather than re-validated, so the load-time routing a step keeps in
    private state survives. A step reads under the target it resolved to, another model that names
    a `target` (an assertion, an interrupt entry) under that one, and anything else under its
    enclosing target. None means no single target reads the value.

    Raises:
        ValueError: A token names a component name its reading target does not hold, or one that
            no single target reads.
    """
    if isinstance(value, str):
        return _TOKEN.sub(lambda m: _scoped_token(m[1], target, scoped), value)
    if isinstance(value, list):
        return [_scope(v, target, scoped) for v in value]
    if isinstance(value, dict):
        return {k: _scope(v, target, scoped) for k, v in value.items()}
    if isinstance(value, BaseModel):
        own = value.resolved_target if isinstance(value, Step) else getattr(value, "target", None)
        here = own if isinstance(own, str) else target
        for name in type(value).model_fields:
            current = getattr(value, name)
            new = _scope(current, here, scoped)
            if new is not current and new != current:
                # No validator to re-run: only the text inside a token changed.
                object.__setattr__(value, name, new)
        return value
    return value


def _scoped_token(token: str, target: str | None, scoped: Scoped) -> str:
    name, key = split_hook_token(token)
    if SCOPE_SEP in name:
        # Already scoped, by an earlier pass over the same part: kept only when it names the reading
        # target and a name that target's component holds. A hand-written scope would otherwise pass
        # the load and reach the step as literal text.
        prefix, _, rest = name.partition(SCOPE_SEP)
        if prefix == target and target in scoped.get(rest, ()):
            return f"${{{token}}}"
        raise ValueError(
            f"${{{token}}} names a target scope Bajutsu writes itself; write "
            f"${{hooks.{rest}.{key}}} and the load reads it under the step's own target"
        )
    holders = scoped.get(name)
    if holders is None:
        return f"${{{token}}}"
    owners = ", ".join(repr(t) for t in sorted(holders))
    if target is None:
        raise ValueError(
            f"${{{token}}} reads {name!r}, a hooks component name of target {owners}, where no single "
            "target reads it; name the `target` of the interrupt entry that holds it"
        )
    if target not in holders:
        raise ValueError(
            f"${{{token}}} is read on target {target!r}, but {name!r} comes from a hooks component "
            f"of target {owners}; a step reads the component names of its own target alone"
        )
    return f"${{hooks.{target}{SCOPE_SEP}{name}.{key}}}"


def scope_tokens(value: Any, target: str, scoped: Scoped) -> None:
    """Rewrite the scoped tokens of *value* (models, or lists of them) in place under *target*.

    Raises:
        ValueError: As `_scope`.
    """
    _scope(value, target, scoped)


def scope_scenario_tokens(s: Scenario, target: str, scoped: Scoped) -> None:
    """Rewrite *s*'s scoped tokens in place; a part naming no target reads under *target*.

    An `expect` entry naming no target is checked on the primary the steps end on, and an interrupt
    entry naming none can fire on any target of a multi-target scenario, so one reads under that
    primary and the other under no single target.

    Args:
        s: The expanded scenario.
        target: The target a step that names none runs against.
        scoped: The component-scoped names of the targets *s* drives, with their holders.

    Raises:
        ValueError: A token names a component name its reading target does not hold.
    """
    if not scoped:
        return
    _scope([s.setup, s.steps, s.cleanup], target, scoped)
    _scope(s.expect, s.final_primary or target, scoped)
    _scope(s.screens, target if len(s.target_names) <= 1 else None, scoped)
