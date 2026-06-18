"""Parse scenario / component YAML into the validated models."""

from __future__ import annotations

from bajutsu import _yaml
from bajutsu.scenario.models import Component, Scenario, ScenarioFile


def load_scenario_file(text: str) -> ScenarioFile:
    """Parse a scenario file (a list of scenarios, or a `{description, scenarios}` mapping)."""
    data = _yaml.safe_load(text)
    if isinstance(data, list):
        return ScenarioFile.model_validate({"scenarios": data})
    if isinstance(data, dict):
        return ScenarioFile.model_validate(data)
    raise ValueError(
        "scenario file must be a list of scenarios or a {description, scenarios} mapping (§6.1)"
    )


def load_scenarios(text: str) -> list[Scenario]:
    """Parse a scenario file into validated Scenario objects (any file-level description dropped)."""
    return load_scenario_file(text).scenarios


def load_component(text: str) -> Component:
    """Parse a YAML string (a single component mapping) into a validated Component."""
    return Component.model_validate(_yaml.safe_load(text))
