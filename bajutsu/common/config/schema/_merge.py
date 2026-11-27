"""Merge the team `defaults` and a target's templates into its raw block, before it is validated."""

from __future__ import annotations

import os
from typing import Any

# The lists that union rather than replace: replacing `evidence.secrets` would let a target that adds
# one secret stop masking every team-wide one, and the same holds for each `redact` rule list.
_UNION_LISTS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("evidence", "secrets"),
        ("evidence", "redact", "labels"),
        ("evidence", "redact", "headers"),
        ("evidence", "redact", "fields"),
        ("evidence", "redact", "unmaskHeaders"),
    }
)

# A `null` here would clear the union, or the block that holds it, and so silently drop every
# team-wide secret or redaction rule; a target may add to them but never wipe them.
_NO_NULL: frozenset[tuple[str, ...]] = _UNION_LISTS | {
    ("evidence",),
    ("evidence", "redact"),
}

# The list whose entries come in three forms (BE-0473), merged form by form by `_merge_interrupts`.
_INTERRUPTS: tuple[str, ...] = ("hooks", "interrupts")

# The `hooks` components each layer applies (BE-0512), joined by `_merge_uses`.
_USES: tuple[str, ...] = ("hooks", "use")

# `ai` keeps its field-by-field merge in `resolve`, and `platforms` is not a target key.
_NOT_MERGED = frozenset({"ai", "platforms"})


def _form(entry: Any) -> str:
    # The key that decides an entry's form, as `Interrupt`'s discriminator reads it; an entry of no
    # known form stays with the screens, where the model then rejects it.
    if isinstance(entry, dict):
        if isinstance(entry.get("systemAlert"), str):
            return "systemAlert"
        if "tipKit" in entry:
            return "tipKit"
    return "screen"


def _merge_interrupts(base: list[Any], over: list[Any]) -> list[Any]:
    """*over*'s `interrupts` laid on *base*'s, one form at a time (BE-0473).

    One list holds three forms, so replacing it whole would let a target that adds one screen handler
    drop every team-wide system-alert answer without a word. Each form keeps the merge it would have
    as a key of its own: the screens replace as a list when *over* writes any, a `systemAlert` entry
    replaces *base*'s for the same prompt, and a `tipKit` entry replaces *base*'s. An empty *over*
    clears the whole list.
    """
    if not over:
        # An empty list still clears what was inherited, as it did when the list held screens alone.
        return []
    screens = [e for e in over if _form(e) == "screen"] or [e for e in base if _form(e) == "screen"]
    # Each layer's own entries pass through untouched, so a prompt one layer names twice still
    # reaches the model's duplicate check rather than being collapsed here.
    over_alerts = [e for e in over if _form(e) == "systemAlert"]
    named = {e["systemAlert"] for e in over_alerts}
    alerts = [e for e in base if _form(e) == "systemAlert" and e["systemAlert"] not in named]
    tip_kit = [e for e in over if _form(e) == "tipKit"] or [e for e in base if _form(e) == "tipKit"]
    return [*screens, *alerts, *over_alerts, *tip_kit]


def _use_identity(entry: Any) -> object:
    # The component file and the alias; every layer's `hook` is relative to the config file's
    # directory by now (a template file's is rebased), so a normalized path compares across layers.
    # An entry of no readable shape keeps an identity of its own and reaches the model's error.
    if isinstance(entry, dict) and isinstance(hook := entry.get("hook"), str):
        return os.path.normpath(hook), entry.get("as")
    return id(entry)


def _merge_uses(base: list[Any], over: list[Any]) -> list[Any]:
    """*over*'s `hooks.use` joined onto *base*'s (BE-0512).

    A replaced list would drop a component that `defaults` or a template applies without a word, so
    the lists join in layer order. An entry naming the same component and alias as one of *base*'s
    replaces it in place, which lets a later layer change an application's values. Only *base* is
    matched, so two such entries in one layer both stay and reach the model's "applied twice" error.
    A `null` still clears the whole list (`merge_layer`): the one deliberate way to drop what an
    earlier layer applies.
    """
    out = list(base)
    # Each of *base*'s slots is replaced at most once per layer: a second equal entry in *over* then
    # appends instead of overwriting the first, so the repeat still reaches the model.
    replaced: set[int] = set()
    for entry in over:
        key = _use_identity(entry)
        at = next(
            (i for i, e in enumerate(base) if i not in replaced and _use_identity(e) == key), None
        )
        if at is None:
            out.append(entry)
        else:
            out[at] = entry
            replaced.add(at)
    return out


def merge_layer(
    base: dict[str, Any], over: dict[str, Any], path: tuple[str, ...] = ()
) -> dict[str, Any]:
    """*over* laid on *base*: dictionaries merge key by key, *over* wins, and None clears a key.

    Lists replace, except the ones `_UNION_LISTS` names, which keep both sides in order without a
    repeat, `hooks.interrupts`, which merges form by form (`_merge_interrupts`), and `hooks.use`,
    which joins (`_merge_uses`). Neither argument
    is changed.

    Raises:
        ValueError: *over* writes `null` on a union list or a block holding one (`_NO_NULL`).
    """
    out = dict(base)
    for key, value in over.items():
        here = (*path, key)
        current = out.get(key)
        if value is None:
            if here in _NO_NULL:
                raise ValueError(
                    f"{'.'.join(here)}: null cannot clear it, since that would drop the team-wide "
                    "secrets or redaction rules; a target adds to these lists, never replaces them"
                )
            out.pop(key, None)
        elif isinstance(current, dict) and isinstance(value, dict):
            out[key] = merge_layer(current, value, here)
        elif here in _UNION_LISTS and isinstance(current, list) and isinstance(value, list):
            out[key] = list(dict.fromkeys([*current, *value]))
        elif here == _INTERRUPTS and isinstance(current, list) and isinstance(value, list):
            out[key] = _merge_interrupts(current, value)
        elif here == _USES and isinstance(current, list) and isinstance(value, list):
            out[key] = _merge_uses(current, value)
        else:
            out[key] = value
    return out


def merge_defaults(defaults: dict[str, Any] | None, *layers: dict[str, Any]) -> dict[str, Any]:
    """The team *defaults* with each of *layers* laid on in turn, the last one strongest.

    The shared groups sit directly under `defaults`; the platform-shaped ones sit under
    `defaults.platforms.<platform>` and apply to that platform's targets alone, so one file can hold
    defaults for several platforms. The platform is the one the layers declare, so a target may take
    it from a template it extends (BE-0471); the expansion has already refused layers that disagree.
    With no `platform` anywhere the result gets the shared groups only, and then fails validation for
    the missing key.

    The layers apply one at a time rather than merged into one block first: a template's `null` must
    find the value `defaults` supplies to clear it, and a block flattened beforehand would have
    dropped the `null`.
    """
    defaults = defaults or {}
    shared = {k: v for k, v in defaults.items() if k not in _NOT_MERGED}
    platforms = defaults.get("platforms")
    platform = next(
        (p for layer in layers if isinstance(p := layer.get("platform"), str)),
        None,
    )
    by_platform: Any = (
        platforms.get(platform) if isinstance(platforms, dict) and platform is not None else None
    )
    out = merge_layer(shared, by_platform) if isinstance(by_platform, dict) else shared
    for layer in layers:
        out = merge_layer(out, layer)
    return out
