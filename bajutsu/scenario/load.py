"""Parse scenario / component YAML into the validated models."""

from __future__ import annotations

from bajutsu import _yaml
from bajutsu.scenario.models import Component, Scenario, ScenarioFile
from bajutsu.scenario.models.scenario import SCHEMA_VERSION


def _check_schema_version(data: dict[str, object]) -> None:
    """Reject an out-of-range schema before full validation, so the mismatch is what's reported (BE-0119)."""
    # `schema` is the on-disk key; `schema_version` is its populate_by_name spelling. A non-int
    # (or bool) is left for model validation to reject; an absent key means the implicit version 1.
    declared = data.get("schema", data.get("schema_version"))
    if declared is None or not isinstance(declared, int) or isinstance(declared, bool):
        return
    if declared > SCHEMA_VERSION:
        raise ValueError(
            f"scenario file uses schema {declared}, but this bajutsu understands up to "
            f"schema {SCHEMA_VERSION} — upgrade bajutsu or pin an older scenario/config version"
        )
    if declared < 1:
        raise ValueError(f"scenario file declares schema {declared}; the earliest schema is 1")


def load_scenario_file(text: str) -> ScenarioFile:
    """Parse a scenario file: a list of scenarios, or a `{description, scenarios}` mapping.

    Raises:
        ValueError: The top level is neither a list nor a mapping (§6.1), or the file declares a
            schema version newer than this bajutsu supports (BE-0119).
    """
    data = _yaml.safe_load(text)
    if isinstance(data, list):
        return ScenarioFile.model_validate({"scenarios": data})
    if isinstance(data, dict):
        _check_schema_version(data)
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
