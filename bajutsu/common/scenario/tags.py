"""The tag rule selection and a hook's `when` share (BE-0034, BE-0497).

A leaf module, so the scenario models can read it: `select` imports them.
"""

from __future__ import annotations

from collections.abc import Iterable


def tags_match(tags: Iterable[str], include: Iterable[str], exclude: Iterable[str]) -> bool:
    """Whether `tags` satisfies the tag rule that `--tag` and `--exclude` apply.

    Args:
        tags: The tags a scenario carries.
        include: At least one must be among `tags`; empty places no constraint.
        exclude: None may be among `tags`. `exclude` wins over `include`.
    """
    carried, wanted = set(tags), set(include)
    if carried & set(exclude):
        return False
    return not wanted or bool(carried & wanted)
