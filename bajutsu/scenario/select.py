"""Tag-based scenario selection (pure metadata filtering)."""

from __future__ import annotations

from bajutsu.scenario.models import Scenario


def select_scenarios(
    scenarios: list[Scenario], include: list[str], exclude: list[str]
) -> list[Scenario]:
    """Filter scenarios by tag, preserving order. A scenario is kept when it carries at
    least one `include` tag (or `include` is empty) and none of the `exclude` tags;
    `exclude` wins over `include`. Pure metadata filtering — never mutates or reorders."""
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
