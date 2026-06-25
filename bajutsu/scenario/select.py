"""Tag-based scenario selection (pure metadata filtering)."""

from __future__ import annotations

from bajutsu.scenario.models import Scenario


def select_scenarios(
    scenarios: list[Scenario], include: list[str], exclude: list[str]
) -> list[Scenario]:
    """Filter scenarios by tag, preserving order.

    Pure metadata filtering — never mutates or reorders.

    Args:
        scenarios: The scenarios to filter.
        include: A scenario is kept only if it carries at least one of these tags; an empty
            `include` keeps everything.
        exclude: A scenario carrying any of these tags is dropped. `exclude` wins over `include`.

    Returns:
        The kept scenarios, in their original order.
    """
    inc, exc = set(include), set(exclude)
    out: list[Scenario] = []
    for s in scenarios:
        tags = set(s.tags)
        if exc & tags:
            continue
        if inc and not (inc & tags):
            continue
        out.append(s)
    return out
