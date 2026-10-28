"""Merge the team `defaults` into one target's raw block, before the target is validated (BE-0452)."""

from __future__ import annotations

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

# `ai` keeps its field-by-field merge in `resolve`, and `platforms` is not a target key.
_NOT_MERGED = frozenset({"ai", "platforms"})


def merge_layer(
    base: dict[str, Any], over: dict[str, Any], path: tuple[str, ...] = ()
) -> dict[str, Any]:
    """*over* laid on *base*: dictionaries merge key by key, *over* wins, and None clears a key.

    Lists replace, except the ones `_UNION_LISTS` names, which keep both sides in order without a
    repeat. Neither argument is changed.

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
        else:
            out[key] = value
    return out


def merge_defaults(defaults: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """*target* with the team *defaults* merged under it.

    The shared groups sit directly under `defaults`; the platform-shaped ones sit under
    `defaults.platforms.<platform>` and apply to that platform's targets alone, so one file can hold
    defaults for several platforms. A target with no `platform` gets the shared groups only, and
    then fails validation for the missing key.
    """
    shared = {k: v for k, v in defaults.items() if k not in _NOT_MERGED}
    platforms = defaults.get("platforms")
    platform = target.get("platform")
    by_platform: Any = (
        platforms.get(platform)
        if isinstance(platforms, dict) and isinstance(platform, str)
        else None
    )
    base = merge_layer(shared, by_platform) if isinstance(by_platform, dict) else shared
    return merge_layer(base, target)
