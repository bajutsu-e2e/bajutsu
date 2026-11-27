"""The load checks on a hook condition that need the whole config (BE-0513).

A `when` can name a target that does not exist, and one written under a target's own `hooks` can
leave that target out; both are defects in the file, so both fail when the config loads rather than
skipping silently at run time.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from typing import Any

from bajutsu.common.scenario.models.hook_command import PLATFORM_ROUTES, HookWhen

from .hooks import COMMAND_LISTS, Hooks


def _raw_conditions(hooks: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any], Any]]:
    """Each `when` of a raw `hooks` block, with where it sits and the `target` its block names."""
    for list_name in COMMAND_LISTS:
        for i, entry in enumerate(_items(hooks.get(list_name))):
            if isinstance(entry, Mapping) and isinstance(entry.get("when"), Mapping):
                yield f"hooks.{list_name}[{i}].when", entry["when"], None
    for i, step in enumerate(_items(hooks.get("setup"))):
        if isinstance(step, Mapping) and isinstance(step.get("when"), Mapping):
            yield f"hooks.setup[{i}].when", step["when"], step.get("target")
    for i, rule in enumerate(_items(hooks.get("cleanup"))):
        if isinstance(rule, Mapping) and isinstance(rule.get("when"), Mapping):
            yield f"hooks.cleanup[{i}].when", rule["when"], None


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _never_holds(when: Mapping[str, Any], owner: str, platform: Any) -> str | None:
    """Why *when* can never hold for *owner* on *platform*, or None.

    Reads the raw mapping, so a malformed field is left to the model's own error.
    """
    targets, platforms, routes = (when.get(k) for k in ("targets", "platforms", "routes"))
    if isinstance(targets, list) and targets and owner not in targets:
        return f"`targets` leaves out {owner!r}, the target this condition belongs to"
    if not isinstance(platform, str):
        return None
    if isinstance(platforms, list) and platforms and platform not in platforms:
        return f"`platforms` leaves out {platform!r}, the platform of target {owner!r}"
    # `platform` is the raw config's string, so it is looked up by value rather than by key type.
    reachable = next((r for name, r in PLATFORM_ROUTES.items() if name == platform), None)
    if (
        isinstance(routes, list)
        and routes
        and reachable is not None
        and not set(routes) & set(reachable)
    ):
        on = ", ".join(reachable) or "none"
        return f"`routes` names no route a {platform} target runs on (its routes: {on})"
    return None


def check_own_conditions(owner: str, raw: Any, platform: Any) -> None:
    """Fail a condition written under `targets.<owner>.hooks` that can never hold (BE-0513).

    Reads the target's block as written, before `defaults` or a template merges in: a merged copy
    that never runs is the point of a shared entry, while one the target wrote for itself is a typo.
    A setup block that names another `target` judges that target, so it is left alone.

    Raises:
        ValueError: A condition leaves out its owner, the owner's platform, or every route of it.
    """
    hooks = raw.get("hooks") if isinstance(raw, Mapping) else None
    if not isinstance(hooks, Mapping):
        return
    for where, when, target in _raw_conditions(hooks):
        if target is not None and target != owner:
            continue
        if (why := _never_holds(when, owner, platform)) is not None:
            raise ValueError(f"targets.{owner}.{where}: {why}; it would never run")


def unknown_condition_targets(
    conditions: Iterator[tuple[str, HookWhen]], known: Collection[str]
) -> str | None:
    """Why a condition names a target the config does not declare, or None (BE-0513).

    Shared with the scenario side, whose own conditions the config can only check once both load.
    """
    for where, when in conditions:
        if unknown := [t for t in when.targets if t not in known]:
            names = ", ".join(sorted(known)) or "none"
            return (
                f"{where} names target(s) {', '.join(map(repr, unknown))}, which the config does "
                f"not declare (declared: {names})"
            )
    return None


def check_condition_targets(targets: Mapping[str, Hooks]) -> None:
    """Fail a `when.targets` entry, under any target, that names no target of the config.

    Raises:
        ValueError: Such a name exists; it is a typo, since the condition could never hold.
    """
    for name, hooks in targets.items():
        why = unknown_condition_targets(hooks.conditions(), targets)
        if why is not None:
            raise ValueError(f"targets.{name}.{why}")
