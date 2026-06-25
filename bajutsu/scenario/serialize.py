"""Serialize scenarios back to YAML / JSON (round-trips through load.py)."""

from __future__ import annotations

from typing import Any, cast

from bajutsu import _yaml
from bajutsu.scenario.models import Mock, Scenario


def _prune(obj: Any) -> Any:
    """Drop None / empty-list / empty-dict entries for readable output."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            pruned = _prune(value)
            if pruned is None or pruned == [] or pruned == {}:
                continue
            out[key] = pruned
        return out
    if isinstance(obj, list):
        return [_prune(v) for v in obj]
    return obj


def scenario_dict(scenario: Scenario) -> dict[str, Any]:
    """A pruned, alias-keyed dict of one scenario (for the rich report view)."""
    return cast(
        "dict[str, Any]",
        _prune(scenario.model_dump(mode="json", by_alias=True, exclude_none=True)),
    )


def dump_scenarios(scenarios: list[Scenario]) -> str:
    """Serialize scenarios back to YAML (round-trips through load_scenarios)."""
    return _yaml.safe_dump([scenario_dict(s) for s in scenarios])


def dump_scenario_file(scenarios: list[Scenario], description: str | None = None) -> str:
    """Serialize a scenario file.

    With a file-level `description`, emits the `{description, scenarios}` mapping form; otherwise the
    bare list (round-trips through `load_scenario_file`).
    """
    body = [scenario_dict(s) for s in scenarios]
    if description:
        return _yaml.safe_dump({"description": description, "scenarios": body})
    return _yaml.safe_dump(body)


def dump_mocks(mocks: list[Mock]) -> str:
    """Serialize a scenario's mocks to the compact JSON BajutsuKit reads from `BAJUTSU_MOCKS`.

    Alias keys, omitting unset fields.
    """
    import json

    return json.dumps([m.model_dump(by_alias=True, exclude_none=True) for m in mocks])
