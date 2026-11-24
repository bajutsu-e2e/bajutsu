"""Tag-based scenario selection (pure metadata filtering)."""

from __future__ import annotations

from bajutsu.common.scenario.models import Scenario
from bajutsu.common.scenario.tags import tags_match


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
    return [s for s in scenarios if tags_match(s.tags, include, exclude)]
